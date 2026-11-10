"""python/bot-skeleton."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
from conftest import Run, Running
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
    assert "May still execute: buy 0.01 EURUSD" in first.stdout
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


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"trade_mode": "real"}, "not demo"),
        ({"login": 5058427999, "server": "ExampleBroker-Live"}, "login or server changed"),
    ],
)
def test_stops_when_the_account_changes_under_it(
    mock: MockPipsocket, run: Callable[..., Run], change: dict[str, Any], says: str
) -> None:
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN
    mock.broker.account_changes_after = (1, change)  # after the bot's first read

    result = run(EXAMPLE, *FAST, "--steps", "20", "--kill-switch-on-exit")

    assert result.code != 0
    assert says in result.stderr
    assert mock.trades() == []
    assert mock.calls_to("POST", "/kill-switch") == []
    assert "Not engaging the kill switch" in result.stdout


def test_refuses_a_netting_account(mock: MockPipsocket, run: Callable[..., Run]) -> None:
    mock.broker.account["margin_mode"] = "netting"
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN

    result = run(EXAMPLE, *FAST, "--steps", "20")

    assert result.code != 0
    assert "only hedging accounts" in result.stderr
    assert mock.trades() == []


def test_one_run_at_a_time_per_journal(
    mock: MockPipsocket, run: Callable[..., Run], start: Callable[..., Running]
) -> None:
    first = start(EXAMPLE, "--interval", "0.1")
    first.wait_for("Trading 0.01 EURUSD")

    second = run(EXAMPLE, *FAST, "--steps", "20")

    assert second.code != 0
    assert "Another run of the bot" in second.stderr
    assert first.interrupt().code == 0


def test_stops_when_a_buy_is_taken_but_not_filled(
    mock: MockPipsocket, run: Callable[..., Run]
) -> None:
    mock.broker.prices["EURUSD"] = [(1.0840, 1.0841)] * 6 + [
        (1.0850 + i / 1000, 1.0851) for i in range(6)
    ]
    mock.broker.plan = ["placed"]

    result = run(EXAMPLE, *FAST, "--steps", "12")

    assert result.code != 0
    assert "didn't end as a fill: placed" in result.stderr
    assert len(mock.trades()) == 1
    check_trades(mock, MAGIC)


def test_ctrl_c_while_a_buy_is_pending_says_it_may_still_execute(
    mock: MockPipsocket, start: Callable[..., Running]
) -> None:
    mock.broker.prices["EURUSD"] = UP_THEN_DOWN[:7]
    mock.broker.plan = ["pending_forever"]
    bot = start(EXAMPLE, *FAST, "--steps", "7", "--kill-switch-on-exit")
    bot.wait_for("buy 0.01 EURUSD: req_")

    stopped = bot.interrupt()

    assert stopped.code != 0
    assert "has no outcome yet: if it fills, the kill switch closes it" in stopped.stdout
    assert "May still execute: buy 0.01 EURUSD" in stopped.stdout
    check_trades(mock, MAGIC)
