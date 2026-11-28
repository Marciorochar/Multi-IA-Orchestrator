"""Double-click assistant for the experimental read-only mode."""
import io
import subprocess
import sys

import pytest

from multi_ia.launcher import run_launcher


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(path, *, commit=True):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "test")
    if commit:
        (path / "sub").mkdir()
        (path / "sub" / "app.py").write_text("print(1)\n", encoding="utf-8")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "project")


class Runner:
    def __init__(self, code=7, error=None):
        self.calls, self.code, self.error = [], code, error

    def __call__(self, goal, repo, *, max_cycles, max_seconds):
        self.calls.append((goal, repo, max_cycles, max_seconds))
        if self.error:
            raise self.error
        return self.code


def launch(monkeypatch, typed, runner=None):
    runner = runner or Runner()
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    return run_launcher(runner=runner), runner


def test_passes_answers_and_defaults_to_runner(monkeypatch, repo, capsys):
    code, runner = launch(monkeypatch, f"{repo}\nrevisar\n\n")
    assert runner.calls == [("revisar", str(repo), 3, 900)]
    assert code == 7
    assert "Pressione Enter para fechar." in capsys.readouterr().out


def test_accepts_quoted_path_with_spaces_and_accents(monkeypatch, tmp_path):
    path = make_repo(tmp_path / "meu projeto ção")
    _, runner = launch(monkeypatch, f'  "{path}"  \nrevisar\n\n')
    assert runner.calls[0][1] == str(path)


def test_accepts_repository_subfolder(monkeypatch, repo):
    _, runner = launch(monkeypatch, f"{repo / 'sub'}\nrevisar\n\n")
    assert runner.calls[0][1] == str(repo / "sub")


@pytest.mark.parametrize("kind", ["missing", "not_git", "no_commit", "empty"])
def test_asks_again_for_invalid_folder(monkeypatch, repo, tmp_path, kind):
    invalid = {"missing": tmp_path / "nope", "not_git": tmp_path / "plain",
               "no_commit": tmp_path / "fresh", "empty": ""}[kind]
    if kind == "not_git":
        invalid.mkdir()
    if kind == "no_commit":
        make_repo(invalid, commit=False)
    # Keep Git from finding a repository above the temporary folder.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _, runner = launch(monkeypatch, f"{invalid}\n{repo}\nrevisar\n\n")
    assert [call[:2] for call in runner.calls] == [("revisar", str(repo))]


def test_asks_again_for_empty_goal(monkeypatch, repo):
    _, runner = launch(monkeypatch, f"{repo}\n   \nrevisar\n\n")
    assert [call[0] for call in runner.calls] == ["revisar"]


@pytest.mark.parametrize("word", ["CANCELAR\n", "cancelar\n", ""])
@pytest.mark.parametrize("question", ["folder", "goal"])
def test_cancel_never_starts_agents(monkeypatch, repo, word, question):
    typed = word if question == "folder" else f"{repo}\n{word}"
    code, runner = launch(monkeypatch, typed)
    assert (code, runner.calls) == (2, [])


def test_ctrl_c_during_questions_exits_cleanly(monkeypatch):
    class Interrupted(io.StringIO):
        def readline(self, *args):
            raise KeyboardInterrupt

    runner = Runner()
    monkeypatch.setattr(sys, "stdin", Interrupted())
    assert run_launcher(runner=runner) == 2
    assert runner.calls == []


def test_missing_git_explains_and_stops(monkeypatch, repo, capsys):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    code, runner = launch(monkeypatch, f"{repo}\n{repo}\nrevisar\n\n")
    output = capsys.readouterr().out
    assert (code, runner.calls) == (2, [])
    assert output.count("Git não encontrado") == 1


def test_window_waits_even_when_runner_fails(monkeypatch, repo, capsys):
    code, _ = launch(monkeypatch, f"{repo}\nrevisar\n\n", Runner(error=RuntimeError("x")))
    output = capsys.readouterr().out
    assert code == 2
    assert "internal_failure" in output
    assert "Pressione Enter para fechar." in output
