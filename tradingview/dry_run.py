"""Post an alert message to your own hook as a dry run, the way TradingView would send it.

The message goes with "test": true, so Pipsocket checks it against each account the hook targets
(the symbol map, sizing, levels, MT5's margin check) and trades nothing. What it found is in the
hook's alert log in the dashboard; the answer here only says whether the alert was recorded.

    python dry_run.py templates/open-buy.json --symbol OANDA:EURUSD --interval 60

TradingView placeholders such as {{ticker}} are filled in with the values given, as TradingView
would fill them. Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

PLACEHOLDER = re.compile(r"\{\{([a-z_.]+)\}\}")

# What each answer means (docs.pipsocket.com/tradingview, "How Pipsocket answers TradingView").
ANSWERS = {
    200: "Recorded. The dry run's result is in the hook's alert log in the dashboard.",
    404: "No such hook, or the wrong secret: check the URL (a rotated URL stops working).",
    413: "The message is over 8 KB.",
    429: "Over the hook's limit of alerts: wait a minute.",
    503: "The alert could not be recorded: try again.",
}


def placeholder_values(symbol: str, interval: str, now: datetime.datetime) -> dict[str, str]:
    """Sample values for the placeholders TradingView fills in."""
    exchange, _, ticker = symbol.rpartition(":")
    bar = now.replace(second=0, microsecond=0)
    return {
        "exchange": exchange,
        "ticker": ticker,
        "interval": interval,
        "time": bar.strftime("%Y-%m-%dT%H:%M:%SZ"),  # the bar's time
        "timenow": now.strftime("%Y-%m-%dT%H:%M:%SZ"),  # when the alert fired
        # A strategy that went from flat to one contract long, for strategy-mode templates.
        "strategy.order.id": "Long",
        "strategy.order.alert_message": "1",
        "strategy.market_position": "long",
        "strategy.market_position_size": "1",
        "strategy.prev_market_position": "flat",
        "strategy.prev_market_position_size": "0",
    }


def fill(template: str, values: dict[str, str]) -> str:
    unknown = sorted(set(PLACEHOLDER.findall(template)) - values.keys())
    if unknown:
        sys.exit(f"No sample value for {', '.join(unknown)}: add one to placeholder_values().")
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def masked(url: str) -> str:
    """The hook URL with its secret hidden: the URL is the credential."""
    return re.sub(r"/whk_[^/]+$", "/whk_...", url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post an alert to your hook as a dry run.")
    parser.add_argument("template", help="a message template, such as templates/open-buy.json")
    parser.add_argument("--symbol", default="OANDA:EURUSD", help="EXCHANGE:TICKER, as TradingView")
    parser.add_argument("--interval", default="60", help="the chart's interval, as TradingView")
    options = parser.parse_args()

    url = os.environ.get("PIPSOCKET_HOOK_URL")
    if not url:
        sys.exit("Set PIPSOCKET_HOOK_URL to your hook's URL (see .env.example).")

    with open(options.template) as file:
        text = fill(
            file.read(),
            placeholder_values(
                options.symbol, options.interval, datetime.datetime.now(datetime.timezone.utc)
            ),
        )
    message = json.loads(text)
    message["test"] = True  # a dry run: checked against each account, never traded
    body = json.dumps(message).encode()

    print(f"Posting a dry run to {masked(url)}:")
    print(json.dumps(message, indent=2))
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            status = answer.status
    except urllib.error.HTTPError as error:
        status = error.code
    print(f"{status}: {ANSWERS.get(status, 'An answer the docs do not list.')}")
    if status != 200:
        sys.exit(1)


if __name__ == "__main__":
    main()
