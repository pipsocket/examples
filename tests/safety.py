"""The rules every trading example keeps, checked on what it actually sent to the mock."""

from __future__ import annotations

import json
from typing import Any

from mock_pipsocket import ACCOUNT, MockPipsocket

MARKET_FILLINGS = ("fok", "ioc")


def check_trades(mock: MockPipsocket, magic: int) -> None:
    """Fail if any trade the example sent breaks a rule.

    - It went to a demo account.
    - Its idempotency key was in the journal before the call arrived, and a key is only ever
      sent again with exactly the same call (a retry), never for a different trade.
    - An order is the symbol's smallest volume, with a filling mode the symbol lists that a
      market order can use, and carries the example's magic number.
    - A close is for a position the example opened (or that carried its magic number before it
      started), for no more volume than that position had, with a filling mode the symbol lists
      that a market order can use, and carries the example's magic number.
    """
    assert mock.broker.account["trade_mode"] == "demo", "traded on an account that isn't demo"
    sent_with: dict[str, tuple[str, str, object]] = {}
    # Position ticket: (symbol, volume), for the positions the example may close.
    ours = {p["ticket"]: (p["symbol"], p["volume"]) for p in owned_at_start(mock, magic)}
    ours |= opened_by_orders(mock)
    prefix = f"/v1/accounts/{ACCOUNT}"
    for call in mock.trades():
        key = call.key
        assert key, f"{call.method} {call.path} was sent without an idempotency key"
        assert call.journal is not None and key in journal_keys(call.journal), (
            f"the key for {call.method} {call.path} wasn't saved before it was sent"
        )
        this = (call.method, call.path, call.body)
        assert sent_with.setdefault(key, this) == this, f"key {key} was used for two trades"

        if call.method == "POST" and call.path == prefix + "/orders":
            symbol = mock.broker.symbols[call.body["symbol"]]
            assert call.body["volume"] == symbol["volume_min"], "not the symbol's smallest volume"
            filling = call.body["type_filling"]
            assert filling in MARKET_FILLINGS and filling in symbol["filling_modes"], filling
            assert call.body.get("magic") == magic, "an order without the example's magic number"
        elif "/positions/" in call.path:
            ticket = int(call.path.split("/positions/")[1].split("/")[0])
            assert ticket in ours, f"touched position {ticket}, which the example didn't open"
            symbol_name, volume = ours[ticket]
            assert call.body["volume"] <= volume + 1e-9, f"closed more of {ticket} than it opened"
            filling = call.body["type_filling"]
            symbol = mock.broker.symbols[symbol_name]
            assert filling in MARKET_FILLINGS and filling in symbol["filling_modes"], filling
            assert call.body.get("magic") == magic, "a close without the example's magic number"
        else:
            raise AssertionError(f"an unexpected trade call: {call.method} {call.path}")


def owned_at_start(mock: MockPipsocket, magic: int) -> list[dict[str, Any]]:
    """Positions carrying the magic that were on the account before the example traded."""
    return [p for p in mock.start_positions if p["magic"] == magic]


def opened_by_orders(mock: MockPipsocket) -> dict[int, tuple[str, float]]:
    """Positions opened by orders the example placed, with what filled, from their requests as
    they stand now (a pending order's ticket is known only once it settles). A position's ticket
    on a hedging account is its order's."""
    prefix = f"/v1/accounts/{ACCOUNT}"
    ids = {
        call.answer["request"]
        for call in mock.trades()
        if call.path == prefix + "/orders"
        and isinstance(call.answer, dict)
        and "request" in call.answer
    }
    opened: dict[int, tuple[str, float]] = {}
    for request in (mock.broker.requests[i] for i in ids):
        result = request.get("result") or {}
        if result.get("deal"):
            opened[result["order"]] = (request["sent"]["symbol"], result["volume"])
    return opened


def journal_keys(text: str) -> set[str]:
    return {json.loads(line)["key"] for line in text.splitlines() if line.strip()}
