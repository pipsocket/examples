# Bot skeleton (Python)

A loop that reads a price, asks a signal what to hold, and trades a demo account to match. The signal, `decide()`, is a toy breakout: replace it with your own. What's around it is the part to keep.

- **Demo and hedging only, checked before every trade.** It stops before doing anything unless the account's `trade_mode` is `demo` and it is a hedging account, and reads the account again before each trade and before engaging the kill switch: if it stops being demo, or its login or server changes, the bot stops.
- **One small position.** It holds at most one position, of the symbol's `volume_min`, with a filling mode the symbol allows (`fok` or `ioc`).
- **Its own trades only.** Its orders carry magic number `7310`, and it only ever closes positions that carry it. A position it opened in an earlier run is picked up again; your other trades are left alone.
- **Save, then send, one run at a time.** Each trade's key comes from `pipsocket.new_key()` and is written to `bot-journal.jsonl` before the trade is sent, and its outcome after. A lock file keeps a second run in the same folder from using the journal at the same time. On start it settles what an earlier run left open: a request it has an id for is looked up with `requests.wait`; a trade whose answer never came back stops the bot until you have checked the account.
- **Anything but a plain fill stops it.** A `pending` trade is followed with `requests.wait`, which only looks it up. If a trade then has no outcome, was rejected, was taken without a deal, filled only in part, or was settled by hand, or the client's retries ran out without an answer (`OutcomeUnknown`), the bot stops rather than trade on top of it.
- **Nothing destructive on exit, unless you ask.** On exit (Ctrl+C, `--steps` done, or a stop) it leaves its position as it is, says so, and lists any trade that may still execute.

### The kill switch on exit

`--kill-switch-on-exit` engages the account's [kill switch](https://docs.pipsocket.com/api/kill-switch) when the bot exits, for whatever reason. That is a different thing from closing the bot's own position:

- it closes **every** position and cancels **every** pending order on the account, the bot's and anyone else's (your manual trades and other bots' included);
- it then refuses new orders on the account, from every source, until you lift it in the dashboard or with `acct.kill_switch.lift()`.

Use it when you'd rather have a flat account than a running position nobody watches. It is off by default.

## Run it

```bash
cp .env.example .env   # then put in your key and a demo account's id
uv run --env-file .env bot.py                   # a step every 5 seconds until Ctrl+C
uv run --env-file .env bot.py --interval 1 --steps 60
uv run --env-file .env bot.py --kill-switch-on-exit
```

## What you should see

```text
Trading 0.01 EURUSD with magic 7310. Ctrl+C stops.
buy 0.01 EURUSD: req_5f0c2a9e41d7b3a8c6e2f190 completed
close position 881402311: req_7a31c0e95b2d4f8a1c6e3b07 completed
^CStopped. Left as they are: 0 of the bot's positions.
```

With `--kill-switch-on-exit`, the last lines are:

```text
Engaging the kill switch: every position and pending order on the account is closed.
Kill switch engaged. Flat: True. Lift it in the dashboard or the API.
```

MetaTrader is a trademark of MetaQuotes Ltd. Pipsocket is not affiliated with MetaQuotes.
