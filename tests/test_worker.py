"""node/worker: the Worker's scheduled handler, run by wrangler's local dev server."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from conftest import ROOT, environment
from mock_pipsocket import KEY, MockPipsocket

WORKER = ROOT / "node" / "worker"


class WranglerDev:
    """`wrangler dev --test-scheduled`, pointed at the mock."""

    def __init__(self, mock: MockPipsocket) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.process = subprocess.Popen(
            [
                "npx",
                "--no-install",
                "wrangler",
                "dev",
                "--test-scheduled",
                "--ip",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--var",
                f"PIPSOCKET_KEY:{KEY}",
                "--var",
                f"PIPSOCKET_BASE_URL:{mock.base_url}",
            ],
            cwd=WORKER,
            env=environment(mock, WRANGLER_SEND_METRICS="false", CI="1"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,  # so npx, wrangler and workerd stop together
        )
        self.output = ""

    def run_scheduled(self) -> None:
        """Fire the cron trigger once, waiting for the dev server to come up."""
        url = f"http://127.0.0.1:{self.port}/__scheduled?cron=*/15+*+*+*+*"
        deadline = time.monotonic() + 90
        while True:
            try:
                answer = httpx.get(url, timeout=60)
                break
            except httpx.TransportError:
                if time.monotonic() > deadline or self.process.poll() is not None:
                    raise
                time.sleep(0.5)
        assert answer.status_code == 200, answer.text

    def stop(self) -> str:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.output, _ = self.process.communicate(timeout=30)
        return self.output

    def report(self) -> dict[str, Any]:
        """The one report line the Worker logged."""
        time.sleep(0.5)  # the log line can follow the answer
        lines = [line for line in self.stop().splitlines() if line.startswith("{")]
        assert len(lines) == 1, self.output
        return json.loads(lines[0])


@pytest.fixture
def worker(mock: MockPipsocket) -> Iterator[WranglerDev]:
    dev = WranglerDev(mock)
    yield dev
    dev.stop()


def test_logs_a_report_on_schedule_reading_history_in_broker_time(
    mock: MockPipsocket, worker: WranglerDev
) -> None:
    mock.broker.positions.append(
        {
            "comment": "",
            "identifier": 7001,
            "magic": 0,
            "price_open": 1.08,
            "sl": 0,
            "symbol": "EURUSD",
            "ticket": 7001,
            "time_msc": 1794067801211,
            "tp": 0,
            "type": "buy",
            "volume": 0.5,
        }
    )

    worker.run_scheduled()
    report = worker.report()

    assert report["status"] == "connected"
    assert report["positions"] == ["buy 0.5 EURUSD"]
    assert report["deals_last_day"] == 0
    [history] = mock.calls_to("GET", "/history/deals")
    server_time = mock.broker.account["server_time"]
    assert (int(history.query["from"]), int(history.query["to"])) == (
        server_time - 86400,
        server_time,
    )
    assert {call.method for call in mock.calls} == {"GET"}  # it only reads


def test_reports_positions_as_unknown_while_the_terminal_is_away(
    mock: MockPipsocket, worker: WranglerDev
) -> None:
    mock.broker.account["status"] = "stopped"
    mock.broker.terminal_running = False

    worker.run_scheduled()
    report = worker.report()

    assert report["status"] == "stopped"
    assert report["positions"] is None and report["deals_last_day"] is None
    assert "is stopped" in worker.output
