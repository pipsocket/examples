"""A bot skeleton: a loop that reads a price, asks a signal what to hold, and trades to match.

Replace `decide()` with your own signal. What's around it is the part worth keeping:

- It trades only on an account whose trade mode is demo.
- It holds at most one position, of the symbol's smallest volume, with a filling mode the symbol
  allows. Its orders carry its magic number, and it only ever closes positions that carry it:
  your other trades on the account are left alone.
- Each trade's idempotency key is saved to a journal before the trade is sent. On start, it
  settles what an earlier run left open, and refuses to trade while a trade may have executed
  without an answer.
- A pending trade is followed with requests.wait, which only looks it up. If a trade's outcome
  can't be known, the bot stops rather than trade on top of it.
- On exit it leaves its position as it is. With --kill-switch-on-exit it engages the account's
  kill switch, which closes every position and cancels every pending order on the account (not
  only the bot's) and refuses new trades until you lift it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from collections.abc import Callable
from typing import Any, Literal, NoReturn

import pipsocket
from pipsocket import (
    AccountUnavailable,
    OutcomeUnknown,
    Pipsocket,
    PipsocketError,
    TradeRequest,
)
from pipsocket.client import Account
from pipsocket.types import PositionView

ACCOUNT = os.environ["PIPSOCKET_ACCOUNT"]
SYMBOL = os.environ.get("PIPSOCKET_SYMBOL", "EURUSD")  # exactly as your broker names it
MAGIC = 7310  # the bot's own number: MT5 keeps it on its orders, deals and positions
DEVIATION = 20  # points, for symbols with instant or request execution
LOOKBACK = 5  # prices the signal looks at
JOURNAL = pathlib.Path("bot-journal.jsonl")

Want = Literal["long", "flat"]
# The filling modes a market order can use.
MARKET_FILLINGS: tuple[Literal["fok", "ioc"], ...] = ("fok", "ioc")


def market_filling(allowed: list[str]) -> Literal["fok", "ioc"] | None:
    """A filling mode the symbol allows that a market order can use, if there is one."""
    return next((mode for mode in MARKET_FILLINGS if mode in allowed), None)


def decide(prices: list[float]) -> Want | None:
    """The signal: what to hold now, or None to keep what's held. Replace it with your own.

    This one is a toy breakout: long when the price goes above the last few, flat when it goes
    below them.
    """
    if len(prices) <= LOOKBACK:
        return None
    *before, latest = prices[-LOOKBACK - 1 :]
    if latest > max(before):
        return "long"
    if latest < min(before):
        return "flat"
    return None


class Stop(Exception):
    """Something the bot can't trade past: it stops."""


# The journal: each trade's key is written before the trade is sent, and its outcome once known.


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
            raise Stop(
                f"An earlier trade ({entry['intent']}, key {entry['key']}) may have executed: its "
                f"answer never came back. Check the account's positions and history for magic "
                f"{MAGIC}, then remove its lines from {JOURNAL}."
            )
        request = acct.requests.wait(entry["request"])  # lookups only: sends nothing
        note({"key": entry["key"], "state": request.state, "final": request.final})
        if not request.final:
            raise Stop(f"An earlier trade ({request.id}) is still {request.state}.")
        print(f"An earlier trade settled: {request.id} {request.state}")


def send(acct: Account, intent: str, call: Callable[[str], TradeRequest]) -> TradeRequest:
    """Send one trade with a key saved first, and follow it until it has an outcome."""
    key = pipsocket.new_key()
    note({"key": key, "intent": intent})  # saved before anything is sent
    try:
        request = call(key)
    except OutcomeUnknown as error:
        raise Stop(
            f"No answer for '{intent}' after the client's retries: it may have executed. Its key "
            f"{key} stays open in {JOURNAL}."
        ) from error
    except PipsocketError as error:
        note({"key": key, "final": True, "refused": str(error)})  # refused: nothing was sent
        raise Stop(f"'{intent}' was refused: {error}") from error
    note({"key": key, "request": request.id, "state": request.state, "final": request.final})
    print(f"{intent}: {request.id} {request.state}")
    if request.outstanding:
        request = acct.requests.wait(request.id)  # lookups only: never sends it again
        note({"key": key, "state": request.state, "final": request.final})
        print(f"{intent}: {request.id} {request.state}")
    if not request.final:
        raise Stop(f"{request.id} is still {request.state}: it may still execute.")
    return request


