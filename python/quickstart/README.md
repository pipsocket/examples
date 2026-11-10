# Quickstart (Python)

Reads a demo account, buys the smallest volume the symbol allows, handles the outcome, and closes the position it opened.

- It stops unless the account's `trade_mode` is `demo`, it is a hedging account, it is connected and trading is allowed. (On a netting account one position holds every trade's volume on a symbol, so "the position this script opened" can include other trades.)
- It takes the filling mode (`fok` or `ioc`) and the volume (`volume_min`) from the symbol, and stops if the symbol allows neither mode. Where the symbol executes on request or instantly, it sends the current price with a deviation.
- Its orders carry magic number `4021`, and it only closes the position its own order opened. It won't buy while a position or pending order with that number is still on the account from an earlier run: close it first.
- Each trade's idempotency key is written to `quickstart-journal.jsonl` before the trade is sent, and its outcome once known. A `pending` answer is followed with `requests.wait`. If the answer to a trade is lost, the entry stays open, and the next run stops until you have checked the account.
- It reads the account again before each trade, and stops if it is no longer demo, connected with trading allowed, or on the same login and server. One run at a time uses the journal in a folder.
- `completed` is not taken to mean filled: it looks for a deal in the result. After closing, it reads the positions again: a close can fill only in part, and then it says how much is left.
- Ctrl+C while a trade has no outcome yet says which trade may still execute.

## Run it

You need [uv](https://docs.astral.sh/uv/) (or Python 3.10 or later and `pip install pipsocket==0.1.0`).

```bash
cp .env.example .env   # then put in your key and a demo account's id
uv run --env-file .env quickstart.py
```

`PIPSOCKET_SYMBOL` is the symbol exactly as your broker names it (`EURUSD` on one broker can be `EURUSD.r` on another).

## What you should see

```text
Account 5058427372 on ExampleBroker-Demo: connected, balance 10000 USD
Filled: deal 771203455, 0.01 at 1.08412
Closed position 881402311: deal 771203498
```

With the market closed you would see `Not filled: rejected TRADE_RETCODE_MARKET_CLOSED ...`, and nothing is closed. Run it on an account that isn't demo and it stops before sending anything:

```text
This account's trade mode is 'real'. The quickstart trades only on demo accounts.
```

See the [quickstart](https://docs.pipsocket.com/quickstart) and [errors and retcodes](https://docs.pipsocket.com/errors) in the docs.

MetaTrader is a trademark of MetaQuotes Ltd. Pipsocket is not affiliated with MetaQuotes.
