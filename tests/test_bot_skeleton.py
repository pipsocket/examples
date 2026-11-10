"""python/bot-skeleton."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any

from conftest import Run
from mock_pipsocket import MockPipsocket
from safety import check_trades

EXAMPLE = "python/bot-skeleton/bot.py"
MAGIC = 7310
FAST = ("--interval", "0")
# Quiet, then a breakout up (buy), then a breakout down (close).
UP_THEN_DOWN = [(1.0840, 1.0841)] * 6 + [(1.0850, 1.0851)] * 3 + [(1.0830, 1.0831)] * 3


def position(ticket: int, magic: int) -> dict[str, Any]:
    return {
        "comment": "",
        "identifier": ticket,
        "magic": magic,
        "price_open": 1.08,
        "sl": 0,
        "symbol": "EURUSD",
        "ticket": ticket,
        "time_msc": 1794067801211,
        "tp": 0,
        "type": "buy",
        "volume": 0.01,
    }


def test_buys_on_the_signal_and_closes_only_its_own_position(
    mock: MockPipsocket, run: Callable[..., Run]
) -> None:
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN
    someone_elses = position(7001, magic=0)
    mock.broker.positions.append(someone_elses)

    result = run(EXAMPLE, *FAST, "--steps", str(len(UP_THEN_DOWN)))

    assert result.code == 0, result.output
    check_trades(mock, MAGIC)
    place, close = mock.trades()
    assert place.body["type"] == "buy"
    assert "/positions/" in close.path
    assert mock.broker.positions == [someone_elses]
    assert mock.calls_to("POST", "/kill-switch") == []
    assert "Left as they are: 0 of the bot's positions" in result.stdout


def test_takes_up_its_own_position_from_an_earlier_run(
    mock: MockPipsocket, run: Callable[..., Run]
) -> None:
    mock.broker.prices["EURUSD"] = [(1.0840, 1.0841)] * 6 + [(1.0830, 1.0831)]
    mock.broker.positions += [position(7001, magic=0), position(7002, magic=MAGIC)]

    result = run(EXAMPLE, *FAST, "--steps", "7")

    assert result.code == 0, result.output
    check_trades(mock, MAGIC)
    [close] = mock.trades()
    assert close.path.endswith("/positions/7002/close")


def test_the_kill_switch_on_exit_is_only_engaged_when_asked(
    mock: MockPipsocket, run: Callable[..., Run]
) -> None:
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN[:7]

    result = run(EXAMPLE, *FAST, "--steps", "7", "--kill-switch-on-exit")

    assert result.code == 0, result.output
    check_trades(mock, MAGIC)
    [engage] = mock.calls_to("POST", "/kill-switch")
    assert mock.calls.index(engage) > mock.calls.index(mock.trades()[-1])
    assert "Kill switch engaged" in result.stdout
    assert mock.broker.positions == []


def test_waits_for_a_trade_an_earlier_run_left_pending(
    mock: MockPipsocket, run: Callable[..., Run], tmp_path: pathlib.Path
) -> None:
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN[:7]
    mock.broker.plan = ["pending_forever"]
    first = run(EXAMPLE, *FAST, "--steps", "7")  # its order never settles while it waits
    assert first.code != 0
    assert "may still execute" in first.stderr
    [placed] = mock.trades()
    request_id = placed.answer["request"]
    mock.broker.settles_after[request_id] = 1  # settles on the next lookup

    second = run(EXAMPLE, *FAST, "--steps", "1")

    assert second.code == 0, second.output
    assert f"An earlier trade settled: {request_id} completed" in second.stdout
    assert len(mock.trades()) == 1
    check_trades(mock, MAGIC)


def test_refuses_to_trade_while_an_earlier_trade_may_have_executed(
    mock: MockPipsocket, run: Callable[..., Run], tmp_path: pathlib.Path
) -> None:
    entry = {"key": "4b6f0f7e-2d0e-4c55-9d43-0a1b3b8f5e21", "intent": "buy 0.01 EURUSD"}
    (tmp_path / "bot-journal.jsonl").write_text(json.dumps(entry) + "\n")

    result = run(EXAMPLE, *FAST, "--steps", "20")

    assert result.code != 0
    assert "may have executed" in result.stderr
    assert mock.trades() == []


def test_refuses_an_account_that_isnt_demo(mock: MockPipsocket, run: Callable[..., Run]) -> None:
    mock.broker.account["trade_mode"] = "real"
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN

    result = run(EXAMPLE, *FAST, "--steps", "20", "--kill-switch-on-exit")

    assert result.code != 0
    assert mock.trades() == []
    assert mock.calls_to("POST", "/kill-switch") == []
