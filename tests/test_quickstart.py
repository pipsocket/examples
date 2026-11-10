"""python/quickstart and node/quickstart: the same behaviour, checked on both."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import Run
from mock_pipsocket import MockPipsocket, symbol_spec
from safety import check_trades

MAGIC = 4021
EXAMPLES = ["python/quickstart/quickstart.py", "node/quickstart/quickstart.ts"]

Runner = Callable[..., Run]


@pytest.fixture(params=EXAMPLES)
def quickstart(request: pytest.FixtureRequest, run: Runner) -> Callable[..., Run]:
    example: str = request.param
    return lambda **env: run(example, **env)


def test_buys_the_minimum_and_closes_only_its_own_position(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.symbols["EURUSD"] = symbol_spec(filling_modes=["boc", "ioc"], volume_min=0.02)
    someone_elses = {
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
    mock.broker.positions.append(someone_elses)

    result = quickstart()

    assert result.code == 0, result.output
    assert "Filled: deal" in result.stdout
    assert "Closed position" in result.stdout
    check_trades(mock, MAGIC)
    place, close = mock.trades()
    assert place.body["type_filling"] == "ioc"
    assert close.path.endswith(f"/positions/{place.answer['result']['order']}/close")
    assert mock.broker.positions == [someone_elses]


@pytest.mark.parametrize("trade_mode", ["real", "contest", None])
def test_refuses_any_account_that_isnt_demo(
    mock: MockPipsocket, quickstart: Callable[..., Run], trade_mode: str | None
) -> None:
    mock.broker.account["trade_mode"] = trade_mode

    result = quickstart()

    assert result.code != 0
    assert "only on demo accounts" in result.stderr
    assert mock.trades() == []


def test_stops_when_the_symbol_allows_no_market_filling(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.symbols["EURUSD"] = symbol_spec(filling_modes=["boc"])

    result = quickstart()

    assert result.code != 0
    assert "neither fok nor ioc" in result.stderr
    assert mock.trades() == []


def test_waits_for_a_pending_order_then_closes_it(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.plan = ["pending"]

    result = quickstart()

    assert result.code == 0, result.output
    assert "waiting" in result.stdout
    assert "Closed position" in result.stdout
    check_trades(mock, MAGIC)
    order_id = mock.trades()[0].answer["request"]
    lookups = mock.calls_to("GET", f"/requests/{order_id}")
    assert len(lookups) >= 2
    # Nothing else was sent while the order was pending.
    assert mock.calls.index(lookups[-1]) < mock.calls.index(mock.trades()[1])


def test_a_rejected_order_closes_nothing(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.plan = ["reject"]

    result = quickstart()

    assert result.code == 0, result.output
    assert "TRADE_RETCODE_MARKET_CLOSED" in result.stdout
    check_trades(mock, MAGIC)
    assert len(mock.trades()) == 1


def test_a_lost_answer_keeps_the_key_and_blocks_the_next_run(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.plan = ["lost"]

    first = quickstart()

    assert first.code != 0
    assert "may have executed" in first.stderr
    check_trades(mock, MAGIC)
    keys = {call.key for call in mock.trades()}
    assert len(mock.trades()) == 3 and len(keys) == 1  # the client's retries, one key

    mock.broker.plan = []
    second = quickstart()

    assert second.code != 0
    assert "may have executed" in second.stderr
    assert len(mock.trades()) == 3  # nothing more sent


def test_sends_a_price_where_the_symbol_executes_on_request(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.symbols["EURUSD"] = symbol_spec(execution="instant", filling_modes=["fok"])
    mock.broker.prices["EURUSD"] = [(1.0841, 1.08412), (1.0845, 1.08452)]

    result = quickstart()

    assert result.code == 0, result.output
    check_trades(mock, MAGIC)
    place, close = mock.trades()
    assert place.body["price"] == 1.08412 and place.body["deviation"] > 0
    assert close.body["price"] == 1.0845


def test_on_a_netting_account_leaves_an_existing_position_alone(
    mock: MockPipsocket, quickstart: Callable[..., Run]
) -> None:
    mock.broker.account["margin_mode"] = "netting"
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

    result = quickstart()

    assert result.code != 0
    assert "netting" in result.stderr
    assert mock.trades() == []
