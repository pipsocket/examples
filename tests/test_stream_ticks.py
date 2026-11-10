"""python/stream-ticks and node/stream-ticks: the same behaviour, checked on both."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import Running
from mock_pipsocket import MockPipsocket

EXAMPLES = ["python/stream-ticks/stream_ticks.py", "node/stream-ticks/stream-ticks.ts"]


@pytest.mark.parametrize("example", EXAMPLES)
def test_streams_prices_and_fills_and_starts_again_from_a_resync_after_a_gap(
    mock: MockPipsocket, start: Callable[..., Running], example: str
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
    running = start(example)

    running.wait_for("resync (connected): 1 positions, 0 pending orders")
    running.wait_for("EURUSD bid 1.0841 ask 1.08412")
    mock.push(
        {
            "type": "tick",
            "symbol": "EURUSD",
            "bid": 1.0843,
            "ask": 1.08433,
            "time_msc": 1794067826000,
            "last": 0,
            "volume": 0,
            "flags": 6,
        }
    )
    running.wait_for("EURUSD bid 1.0843 ask 1.08433")
    deal = {
        "comment": "",
        "commission": 0,
        "entry": "out",
        "fee": 0,
        "magic": 0,
        "order": 7002,
        "position": 7001,
        "price": 1.0843,
        "profit": 11.5,
        "reason": "client",
        "swap": 0,
        "symbol": "EURUSD",
        "ticket": 7003,
        "time_msc": 1794067826100,
        "type": "sell",
        "volume": 0.5,
    }
    mock.push({"type": "deal", "deal": deal})
    running.wait_for("deal: 7003 sell 0.5 EURUSD at 1.0843 (out, profit 11.5)")

    # The position closed while the stream was away: the resync's snapshot says so.
    mock.broker.positions.clear()
    mock.drop_streams()
    running.wait_for("resync (reconnected): 0 positions, 0 pending orders")
    running.wait_for("ticks and events from the gap are not sent again")
    running.wait_for("EURUSD bid")  # the tick set was sent again on the new connection
    stopped = running.interrupt()

    assert stopped.code == 0, stopped.output
    assert "stopped" in stopped.stdout
    assert mock.upgrades == 2
    assert mock.trades() == []
