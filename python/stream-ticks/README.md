# Stream ticks and fills (Python)

Keeps the account's WebSocket stream open with the SDK's helper (`acct.stream(...)`) and prints prices, deals and settled requests as they happen. It only reads: it sends no trades. Stop it with Ctrl+C.

The helper reconnects by itself and, after every connect and every gap, yields a `resync` event: the account, positions and pending orders read over REST once the stream was live, and the deals from history since the last checkpoint. The script:

- **Treats a `resync` as the new baseline.** It replaces its own copy of the positions and pending orders with the snapshot. `None` means unknown (the terminal isn't running), which is not the same as none.
- **Treats every other event as a notice.** A `deal` event can be older than the baseline, so it is never added to or taken from the snapshot. Deals are deduplicated by ticket, and the tickets seen are forgotten when the account's login or server changes, as the new one numbers its own.
- **Says what a gap loses.** Ticks and events from the gap are not sent again. The resync's snapshot and its `history` (best effort, or `None` with `history_reason`) cover what changed. For a trade you are waiting on, `acct.requests.wait(id)` looks it up.

## Run it

```bash
cp .env.example .env   # then put in your key and an account's id
uv run --env-file .env stream_ticks.py
```

`PIPSOCKET_SYMBOLS` is the symbols to stream prices for, separated by commas.

## What you should see

```text
resync (connected): 1 positions, 0 pending orders, equity 10012.4
EURUSD bid 1.0841 ask 1.08412
EURUSD bid 1.0843 ask 1.08433
deal: 771203498 sell 0.1 EURUSD at 1.0843 (out, profit 18.0)
resync (reconnected): 0 positions, 0 pending orders, equity 10030.4
  after a gap: ticks and events from the gap are not sent again; the snapshot and the history below cover what changed
EURUSD bid 1.0844 ask 1.08442
^Cstopped
```

See [the WebSocket stream](https://docs.pipsocket.com/websocket) in the docs.

MetaTrader is a trademark of MetaQuotes Ltd. Pipsocket is not affiliated with MetaQuotes.
