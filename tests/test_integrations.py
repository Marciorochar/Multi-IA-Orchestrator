from multi_ia.cli import main
from multi_ia.store import RunStore


def test_fake_cli_completes_and_persists(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"

    code = main([
        "start",
        "--run-id", "run-1",
        "--db", str(db),
        "--goal", "demo",
        "--repo", str(tmp_path),
        "--max-cycles", "1",
    ])

    assert code == 0
    checkpoint = RunStore(db).load_checkpoint("run-1")
    assert checkpoint["state"] == "completed"

    assert main(["status", "--run-id", "run-1", "--db", str(db)]) == 0
    assert '"state": "completed"' in capsys.readouterr().out


def test_stop_request_persists_and_blocks_next_stage(tmp_path):
    db = tmp_path / "runs.sqlite"

    assert main(["stop", "--run-id", "run-2", "--db", str(db)]) == 0
    store = RunStore(db)
    assert store.stop_requested("run-2") is True

    code = main([
        "start",
        "--run-id", "run-2",
        "--db", str(db),
        "--goal", "demo",
        "--repo", str(tmp_path),
        "--max-cycles", "1",
    ])

    assert code == 2
    assert RunStore(db).load_checkpoint("run-2")["state"] == "cancelled"


def test_resume_refuses_uncertain_write(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    store.save_checkpoint(
        "run-3",
        {"cycle_id": 1, "state": "executing", "write_status": "unconfirmed"},
    )
    store.close()

    code = main([
        "resume",
        "--run-id", "run-3",
        "--db", str(db),
        "--goal", "demo",
        "--repo", str(tmp_path),
    ])

    assert code == 3
