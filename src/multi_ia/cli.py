from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing
from collections.abc import Sequence

from multi_ia.engine import OrchestratorState
from multi_ia.experimental import run_experimental
from multi_ia.service import ExecutionService
from multi_ia.policies import ExecutionPolicy
from multi_ia.store import RunStore
from multi_ia.security import redact
from multi_ia.paths import default_db_path, prepare_db_path


def _execute(args: argparse.Namespace, *, resume: bool = False) -> int:
    try:
        with closing(RunStore(prepare_db_path(args.db))) as store:
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
        print(json.dumps(redact(payload), sort_keys=True))
        if outcome.state is OrchestratorState.COMPLETED:
            return 0
        if outcome.reason == "run_not_found":
            return 1
        if resume or outcome.reason in {"run_already_exists", "run_busy"}:
            return 3
        return 2
    except (sqlite3.Error, OSError):
        return _failure("persistence_failure")
    except ValueError:
        return _failure("invalid_run_configuration")
    except Exception:
        return _failure("internal_failure")


def _failure(reason: str) -> int:
    print(json.dumps({"state": "failed", "reason": reason}, sort_keys=True))
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="multi-ia")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_run_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--run-id", required=True)
        command.add_argument("--db", default=str(default_db_path()))
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
    status.add_argument("--db", default=str(default_db_path()))

    stop = subparsers.add_parser("stop")
    stop.add_argument("--run-id", required=True)
    stop.add_argument("--db", default=str(default_db_path()))

    experimental = subparsers.add_parser(
        "experimental", help="real tools, read-only, operated from the terminal",
    )
    experimental.add_argument("--goal", required=True)
    experimental.add_argument("--repo", required=True)
    experimental.add_argument("--max-cycles", type=int, default=3)
    experimental.add_argument(
        "--max-seconds", type=float, default=900,
        help="agent time limit; time waiting for your replies is not counted",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "start":
        return _execute(args)
    if args.command == "resume":
        return _execute(args, resume=True)
    if args.command == "experimental":
        return run_experimental(
            args.goal, args.repo,
            max_cycles=args.max_cycles, max_seconds=args.max_seconds,
        )

    try:
        with closing(RunStore(prepare_db_path(args.db))) as store:
            if args.command == "stop":
                if store.load_checkpoint(args.run_id) is None:
                    # Recording it would silently cancel a later run with this ID.
                    payload, code = {
                        "run_id": args.run_id, "state": "unknown",
                        "reason": "run_not_found",
                    }, 1
                else:
                    store.request_stop(args.run_id)
                    payload, code = {"run_id": args.run_id, "state": "stop_requested"}, 0
            else:
                checkpoint = store.load_checkpoint(args.run_id)
                if checkpoint is None:
                    payload, code = {
                        "run_id": args.run_id,
                        "state": "unknown",
                        "stop_requested": store.stop_requested(args.run_id),
                    }, 1
                else:
                    payload = dict(checkpoint)
                    workspace_status = store.workspace_status(args.run_id)
                    if workspace_status is not None:
                        payload["workspace_status"] = workspace_status
                    if workspace_status == "pending" and payload.get("state") == "completed":
                        # The engine checkpoint alone is not a final service outcome.
                        payload["state"] = "waiting"
                        payload["reason"] = "workspace_finalization_pending"
                    elif payload.get("state") == "completed" and not store.has_completed_audit(
                        args.run_id, payload.get("cycle_id")
                    ):
                        payload["state"] = "waiting"
                        payload["reason"] = "audit_evidence_missing"
                    code = 0
        print(json.dumps(redact(payload), sort_keys=True))
        return code
    except (sqlite3.Error, OSError):
        return _failure("persistence_failure")
    except Exception:
        return _failure("internal_failure")


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
