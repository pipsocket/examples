# Deal history in pandas (Python)

Reads an account's deals into a pandas DataFrame and prints the net result (profit, commission, swap and fees) by symbol and by day. It only reads.

History ranges are the **broker's server time**, in whole seconds, both ends included: not UTC, and not your machine's clock. So the script builds its range from the account's own `server_time`, and the times in the table are the broker's too (kept without a time zone, as they aren't UTC). If the terminal hasn't reported its time yet, it says so and stops.

A range with more deals than one answer can carry is refused (`RangeTooLarge`); the script then splits it in halves, with no gaps or overlaps, until each part fits.

## Run it

```bash
cp .env.example .env   # then put in your key and an account's id
uv run --env-file .env history_pandas.py                 # the last 30 days
uv run --env-file .env history_pandas.py --days 7 --csv deals.csv
```

## What you should see

```text
Deals from 2026-10-08 16:10:25 to 2026-11-07 16:10:25 (broker server time): 4

By symbol:
        deals  volume   net
symbol
EURUSD      2     0.2  11.5
XAUUSD      1     0.1  -4.5

Net by day (broker server time):
day
2026-10-18    9.5
2026-11-04   -4.5
2026-11-07    2.0

Net from trades: 7.00
Balance operations: 1, total 499.50
```

See [deal and order history](https://docs.pipsocket.com/api/history) in the docs.
