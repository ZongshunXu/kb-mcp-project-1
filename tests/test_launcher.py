"""Launcher forwarding and isolated, real-stdio demo checks."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import run as launcher

ROOT = Path(__file__).resolve().parents[1]


def test_no_command_only_shows_help(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        raise AssertionError("Default help must not run indexing or any command")
    monkeypatch.setattr(launcher.subprocess, "run", unexpected)
    assert launcher.main([]) == 0
    assert "index" in capsys.readouterr().out


@pytest.mark.parametrize("command,module", [
    ("index", "scripts.index"), ("query", "scripts.query"), ("check", "scripts.check_mcp"),
    ("eval", "scripts.eval"), ("test", "pytest"), ("agent", "scripts.agent"), ("demo", "scripts.demo_server"),
])
def test_current_interpreter_arguments_working_directory_and_exit_code(command, module, monkeypatch):
    calls = []
    monkeypatch.setenv("KB_LAUNCHER_TEST_ENV", "preserved")

    def capture(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(launcher.subprocess, "run", capture)
    assert launcher.main([command, "--help", "--literal=中文 space"]) == 7
    arguments, kwargs = calls[0]
    assert arguments == [sys.executable, "-m", module, "--help", "--literal=中文 space"]
    assert kwargs["cwd"] == ROOT
    assert kwargs["check"] is False
    assert kwargs["env"]["KB_LAUNCHER_TEST_ENV"] == "preserved"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"


def test_launcher_from_parent_directory_forwards_module_help():
    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "query", "--help"], cwd=ROOT.parent,
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    assert "--query" in result.stdout and "--group" in result.stdout


def test_fixed_demo_real_stdio_never_touches_formal_data_or_reports():
    paths = [ROOT / name for name in (
        "artifacts/manifest.json", "data/qrels/test.jsonl", "data/qrels/review.json",
        "reports/results.csv", "reports/per_query.csv", "reports/mcp_check.json",
    )]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}
    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "demo", "--check"], cwd=ROOT.parent,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "KB_MODEL_PATH": str(ROOT / "models" / "not-a-model"), "KB_EXPERIMENT_ID": "P3"},
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["mode"] == "fixed-data-no-formal-eval"
    assert report["formal_evaluation"] is False
    assert report["real_mcp_client"] is True
    assert all(report["checks"].values())
    assert report["hits"][0]["parent_id"] is None
    assert report["fetched"]["node_id"] == report["hits"][0]["node_id"]
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
