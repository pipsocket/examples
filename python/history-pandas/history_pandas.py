"""An account's deals in a pandas DataFrame: net profit by symbol and by day.

History ranges are the broker's server time, in seconds, both ends included: not UTC, and not
this machine's clock. So the range is built from the account's own `server_time`, and the
times in the table are the broker's too. It only reads.
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
from pipsocket import Pipsocket, RangeTooLarge
from pipsocket.client import Account
from pipsocket.types import DealView

ACCOUNT = os.environ["PIPSOCKET_ACCOUNT"]
DAY = 86400


def deals_between(acct: Account, start: int, end: int) -> list[DealView]:
    """Every deal from start to end, halving the range while it holds too many for one answer."""
    try:
        return acct.history.deals(start, end)
    except RangeTooLarge:
        if end - start < 60:
            raise
        middle = (start + end) // 2
        return deals_between(acct, start, middle) + deals_between(acct, middle + 1, end)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="how far back to read (default 30)")
    parser.add_argument("--csv", help="also write every deal to this CSV file")
    options = parser.parse_args()

    # PIPSOCKET_BASE_URL is for tests against a local mock; leave it unset.
    ps = Pipsocket(base_url=os.environ.get("PIPSOCKET_BASE_URL"))  # reads PIPSOCKET_KEY
    acct = ps.account(ACCOUNT)

    now = acct.get().get("server_time")  # the broker's server time, in seconds
    if now is None:
        sys.exit("The account's terminal hasn't reported its server time yet: try again shortly.")
    start = now - options.days * DAY
    deals = deals_between(acct, start, now)

    print(
        f"Deals from {pd.to_datetime(start, unit='s')} to {pd.to_datetime(now, unit='s')} "
        f"(broker server time): {len(deals)}"
    )
    if not deals:
        return

    frame = pd.DataFrame(deals)
    # time_msc is broker server time too: kept without a time zone, as it isn't UTC.
    frame["time"] = pd.to_datetime(frame["time_msc"], unit="ms")
    frame["net"] = frame["profit"] + frame["commission"] + frame["swap"] + frame["fee"]
    if options.csv:
        frame.to_csv(options.csv, index=False)
        print(f"Wrote {options.csv}")

    # Trades are buy and sell deals; the rest are balance operations (deposits, charges...).
    trades = frame[frame["type"].isin(["buy", "sell"])]
    others = frame[~frame["type"].isin(["buy", "sell"])]

    by_symbol = trades.groupby("symbol").agg(
        deals=("ticket", "count"), volume=("volume", "sum"), net=("net", "sum")
    )
    print("\nBy symbol:")
    print(by_symbol.round(2).to_string())

    by_day = trades.assign(day=trades["time"].dt.date).groupby("day")["net"].sum()
    print("\nNet by day (broker server time):")
    print(by_day.round(2).to_string())

    print(f"\nNet from trades: {trades['net'].sum():.2f}")
    if not others.empty:
        print(f"Balance operations: {len(others)}, total {others['net'].sum():.2f}")


if __name__ == "__main__":
    main()
