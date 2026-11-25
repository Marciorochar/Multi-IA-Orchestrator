from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from multi_ia.engine import OrchestratorState
from multi_ia.service import ExecutionService
from multi_ia.policies import ExecutionPolicy
from multi_ia.store import RunStore


def _execute(args: argparse.Namespace, *, resume: bool = False) -> int:
    store = RunStore(args.db)
    try:
        policy = ExecutionPolicy(
            max_cycles=args.max_cycles, max_seconds=args.max_seconds,
            max_retries=args.max_retries, max_budget=args.max_budget,
        )
        service = ExecutionService(
            store, goal=args.goal, repo=args.repo, policy=policy,
            supervised=args.supervised,
        )
        outcome = asyncio.run(service.resume(args.run_id) if resume else service.start(args.run_id))
        payload = {
            "run_id": args.run_id, "state": outcome.state.value,
            "reason": outcome.reason, "mode": "fake",
        }
        print(json.dumps(payload, sort_keys=True))
        if outcome.state is OrchestratorState.COMPLETED:
            return 0
        if outcome.reason == "run_not_found":
            return 1
        if resume or outcome.reason in {"run_already_exists", "run_busy"}:
            return 3
        return 2
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multi-ia")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_run_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--run-id", required=True)
        command.add_argument("--db", default="multi_ia.sqlite")
        command.add_argument("--goal", required=True)
        command.add_argument("--repo", default=".")
        command.add_argument("--supervised", action="store_true")
        command.add_argument("--max-cycles", type=int, default=3)
        command.add_argument("--max-seconds", type=float, default=300)
        command.add_argument("--max-retries", type=int, default=0)
        command.add_argument("--max-budget", type=float)

    add_run_args(subparsers.add_parser("start"))
    add_run_args(subparsers.add_parser("resume"))

    status = subparsers.add_parser("status")
    status.add_argument("--run-id", required=True)
    status.add_argument("--db", default="multi_ia.sqlite")

    stop = subparsers.add_parser("stop")
    stop.add_argument("--run-id", required=True)
    stop.add_argument("--db", default="multi_ia.sqlite")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "start":
        return _execute(args)
    if args.command == "resume":
        return _execute(args, resume=True)

    store = RunStore(args.db)
    try:
        if args.command == "stop":
            store.request_stop(args.run_id)
            print(json.dumps({"run_id": args.run_id, "state": "stop_requested"}))
            return 0

        checkpoint = store.load_checkpoint(args.run_id)
        if checkpoint is None:
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "state": "unknown",
                        "stop_requested": store.stop_requested(args.run_id),
                    },
                    sort_keys=True,
                )
            )
            return 1
        print(json.dumps(checkpoint, sort_keys=True))
        return 0
    finally:
        store.close()


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
