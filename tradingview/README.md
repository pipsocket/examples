# TradingView alerts

Message templates for a Pipsocket hook, and a script that posts one to your own hook as a **dry run**.

A hook is a webhook URL that trades: you create it in the dashboard, point it at your accounts, and paste its URL and a message into a TradingView alert. See [TradingView alerts](https://docs.pipsocket.com/tradingview), [alert messages](https://docs.pipsocket.com/tradingview-messages) and [alert outcomes](https://docs.pipsocket.com/tradingview-outcomes).

## The templates

Paste one into the **Message** box of a TradingView alert. TradingView fills in the `{{...}}` placeholders when the alert fires (in an alert's message box, not inside a script's `alert()` call).

| Template | For |
| --- | --- |
| [`open-buy.json`](templates/open-buy.json) | An indicator's `alertcondition()`: buy, sized to risk 1% of the account at a 25 pip stop loss |
| [`close-buy.json`](templates/close-buy.json) | The matching exit: closes the buys this template's lane holds |
| [`open-buy-limit.json`](templates/open-buy-limit.json) | A buy limit 15 pips below the price, for the day, at a fixed 0.01 lots |
| [`strategy.json`](templates/strategy.json) | A strategy's order fills: strategy mode, which brings the account's position to the strategy's |
| [`close-all.json`](templates/close-all.json) | Cancels the pending orders and closes the positions of every lane tagged `ema` |

Each has a `tag` (its lane: a hook only acts on positions its own lane opened) and an `event_id` built from the template's `src`, the symbol, the interval and the bar's time, so a resent alert is recognised and never trades twice. Change `src` when you copy a template for a new alert. The dashboard's message builder writes these for you, too.

## The dry run

`dry_run.py` fills in the placeholders with sample values, adds `"test": true`, and posts the message to your hook, as TradingView would. With `"test": true`, Pipsocket checks the message against each account the hook targets as it is now (the symbol map, sizing, levels, MT5's margin check) and **trades nothing**. The answer only says whether the alert was recorded; what the dry run found is in the hook's alert log in the dashboard.

The hook's URL is its credential: anyone who has it can send alerts to your accounts. Keep it in `.env` (it's in `.gitignore`), and the script never prints its secret.

It needs Python 3.10 or later and nothing else.

```bash
cp .env.example .env     # then put in your hook's URL
set -a; . ./.env; set +a
python dry_run.py templates/open-buy.json --symbol OANDA:EURUSD --interval 60
```

## What you should see

```text
Posting a dry run to https://hooks.pipsocket.com/t/hk_.../whk_...:
{
  "action": "open",
  "src": "q7m2",
  "event_id": "q7m2-ema-OANDA:EURUSD-60-open-buy-2026-11-08T14:30:00Z",
  ...
  "test": true
}
200: Recorded. The dry run's result is in the hook's alert log in the dashboard.
```
