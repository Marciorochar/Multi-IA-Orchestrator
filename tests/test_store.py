from multi_ia.store import RunStore


def test_events_preserve_order(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    store.append_event("run-1", {"type": "first"})
    store.append_event("run-1", {"type": "second"})

    assert [event["type"] for event in store.list_events("run-1")] == ["first", "second"]


def test_restart_loads_last_consistent_checkpoint(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    store.save_checkpoint("run-1", {"cycle_id": 1, "state": "planning"})
    store.save_checkpoint("run-1", {"cycle_id": 1, "state": "auditing"})
    store.close()

    restarted = RunStore(db)
    assert restarted.load_checkpoint("run-1") == {"cycle_id": 1, "state": "auditing"}


def test_restart_does_not_repeat_unconfirmed_write(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    store.save_checkpoint(
        "run-1",
        {"cycle_id": 1, "state": "executing", "write_status": "unconfirmed"},
    )
    store.close()

    restarted = RunStore(db)
    checkpoint = restarted.load_checkpoint("run-1")

    assert checkpoint["write_status"] == "needs_reconciliation"
    assert restarted.can_replay_write(checkpoint) is False


def test_secrets_are_redacted(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    store.append_event(
        "run-1",
        {
            "token": "abc",
            "nested": {"password": "secret", "message": "safe"},
        },
    )

    event = store.list_events("run-1")[0]
    assert event["token"] == "<redacted>"
    assert event["nested"]["password"] == "<redacted>"
    assert event["nested"]["message"] == "safe"



def test_engine_persists_terminal_checkpoint(tmp_path):
    import asyncio

    from multi_ia.contracts import AgentResult
    from multi_ia.engine import Orchestrator

    class StaticAdapter:
        def __init__(self, result):
            self.result = result

        async def run(self, request):
            return self.result

    store = RunStore(tmp_path / "runs.sqlite")
    ok = AgentResult(status="completed", payload={}, evidence=["ok"])
    audit = AgentResult(status="completed", payload={"approved": True}, evidence=["ok"])
    orchestrator = Orchestrator(
        StaticAdapter(ok),
        StaticAdapter(ok),
        StaticAdapter(audit),
        store=store,
    )

    asyncio.run(orchestrator.run_cycle("run-1", 1))

    checkpoint = store.load_checkpoint("run-1")
    assert checkpoint["cycle_id"] == 1
    assert checkpoint["state"] == "completed"
