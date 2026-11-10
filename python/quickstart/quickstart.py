"""Read a demo account, buy the smallest volume a symbol allows, handle the outcome, close it.

It trades only on an account whose trade mode is demo, and only ever closes the position its own
order opened (found by this script's magic number).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections.abc import Callable
from typing import Any, Literal, NoReturn

import pipsocket
from pipsocket import OutcomeUnknown, Pipsocket, PipsocketError, TradeRequest
from pipsocket.client import Account

ACCOUNT = os.environ["PIPSOCKET_ACCOUNT"]
SYMBOL = os.environ.get("PIPSOCKET_SYMBOL", "EURUSD")  # exactly as your broker names it
MAGIC = 4021  # this script's own number: MT5 keeps it on the order, its deals and its position
DEVIATION = 20  # points, for symbols with instant or request execution
JOURNAL = pathlib.Path("quickstart-journal.jsonl")
# The filling modes a market order can use.
MARKET_FILLINGS: tuple[Literal["fok", "ioc"], ...] = ("fok", "ioc")


def market_filling(allowed: list[str]) -> Literal["fok", "ioc"] | None:
    """A filling mode the symbol allows that a market order can use, if there is one."""
    return next((mode for mode in MARKET_FILLINGS if mode in allowed), None)


def stop(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


# The journal: each trade's idempotency key is written here before the trade is sent, and its
# outcome once it is known. If the script dies in between, the next run finds the open entry.


def note(entry: dict[str, Any]) -> None:
    with JOURNAL.open("a") as file:
        file.write(json.dumps(entry) + "\n")
        file.flush()
        os.fsync(file.fileno())


def open_entries() -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    if JOURNAL.exists():
        for line in JOURNAL.read_text().splitlines():
            entry = json.loads(line)
            entries.setdefault(entry["key"], {}).update(entry)
    return [entry for entry in entries.values() if not entry.get("final")]


def settle_journal(acct: Account) -> None:
    """Finish what an earlier run left open before trading again."""
    for entry in open_entries():
        if "request" not in entry:
            # Sent, but no answer came back: it may have executed, and only the same key can
            # find out. Don't trade on top of it.
            stop(
                f"An earlier trade ({entry['intent']}, key {entry['key']}) may have executed: "
                f"its answer never came back. Check the account's positions and history for "
                f"magic {MAGIC}, then remove its lines from {JOURNAL}."
            )
        request = acct.requests.wait(entry["request"])  # lookups only: sends nothing
        note({"key": entry["key"], "state": request.state, "final": request.final})
        if not request.final:
            stop(f"An earlier trade ({request.id}) is still {request.state}: try again later.")
        print(f"An earlier trade settled: {request.id} {request.state}")


def send(acct: Account, intent: str, call: Callable[[str], TradeRequest]) -> TradeRequest:
    """Send one trade with a key saved first, and follow it until it has an outcome."""
    key = pipsocket.new_key()
    note({"key": key, "intent": intent})  # saved before anything is sent
    try:
        request = call(key)
    except OutcomeUnknown:
        stop(
            f"No answer for '{intent}' after the client's retries: it may have executed. "
            f"Its key {key} stays open in {JOURNAL}; check the account before running again."
        )
    except PipsocketError as error:
        note({"key": key, "final": True, "refused": str(error)})  # refused: nothing was sent
        stop(f"'{intent}' was refused: {error}")
    note({"key": key, "request": request.id, "state": request.state, "final": request.final})
    if request.outstanding:
        print(f"{request.id} is {request.state}: no answer from the trade server yet, waiting")
        request = acct.requests.wait(request.id)  # lookups only: never sends it again
        note({"key": key, "state": request.state, "final": request.final})
    if not request.final:
        stop(f"{request.id} is still {request.state}: it may still execute. Run again later.")
    return request


def main() -> None:
    # PIPSOCKET_BASE_URL is for tests against a local mock; leave it unset.
    ps = Pipsocket(base_url=os.environ.get("PIPSOCKET_BASE_URL"))  # reads PIPSOCKET_KEY
    acct = ps.account(ACCOUNT)

    account = acct.get()
    print(
        f"Account {account['login']} on {account['server']}: {account['status']}, "
        f"balance {account.get('balance')} {account.get('currency')}"
    )
    if account.get("trade_mode") != "demo":
        stop(
            f"This account's trade mode is {account.get('trade_mode')!r}. "
            "The quickstart trades only on demo accounts."
        )
    if account["status"] != "connected" or not account.get("trade_allowed"):
        stop("The account isn't ready to trade: it must be connected with trading allowed.")

    settle_journal(acct)

    # A filling mode and a volume the symbol allows: MT5 rejects an order with any other. A
    # market order can use fok or ioc.
    symbol = acct.symbols.get(SYMBOL)
    filling = market_filling(symbol["filling_modes"])
    if filling is None:
        stop(f"{SYMBOL} allows neither fok nor ioc ({symbol['filling_modes']}): no market order.")
    if symbol.get("trading") != "full":
        stop(f"{SYMBOL} can't be bought and sold now (trading: {symbol.get('trading')}).")
    volume = symbol["volume_min"]
    needs_price = symbol.get("execution") in ("instant", "request")

    # On a netting account an order on a symbol that already has a position changes that
    # position, which this script didn't open.
    if account.get("margin_mode") != "hedging" and any(
        position["symbol"] == SYMBOL for position in acct.positions.list()
    ):
        stop(f"This is a netting account with a position on {SYMBOL} already: not touching it.")

    price = acct.symbols.tick(SYMBOL)["ask"] if needs_price else None
    order = send(
        acct,
        f"buy {volume} {SYMBOL}",
        lambda key: acct.orders.place(
            symbol=SYMBOL,
            type="buy",
            volume=volume,
            type_filling=filling,
            price=price,
            deviation=DEVIATION if needs_price else None,
            magic=MAGIC,
            idempotency_key=key,
        ),
    )

    if order.state in ("rejected", "not_sent", "not_executed"):
        print(f"Not filled: {order.state} {order.retcode_name or ''} {order.message}")
        return
    if order.state == "executed":
        print(f"Settled as executed by the account's owner: look for magic {MAGIC} in positions.")
        return
    # completed, placed or partial: MT5 took it. A fill has a deal; completed alone isn't one.
    if not order.result["deal"]:
        print(f"{order.state}, but not filled: order {order.result['order']}, no deal")
        return
    print(
        f"Filled: deal {order.result['deal']}, {order.result['volume']} at {order.result['price']}"
    )

    # The position this order opened: its identifier is the order's ticket.
    opened = order.result["position"] or order.result["order"]
    mine = [
        position
        for position in acct.positions.list()
        if position["identifier"] == opened and position["magic"] == MAGIC
    ]
    if not mine:
        print("Its position is already closed.")
    for position in mine:
        price = acct.symbols.tick(SYMBOL)["bid"] if needs_price else None
        closed = send(
            acct,
            f"close {position['volume']} of position {position['ticket']}",
            lambda key, position=position, price=price: acct.positions.close(
                position["ticket"],
                volume=position["volume"],  # all of it: a partial fill opened less
                type_filling=filling,
                price=price,
                deviation=DEVIATION if needs_price else None,
                magic=MAGIC,
                idempotency_key=key,
            ),
        )
        if closed.accepted and closed.result["deal"]:
            print(f"Closed position {position['ticket']}: deal {closed.result['deal']}")
        else:
            print(f"Position {position['ticket']} not closed: {closed.state} {closed.message}")


if __name__ == "__main__":
    main()
