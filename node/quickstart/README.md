# Quickstart (TypeScript)

Reads a demo account, buys the smallest volume the symbol allows, handles the outcome, and closes the position it opened. The same script as [`python/quickstart`](../../python/quickstart), with the same safeguards:

- It stops unless the account's `trade_mode` is `demo`, the account is connected and trading is allowed.
- It takes the filling mode (`fok` or `ioc`) and the volume (`volume_min`) from the symbol, and stops if the symbol allows neither mode. Where the symbol executes on request or instantly, it sends the current price with a deviation.
- On a netting account that already has a position on the symbol, it stops.
- Its orders carry magic number `4021`, and it only closes the position its own order opened.
- Each trade's idempotency key (`newKey()`) is written to `quickstart-journal.jsonl` before the trade is sent. A `pending` answer is followed with `requests.wait`. If the answer to a trade is lost, the next run stops until you have checked the account.
- A `switch` on `order.kind` narrows `order.result`; a fill is a result with a `deal` that isn't 0.

## Run it

Node 22.18 or later (it runs the TypeScript file directly).

```bash
npm install
cp .env.example .env   # then put in your key and a demo account's id
npm start
```

## What you should see

```text
Account 5058427372 on ExampleBroker-Demo: connected, balance 10000 USD
Filled: deal 771203455, 0.01 at 1.08412
Closed position 881402311: deal 771203498
```

On an account that isn't demo it stops before sending anything:

```text
This account's trade mode is "real". The quickstart trades only on demo accounts.
```
