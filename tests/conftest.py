"""Fixtures that run the examples, as they are, against the local mock."""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mock_pipsocket import ACCOUNT, KEY, MockPipsocket

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(scope="session")
def server() -> Iterator[MockPipsocket]:
    mock = MockPipsocket()
    yield mock
    mock.stop()


@pytest.fixture
def mock(server: MockPipsocket) -> Iterator[MockPipsocket]:
    server.reset()
    yield server
    assert server.problems == [], "the mock saw calls or answers that don't match the API"


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def environment(mock: MockPipsocket, **extra: str) -> dict[str, str]:
    """The machine's environment without any Pipsocket settings, pointed at the mock."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PIPSOCKET_") and name != "VIRTUAL_ENV"
    }
    env.update(
        PIPSOCKET_KEY=KEY,
        PIPSOCKET_ACCOUNT=ACCOUNT,
        PIPSOCKET_BASE_URL=mock.base_url,
        PIPSOCKET_STREAM_URL=mock.stream_url,
        NODE_NO_WARNINGS="1",
    )
    env.update(extra)
    assert env["PIPSOCKET_BASE_URL"].startswith("http://127.0.0.1:"), (
        "tests never call the real API"
    )
    return env


def command(example: str, *args: str) -> list[str]:
    """How to run an example: Python ones with their own locked project, Node ones with node."""
    path = ROOT / example
    if path.suffix == ".py" and (path.parent / "pyproject.toml").exists():
        project = path.parent
        return [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(project),
            "python",
            str(path),
            *args,
        ]
    if path.suffix == ".py":  # the standard library only
        return [sys.executable, str(path), *args]
    return ["node", str(path), *args]


@pytest.fixture
def run(mock: MockPipsocket, tmp_path: pathlib.Path) -> Callable[..., Run]:
    """Run an example to the end in a fresh directory (where it keeps its journal)."""

    def run_example(example: str, *args: str, timeout: float = 60, **env: str) -> Run:
        mock.journal_dir = tmp_path
        done = subprocess.run(
            command(example, *args),
            cwd=tmp_path,
            env=environment(mock, **env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return Run(done.returncode, done.stdout, done.stderr)

    return run_example


class Running:
    """An example left running (a stream), read line by line and stopped with Ctrl+C."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.lines: list[str] = []

    def wait_for(self, text: str, seconds: float = 20) -> str:
        assert self.process.stdout is not None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line and self.process.poll() is not None:
                break
            self.lines.append(line)
            if text in line:
                return line
        stderr = self.process.stderr.read() if self.process.stderr else ""
        raise AssertionError(f"never printed {text!r}; printed:\n{''.join(self.lines)}{stderr}")

    def interrupt(self, seconds: float = 15) -> Run:
        self.process.send_signal(signal.SIGINT)
        stdout, stderr = self.process.communicate(timeout=seconds)
        return Run(self.process.returncode, "".join(self.lines) + stdout, stderr)


@pytest.fixture
def start(mock: MockPipsocket, tmp_path: pathlib.Path) -> Iterator[Callable[..., Running]]:
    started: list[Running] = []

    def start_example(example: str, *args: str, **env: str) -> Running:
        mock.journal_dir = tmp_path
        process = subprocess.Popen(
            command(example, *args),
            cwd=tmp_path,
            env=environment(mock, PYTHONUNBUFFERED="1", **env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        running = Running(process)
        started.append(running)
        return running

    yield start_example
    for running in started:
        if running.process.poll() is None:
            running.process.kill()
            running.process.wait()


def node_available() -> bool:
    return shutil.which("node") is not None
