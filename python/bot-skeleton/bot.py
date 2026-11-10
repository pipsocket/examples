"""A bot skeleton: a loop that reads a price, asks a signal what to hold, and trades to match.

Replace `decide()` with your own signal. What's around it is the part worth keeping:

- It trades only on a hedging account whose trade mode is demo, and reads the account again
  before every trade: if it stops being demo, or its login or server changes, the bot stops.
- It holds at most one position, of the symbol's smallest volume, with a filling mode the symbol
  allows. Its orders carry its magic number, and it only ever closes positions that carry it:
  your other trades on the account are left alone.
- Each trade's idempotency key is saved to a journal before the trade is sent. One run at a time
  holds the journal. On start, it settles what an earlier run left open, and refuses to trade
  while a trade may have executed without an answer.
- A pending trade is followed with requests.wait, which only looks it up. Any trade that doesn't
  end as a plain fill (no outcome yet, rejected, placed without a deal, settled by hand) stops
  the bot for you to look at, rather than have it trade on top.
- On exit it leaves its position as it is, and says which trades, if any, may still execute.
  With --kill-switch-on-exit it engages the account's kill switch, which closes every position
  and cancels every pending order on the account (not only the bot's) and refuses new trades
  until you lift it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from collections.abc import Callable
from typing import IO, Any, Literal, NoReturn

import pipsocket
from pipsocket import (
    AccountUnavailable,
    OutcomeUnknown,
    Pipsocket,
    PipsocketError,
    TradeRequest,
)
from pipsocket.client import Account
from pipsocket.types import AccountView, PositionView

ACCOUNT = os.environ["PIPSOCKET_ACCOUNT"]
SYMBOL = os.environ.get("PIPSOCKET_SYMBOL", "EURUSD")  # exactly as your broker names it
MAGIC = 7310  # the bot's own number: MT5 keeps it on its orders, deals and positions
DEVIATION = 20  # points, for symbols with instant or request execution
LOOKBACK = 5  # prices the signal looks at
JOURNAL = pathlib.Path("bot-journal.jsonl")
LOCK = pathlib.Path("bot-journal.lock")

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


def lock_journal() -> IO[str]:
    """Hold the journal for this run, so two runs never trade from one journal at once. The
    operating system lets go of the lock when the process ends, however it ends."""
    file = LOCK.open("a")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        raise Stop(f"Another run of the bot is using {JOURNAL} in this folder.") from None
    return file


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
        raise Stop(f"No answer for '{intent}' after the client's retries.") from error
    except PipsocketError as error:
        note({"key": key, "final": True, "refused": str(error)})  # refused: nothing was sent
        raise Stop(f"'{intent}' was refused: {error}") from error
    note({"key": key, "request": request.id, "state": request.state, "final": request.final})
    print(f"{intent}: {request.id} {request.state}")
    if request.outstanding:
        request = acct.requests.wait(request.id)  # lookups only: never sends it again
        note({"key": key, "state": request.state, "final": request.final})
        print(f"{intent}: {request.id} {request.state}")
    return request


def filled(request: TradeRequest) -> bool:
    """MT5 took it and made a deal. `completed` alone isn't a fill."""
    return request.accepted and bool(request.result["deal"])


class Bot:
    def __init__(self, acct: Account) -> None:
        self.acct = acct
        self.identity: tuple[int, str] | None = None
        account = self.check_account()
        self.identity = (account["login"], account["server"])
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
        if any(order["magic"] == MAGIC for order in acct.orders.list()):
            raise Stop(f"A pending order with magic {MAGIC} is on the account: look at it first.")

    def check_account(self) -> AccountView:
        """The account, read fresh: still demo, hedging, and the same login on the same server."""
        account = self.acct.get()
        if account.get("trade_mode") != "demo":
            raise Stop(f"The account's trade mode is {account.get('trade_mode')!r}, not demo.")
        if account.get("margin_mode") != "hedging":
            # On a netting account, a position holds everyone's volume on its symbol.
            raise Stop("The bot trades only hedging accounts.")
        if self.identity not in (None, (account["login"], account["server"])):
            raise Stop("The account's login or server changed: its positions aren't the bot's.")
        return account

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
        self.prices = [*self.prices[-LOOKBACK:], (tick["bid"] + tick["ask"]) / 2]
        want = decide(self.prices)
        if want is None:
            return
        held = self.mine()
        if want == "long" and not held:
            self.check_account()
            self.buy(tick["ask"])
        elif want == "flat" and held:
            self.check_account()
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
        if not filled(order):
            raise Stop(f"The buy didn't end as a fill: {order.state} {order.message}")

    def close(self, position: PositionView, bid: float) -> None:
        closed = send(
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
        if not filled(closed) or closed.state == "partial":
            raise Stop(f"The close didn't end as a full fill: {closed.state} {closed.message}")


def engage_kill_switch(bot: Bot | None, acct: Account) -> None:
    if bot is None:
        print("Not engaging the kill switch: the bot never started trading.")
        return
    try:
        bot.check_account()
    except Stop as error:
        print(f"Not engaging the kill switch: {error}")
        return
    print("Engaging the kill switch: every position and pending order on the account is closed.")
    try:
        outcome = acct.kill_switch.engage(deviation=DEVIATION)
    except AccountUnavailable as error:
        # Engaged all the same: the sweep runs when the terminal is back.
        print(f"Engaged, but the terminal didn't answer: {error.kill_switch}")
        return
    print(f"Kill switch engaged. Flat: {outcome['flat']}. Lift it in the dashboard or the API.")
    for request_id in outcome["unanswered"]:
        print(f"  {request_id} has no outcome yet: if it fills, the kill switch closes it")


def report_open_trades() -> bool:
    """Say which trades may still execute. True if there are any."""
    entries = open_entries()
    for entry in entries:
        where = f"request {entry['request']}" if "request" in entry else "no answer came back"
        print(f"May still execute: {entry['intent']} (key {entry['key']}, {where})")
    return bool(entries)


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
    try:
        lock = lock_journal()
    except Stop as error:
        stop(str(error))

    bot: Bot | None = None
    failure: str | None = None
    try:
        bot = Bot(acct)  # checks the account before anything else
        settle_journal(acct)
        print(f"Trading {bot.volume} {SYMBOL} with magic {MAGIC}. Ctrl+C stops.")
        step = 0
        while options.steps == 0 or step < options.steps:
            bot.step()
            step += 1
            time.sleep(options.interval)
    except Stop as error:
        failure = str(error)
    except KeyboardInterrupt:
        pass  # the usual way to stop it
    finally:
        if options.kill_switch_on_exit:
            engage_kill_switch(bot, acct)
        elif bot is not None:
            print(f"Stopped. Left as they are: {len(bot.mine())} of the bot's positions.")
        if report_open_trades():
            failure = failure or "Some trades have no known outcome yet."
        lock.close()
    if failure is not None:
        stop(failure)


if __name__ == "__main__":
    main()
