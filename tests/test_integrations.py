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

    store = RunStore(db)
    store.request_stop("run-2")
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


def test_cli_stop_records_request_for_existing_run(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    store.register_run("active", "digest")

    assert main(["stop", "--run-id", "active", "--db", str(db)]) == 0
    assert store.stop_requested("active") is True
    assert '"state": "stop_requested"' in capsys.readouterr().out
    store.close()


def test_cli_stop_rejects_unknown_run_without_poisoning_it(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"

    assert main(["stop", "--run-id", "typo", "--db", str(db)]) == 1
    assert '"reason": "run_not_found"' in capsys.readouterr().out
    store = RunStore(db)
    assert store.stop_requested("typo") is False
    store.close()

    code = main([
        "start", "--run-id", "typo", "--db", str(db),
        "--goal", "demo", "--repo", str(tmp_path), "--max-cycles", "1",
    ])

    assert code == 0


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
