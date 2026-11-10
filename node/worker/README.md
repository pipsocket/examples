# Account report from a Cloudflare Worker (TypeScript)

A [Cloudflare Worker](https://developers.cloudflare.com/workers/) with a [cron trigger](https://developers.cloudflare.com/workers/configuration/cron-triggers/): every 15 minutes it reads an account over REST and logs one line, its status, balance, equity, margin level, kill switch, open positions and the last day's deals. It only reads, so it is safe on any account.

- The SDK's REST client runs on Workers as it is (the stream doesn't: it needs a long-lived socket).
- The key is a Worker secret, never in the code or `wrangler.jsonc`.
- The last day's deals are read in the broker's server time, from the account's `server_time`, not from the Worker's clock.
- While the account's terminal isn't running, positions and history can't be read (`AccountUnavailable`): the report says `null` for them, which means unknown, not none.

## Run it

```bash
npm install
cp .env.example .env     # your key, and the account, for local runs
npm run dev              # wrangler dev --test-scheduled
curl "http://localhost:8787/__scheduled?cron=*/15+*+*+*+*"
```

Deploy it, with the account's id in `wrangler.jsonc` and the key as a secret:

```bash
npx wrangler secret put PIPSOCKET_KEY
npm run deploy
```

## What you should see

In the `wrangler dev` output, or in the Worker's logs once deployed:

```text
{"account":"acc_01m6tx3k9f2qw8r7bznd5yvh4c","status":"connected","trade_mode":"demo","balance":10000,"equity":10012.4,"margin_level":46182.66,"kill_switch":false,"positions":["buy 0.1 EURUSD"],"deals_last_day":2,"net_last_day":-51.9}
```

MetaTrader is a trademark of MetaQuotes Ltd. Pipsocket is not affiliated with MetaQuotes.