class Bot:
    def __init__(self, acct: Account) -> None:
        self.acct = acct
        symbol = acct.symbols.get(SYMBOL)
        filling = market_filling(symbol["filling_modes"])
        if filling is None:
            raise Stop(f"{SYMBOL} allows neither fok nor ioc: no market orders.")
        if symbol.get("trading") != "full":
            raise Stop(f"{SYMBOL} can't be bought and sold now (trading: {symbol.get('trading')}).")
        self.filling: Literal["fok", "ioc"] = filling
        self.volume = symbol["volume_min"]
        self.needs_price = symbol.get("execution") in ("instant", "request")
        self.prices: list[float] = []

    def mine(self) -> list[PositionView]:
        """The bot's positions: the symbol's, carrying its magic number. Read fresh each time."""
        return [
            p for p in self.acct.positions.list() if p["symbol"] == SYMBOL and p["magic"] == MAGIC
        ]

    def step(self) -> None:
        try:
            tick = self.acct.symbols.tick(SYMBOL)
        except PipsocketError as error:  # after the client's own retries: try the next step
            print(f"no price this time: {error}")
            return
        self.prices.append((tick["bid"] + tick["ask"]) / 2)
        want = decide(self.prices)
        held = self.mine()
        if want == "long" and not held:
            self.buy(tick["ask"])
        elif want == "flat":
            for position in held:
                self.close(position, tick["bid"])

    def buy(self, ask: float) -> None:
        order = send(
            self.acct,
            f"buy {self.volume} {SYMBOL}",
            lambda key: self.acct.orders.place(
                symbol=SYMBOL,
                type="buy",
                volume=self.volume,
                type_filling=self.filling,
                price=ask if self.needs_price else None,
                deviation=DEVIATION if self.needs_price else None,
                magic=MAGIC,
                idempotency_key=key,
            ),
        )
        if order.accepted and not order.result["deal"]:
            # Taken but not filled (completed doesn't mean filled): nothing to hold yet.
            print(f"order {order.result['order']} placed, not filled")

    def close(self, position: PositionView, bid: float) -> None:
        send(
            self.acct,
            f"close position {position['ticket']}",
            lambda key: self.acct.positions.close(
                position["ticket"],
                volume=position["volume"],
                type_filling=self.filling,
                price=bid if self.needs_price else None,
                deviation=DEVIATION if self.needs_price else None,
                magic=MAGIC,
                idempotency_key=key,
            ),
        )


def engage_kill_switch(acct: Account) -> None:
    print("Engaging the kill switch: every position and pending order on the account is closed.")
    try:
        outcome = acct.kill_switch.engage(deviation=DEVIATION)
    except AccountUnavailable as error:
        # Engaged all the same: the sweep runs when the terminal is back.
        print(f"Engaged, but the terminal didn't answer: {error.kill_switch}")
        return
    print(f"Kill switch engaged. Flat: {outcome['flat']}. Lift it in the dashboard or the API.")


def stop(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="A bot skeleton that trades a demo account.")
    parser.add_argument("--interval", type=float, default=5, help="seconds between steps")
    parser.add_argument("--steps", type=int, default=0, help="stop after this many (0: never)")
    parser.add_argument(
        "--kill-switch-on-exit",
        action="store_true",
        help="on exit, engage the account's kill switch: closes EVERY position and pending order "
        "on the account, not only the bot's, and blocks new trades until it is lifted",
    )
    options = parser.parse_args()

    # PIPSOCKET_BASE_URL is for tests against a local mock; leave it unset.
    acct = Pipsocket(base_url=os.environ.get("PIPSOCKET_BASE_URL")).account(ACCOUNT)
    account = acct.get()
    if account.get("trade_mode") != "demo":
        stop(
            f"This account's trade mode is {account.get('trade_mode')!r}: the bot trades only demo."
        )

    failure: str | None = None
    try:
        settle_journal(acct)
        bot = Bot(acct)
        if account.get("margin_mode") != "hedging" and any(
            p["symbol"] == SYMBOL and p["magic"] != MAGIC for p in acct.positions.list()
        ):
            raise Stop(f"Netting account with someone else's position on {SYMBOL}: not trading.")
        print(f"Trading {bot.volume} {SYMBOL} with magic {MAGIC}. Ctrl+C stops.")
        step = 0
        while options.steps == 0 or step < options.steps:
            bot.step()
            step += 1
            time.sleep(options.interval)
    except Stop as error:
        failure = str(error)
    except KeyboardInterrupt:
        pass
    finally:
        if options.kill_switch_on_exit:
            engage_kill_switch(acct)
        else:
            held = [
                p for p in acct.positions.list() if p["symbol"] == SYMBOL and p["magic"] == MAGIC
            ]
            print(f"Stopped. Left as they are: {len(held)} of the bot's positions.")
    if failure is not None:
        stop(failure)


if __name__ == "__main__":
    main()
