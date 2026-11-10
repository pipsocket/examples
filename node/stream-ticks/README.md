# Stream ticks and fills (TypeScript)

Keeps the account's WebSocket stream open with the SDK's helper (`acct.stream(...)`) and prints prices, deals and settled requests as they happen. The same script as [`python/stream-ticks`](../../python/stream-ticks). It only reads. Stop it with Ctrl+C, which calls `stream.close()`.

- **A `resync` is the new baseline**: the script replaces its copy of positions and pending orders with the snapshot (`null` means unknown, not none).
- **Every other event is a notice**: never added to or taken from the snapshot. Deals are deduplicated by ticket, forgotten when the account's login or server changes.
- **A gap loses its ticks and events**: they are not sent again. The resync's snapshot and `history` cover what changed; `requests.wait(id)` tells you how a trade you sent settled.

`isEvent(event, "tick")` narrows an event to its type; a message of a type added later still comes through, and is ignored here.

## Run it

Node 22.18 or later. On Node the stream needs the [`ws`](https://www.npmjs.com/package/ws) package, which is in `package.json`.

```bash
npm install
cp .env.example .env   # then put in your key and an account's id
npm start
```

## What you should see

```text
resync (connected): 1 positions, 0 pending orders, equity 10012.4
EURUSD bid 1.0841 ask 1.08412
deal: 771203498 sell 0.1 EURUSD at 1.0843 (out, profit 18)
resync (reconnected): 0 positions, 0 pending orders, equity 10030.4
  after a gap: ticks and events from the gap are not sent again; the snapshot and the history below cover what changed
^Cstopped
```

MetaTrader is a trademark of MetaQuotes Ltd. Pipsocket is not affiliated with MetaQuotes.
