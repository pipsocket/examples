# Pipsocket examples

Small projects that run as they are, using the [Python](https://github.com/pipsocket/pipsocket-python) and [TypeScript](https://github.com/pipsocket/pipsocket-node) clients for the Pipsocket API: REST and WebSocket access to MetaTrader 5 accounts hosted by Pipsocket.

**Use a demo account.** The examples that trade refuse to run unless the account's trade mode is `demo` and it is a hedging account. They are written to be copied into your own code, so they show the parts that keep a real account safe too.

| Folder | What it shows |
| --- | --- |
| [`python/quickstart`](python/quickstart) | Read the account, place the smallest order the symbol allows, handle every outcome, close what it opened |
| [`python/stream-ticks`](python/stream-ticks) | Prices and fills as they happen, with reconnects and resyncs |
| [`python/bot-skeleton`](python/bot-skeleton) | A loop that trades on a signal: keys saved before sending, `requests.wait`, the kill switch on exit (opt-in) |
| [`python/history-pandas`](python/history-pandas) | Deals into a pandas DataFrame, over a range of the broker's server time |
| [`node/quickstart`](node/quickstart) | The quickstart in TypeScript |
| [`node/stream-ticks`](node/stream-ticks) | The stream in TypeScript |
| [`node/worker`](node/worker) | A Cloudflare Worker that reads an account on a schedule and logs a report |
| [`tradingview`](tradingview) | Alert message templates, and a dry run posted to your own hook (`"test": true`) |

Each folder has its own README, an `.env.example` and pinned dependencies. Copy `.env.example` to `.env` and fill it in: your workspace's key from the dashboard's **API key** page (`psk_...`) and the account's id (`acc_...`) from the same page. In `tradingview/` it takes your hook's URL instead: a hook's URL is its credential, and the dry run doesn't use the API key.

## What every trading example does

These are the habits worth copying from them:

- **Filling mode and volume come from the symbol.** A symbol allows only some filling modes and volumes, and MT5 rejects an order with any other. A market order uses `fok` or `ioc`, whichever the symbol lists; if it lists neither, the example stops and says so. The volume is the symbol's `volume_min`.
- **`completed` doesn't mean filled.** A pending order placed, a change and a cancel can all be `completed`. A fill has a deal: `result.deal` isn't 0.
- **A key is saved before the trade is sent.** Each trade gets a new key from `new_key()` (Python) or `newKey()` (TypeScript), written to a journal file first. If an answer is lost, the next run finds the open entry and won't trade on top of it.
- **A pending trade is followed with `requests.wait`**, which only looks the request up and never sends it again.
- **A magic number marks the example's own trades.** It only ever closes positions that carry it, and reads the positions again after a close, as a close can fill only in part.
- **History ranges are the broker's server time**, built from the account's `server_time`, never from your clock or UTC.

## How they are tested

Every example runs end to end in CI against a local mock of the API and its stream ([`tests/mock_pipsocket.py`](tests/mock_pipsocket.py)), with no key and no account, on the SDK versions pinned here. The mock checks the shape of every request and answer against the API's published OpenAPI document (how the API behaves, it models from the docs), and the tests check what each example sent: demo accounts only, the minimum volume, a filling mode the symbol allows, the magic number, a saved key, and nothing touched that the example didn't open. The examples read `PIPSOCKET_BASE_URL` and `PIPSOCKET_STREAM_URL` only so the tests can point them at the mock; leave those unset.

A scheduled job runs them again every day on the newest published SDKs and opens an issue if they break.

```bash
uv sync
(cd node/quickstart && npm ci); (cd node/stream-ticks && npm ci); (cd node/worker && npm ci)
uv run pytest
```

## Links

- Docs: [Quickstart](https://docs.pipsocket.com/quickstart), [API reference](https://docs.pipsocket.com/api/place-an-order), [WebSocket stream](https://docs.pipsocket.com/websocket), [Errors and retcodes](https://docs.pipsocket.com/errors), [TradingView alerts](https://docs.pipsocket.com/tradingview)
- SDKs: [pipsocket on PyPI](https://pypi.org/project/pipsocket/) ([source](https://github.com/pipsocket/pipsocket-python)), [pipsocket on npm](https://www.npmjs.com/package/pipsocket) ([source](https://github.com/pipsocket/pipsocket-node))
- Questions about your account or billing: [support@pipsocket.com](mailto:support@pipsocket.com). Problems with an example: [issues](https://github.com/pipsocket/examples/issues).

## Licence

MIT. See [LICENSE](LICENSE).

MetaTrader is a trademark of MetaQuotes Ltd. Pipsocket is not affiliated with MetaQuotes.
