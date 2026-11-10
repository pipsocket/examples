"""python/history-pandas."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from conftest import Run
from mock_pipsocket import MockPipsocket

EXAMPLE = "python/history-pandas/history_pandas.py"
SERVER_TIME = 1794067825  # the broker's clock, which is not this machine's
DAY = 86400


def deal(ticket: int, seconds_ago: int, symbol: str, kind: str, profit: float) -> dict[str, Any]:
    return {
        "comment": "",
        "commission": -0.5,
        "entry": "out",
        "fee": 0,
        "magic": 0,
        "order": ticket,
        "position": ticket,
        "price": 1.08,
        "profit": profit,
        "reason": "expert",
        "swap": 0,
        "symbol": symbol,
        "ticket": ticket,
        "time_msc": (SERVER_TIME - seconds_ago) * 1000,
        "type": kind,
        "volume": 0.1,
    }


def test_reads_deals_in_broker_time_splitting_a_range_too_large(
    mock: MockPipsocket, run: Callable[..., Run]
) -> None:
    mock.broker.account["server_time"] = SERVER_TIME
    mock.broker.longest_range = 8 * DAY  # a busy account: a month is too much for one answer
    mock.broker.deals = [
        deal(1, 40 * DAY, "EURUSD", "sell", 999.0),  # before the range
        deal(2, 20 * DAY, "EURUSD", "sell", 10.0),
        deal(3, 3 * DAY, "XAUUSD", "buy", -4.0),
        deal(4, 3 * DAY, "", "balance", 500.0),
        deal(5, 0, "EURUSD", "sell", 2.5),  # at the range's end, which is included
    ]

    result = run(EXAMPLE)

    assert result.code == 0, result.output
    assert "(broker server time): 4" in result.stdout
    assert "Net from trades: 7.00" in result.stdout  # 9.5 + 2.0 - 4.5
    assert "Balance operations: 1, total 499.50" in result.stdout
    reads = mock.calls_to("GET", "/history/deals")
    ranges = sorted((int(c.query["from"]), int(c.query["to"])) for c in reads if c.status == 200)
    assert ranges[0][0] == SERVER_TIME - 30 * DAY
    assert ranges[-1][1] == SERVER_TIME
    for (_, end), (start, _) in itertools.pairwise(ranges):
        assert start == end + 1  # no gaps, no overlaps
    assert mock.trades() == []


def test_stops_until_the_terminal_has_reported_its_time(
    mock: MockPipsocket, run: Callable[..., Run]
) -> None:
    mock.broker.account["server_time"] = None

    result = run(EXAMPLE)

    assert result.code != 0
    assert "hasn't reported its server time" in result.stderr
    assert mock.calls_to("GET", "/history/deals") == []
