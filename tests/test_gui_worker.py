from multi_ia.gui.worker import OrchestratorWorker


def test_worker_emits_fake_stage_events_and_finishes(tmp_path):
    events = []
    finished = []
    failures = []
    worker = OrchestratorWorker(
        run_id="gui-run-1",
        goal="Testar GUI",
        repo_path=str(tmp_path),
        db_path=tmp_path / "runs.sqlite",
        max_cycles=1,
    )
    worker.event.connect(events.append)
    worker.finished.connect(finished.append)
    worker.failed.connect(failures.append)

    worker.run()

    assert failures == []
    assert [
        event["role"]
        for event in events
        if event.get("type") == "stage_started"
    ] == ["planner", "executor", "auditor"]
    assert finished == [
        {
            "run_id": "gui-run-1",
            "state": "completed",
            "reason": None,
            "mode": "fake",
        }
    ]


def test_worker_stop_before_run_finishes_cancelled(tmp_path):
    finished = []
    worker = OrchestratorWorker(
        run_id="gui-run-stop",
        goal="Parar",
        repo_path=str(tmp_path),
        db_path=tmp_path / "runs.sqlite",
        max_cycles=1,
    )
    worker.finished.connect(finished.append)

    worker.request_stop()
    worker.run()

    assert finished[0]["state"] == "cancelled"
    assert finished[0]["reason"] == "stop_requested"


def test_worker_toggle_pause_emits_pause_state(tmp_path):
    events = []
    worker = OrchestratorWorker(
        run_id="gui-run-pause",
        goal="Pausar",
        repo_path=str(tmp_path),
        db_path=tmp_path / "runs.sqlite",
        max_cycles=1,
    )
    worker.event.connect(events.append)

    worker.toggle_pause()
    worker.toggle_pause()

    pause_events = [event for event in events if event.get("type") == "pause_changed"]
    assert pause_events == [
        {"type": "pause_changed", "paused": True},
        {"type": "pause_changed", "paused": False},
    ]
