"""Prices, fills and the account as they happen, through the SDK's stream helper.

The helper keeps the socket open, reconnects, and after every connect and every gap hands you a
`resync` event: a fresh baseline read over REST. This script keeps its own copy of the open
positions and pending orders, replaces it on each resync, and treats every other event as a
notice. It sends no trades. Stop it with Ctrl+C.
"""

from __future__ import annotations

import os
from typing import Any

from pipsocket import Pipsocket, TradeRequest

ACCOUNT = os.environ["PIPSOCKET_ACCOUNT"]
# The symbols to stream prices for, exactly as your broker names them, separated by commas.
SYMBOLS = os.environ.get("PIPSOCKET_SYMBOLS", "EURUSD").split(",")


def main() -> None:
    # PIPSOCKET_BASE_URL and PIPSOCKET_STREAM_URL are for tests against a local mock: leave
    # them unset.
    ps = Pipsocket(
        base_url=os.environ.get("PIPSOCKET_BASE_URL"),
        stream_url=os.environ.get("PIPSOCKET_STREAM_URL"),
    )
    acct = ps.account(ACCOUNT)

    # The account as the last resync showed it. None means unknown (the terminal isn't running),
    # which is not the same as empty.
    positions: dict[int, dict[str, Any]] | None = None
    orders: dict[int, dict[str, Any]] | None = None
    deals_seen: set[int] = set()  # tickets are per login and server
    identity: tuple[object, object] | None = None

    try:
        with acct.stream(ticks=SYMBOLS) as events:
            for event in events:
                match event["type"]:
                    case "resync":
                        # The new baseline: replace what we hold, never add events to it.
                        positions = by_ticket(event["positions"])
                        orders = by_ticket(event["orders"])
                        account: dict[str, Any] = event["account"] or {}
                        login = (account.get("login"), account.get("server"))
                        if identity not in (None, login):
                            # Another login or server: its tickets are a different series.
                            deals_seen.clear()
                            print(f"  the account is now login {login[0]} on {login[1]}")
                        identity = login
                        held = "unknown" if positions is None else len(positions)
                        pending = "unknown" if orders is None else len(orders)
                        print(
                            f"resync ({event['reason']}): {held} positions, {pending} pending "
                            f"orders, equity {account.get('equity')}"
                        )
                        if event["reason"] != "connected":
                            print(
                                "  after a gap: ticks and events from the gap are not sent again; "
                                "the snapshot and the history below cover what changed"
                            )
                        history = event["history"]
                        if history is None:
                            if event["history_reason"] is not None:
                                print(f"  no history read back ({event['history_reason']})")
                        else:
                            for deal in history["deals"]:
                                if deal["ticket"] not in deals_seen:
                                    deals_seen.add(deal["ticket"])
                                    print(f"  deal from history: {describe(deal)}")
                    case "tick":
                        print(f"{event['symbol']} bid {event['bid']} ask {event['ask']}")
                    case "deal":
                        # A notice: it may be older than the baseline, so don't apply it to the
                        # positions above. Read them again (acct.positions.list()) when it matters.
                        deal = event["deal"]
                        if deal["ticket"] not in deals_seen:
                            deals_seen.add(deal["ticket"])
                            print(f"deal: {describe(deal)}")
                    case "request":
                        request = TradeRequest.from_event(event)
                        print(f"request {request.id} settled: {request.state}")
                    case "status":
                        print(f"account status: {event['status']}")
                    case _:
                        pass  # account (every second), transaction, and types added later
    except KeyboardInterrupt:
        print("stopped")


def by_ticket(rows: list[dict[str, Any]] | None) -> dict[int, dict[str, Any]] | None:
    return None if rows is None else {row["ticket"]: row for row in rows}


def describe(deal: dict[str, Any]) -> str:
    return (
        f"{deal['ticket']} {deal['type']} {deal['volume']} {deal['symbol']} at {deal['price']} "
        f"({deal['entry']}, profit {deal['profit']})"
    )


if __name__ == "__main__":
    main()
