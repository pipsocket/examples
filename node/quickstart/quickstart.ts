// Read a demo account, buy the smallest volume a symbol allows, handle the outcome, close it.
//
// It trades only on an account whose trade mode is demo, and only ever closes the position its
// own order opened (found by this script's magic number).

import { appendFileSync, closeSync, existsSync, fsyncSync, openSync, readFileSync } from "node:fs";
import {
  type Account,
  isFinal,
  isOutstanding,
  newKey,
  OutcomeUnknown,
  Pipsocket,
  PipsocketError,
  type TradeRequest,
} from "pipsocket";

const ACCOUNT = required("PIPSOCKET_ACCOUNT");
const SYMBOL = process.env.PIPSOCKET_SYMBOL ?? "EURUSD"; // exactly as your broker names it
const MAGIC = 4021; // this script's own number: MT5 keeps it on the order, its deals and its position
const DEVIATION = 20; // points, for symbols with instant or request execution
const JOURNAL = "quickstart-journal.jsonl";

function required(name: string): string {
  const value = process.env[name];
  if (!value) stop(`Set ${name} (see .env.example).`);
  return value;
}

function stop(message: string): never {
  console.error(message);
  process.exit(1);
}

// The journal: each trade's idempotency key is written here before the trade is sent, and its
// outcome once it is known. If the script dies in between, the next run finds the open entry.

interface Entry {
  key: string;
  intent?: string;
  request?: string;
  state?: string;
  final?: boolean;
  refused?: string;
}

function note(entry: Entry): void {
  const file = openSync(JOURNAL, "a");
  try {
    appendFileSync(file, `${JSON.stringify(entry)}\n`);
    fsyncSync(file);
  } finally {
    closeSync(file);
  }
}

function openEntries(): Entry[] {
  const entries = new Map<string, Entry>();
  if (existsSync(JOURNAL)) {
    for (const line of readFileSync(JOURNAL, "utf8").split("\n")) {
      if (!line) continue;
      const entry = JSON.parse(line) as Entry;
      entries.set(entry.key, { ...entries.get(entry.key), ...entry });
    }
  }
  return [...entries.values()].filter((entry) => !entry.final);
}

/** Finish what an earlier run left open before trading again. */
async function settleJournal(acct: Account): Promise<void> {
  for (const entry of openEntries()) {
    if (!entry.request) {
      // Sent, but no answer came back: it may have executed, and only the same key can find out.
      // Don't trade on top of it.
      stop(
        `An earlier trade (${entry.intent}, key ${entry.key}) may have executed: its answer never came back. ` +
          `Check the account's positions and history for magic ${MAGIC}, then remove its lines from ${JOURNAL}.`,
      );
    }
    const request = await acct.requests.wait(entry.request); // lookups only: sends nothing
    note({ key: entry.key, state: request.state, final: isFinal(request) });
    if (!isFinal(request)) stop(`An earlier trade (${request.id}) is still ${request.state}: try again later.`);
    console.log(`An earlier trade settled: ${request.id} ${request.state}`);
  }
}

/** Send one trade with a key saved first, and follow it until it has an outcome. */
async function send(
  acct: Account,
  intent: string,
  call: (key: string) => Promise<TradeRequest>,
): Promise<TradeRequest> {
  const key = newKey();
  note({ key, intent }); // saved before anything is sent
  let request: TradeRequest;
  try {
    request = await call(key);
  } catch (error) {
    if (error instanceof OutcomeUnknown) {
      stop(
        `No answer for '${intent}' after the client's retries: it may have executed. ` +
          `Its key ${key} stays open in ${JOURNAL}; check the account before running again.`,
      );
    }
    if (error instanceof PipsocketError) {
      note({ key, final: true, refused: error.message }); // refused: nothing was sent
      stop(`'${intent}' was refused: ${error.message}`);
    }
    throw error;
  }
  note({ key, request: request.id, state: request.state, final: isFinal(request) });
  if (isOutstanding(request)) {
    console.log(`${request.id} is ${request.state}: no answer from the trade server yet, waiting`);
    request = await acct.requests.wait(request.id); // lookups only: never sends it again
    note({ key, state: request.state, final: isFinal(request) });
  }
  if (!isFinal(request)) stop(`${request.id} is still ${request.state}: it may still execute. Run again later.`);
  return request;
}

// PIPSOCKET_BASE_URL is for tests against a local mock; leave it unset.
const ps = new Pipsocket({ baseUrl: process.env.PIPSOCKET_BASE_URL }); // reads PIPSOCKET_KEY
const acct = ps.account(ACCOUNT);

const account = await acct.get();
console.log(
  `Account ${account.login} on ${account.server}: ${account.status}, balance ${account.balance} ${account.currency}`,
);
if (account.trade_mode !== "demo") {
  stop(
    `This account's trade mode is ${JSON.stringify(account.trade_mode)}. The quickstart trades only on demo accounts.`,
  );
}
if (account.status !== "connected" || !account.trade_allowed) {
  stop("The account isn't ready to trade: it must be connected with trading allowed.");
}

await settleJournal(acct);

// A filling mode and a volume the symbol allows: MT5 rejects an order with any other. A market
// order can use fok or ioc.
const symbol = await acct.symbols.get(SYMBOL);
const filling = (["fok", "ioc"] as const).find((mode) => symbol.filling_modes.includes(mode));
if (!filling) stop(`${SYMBOL} allows neither fok nor ioc (${symbol.filling_modes.join(", ")}): no market order.`);
if (symbol.trading !== "full") stop(`${SYMBOL} can't be bought and sold now (trading: ${symbol.trading}).`);
const volume = symbol.volume_min;
const needsPrice = symbol.execution === "instant" || symbol.execution === "request";

// On a netting account an order on a symbol that already has a position changes that position,
// which this script didn't open.
if (account.margin_mode !== "hedging" && (await acct.positions.list()).some((p) => p.symbol === SYMBOL)) {
  stop(`This is a netting account with a position on ${SYMBOL} already: not touching it.`);
}

const buyPrice = needsPrice ? { price: (await acct.symbols.tick(SYMBOL)).ask, deviation: DEVIATION } : {};
const order = await send(acct, `buy ${volume} ${SYMBOL}`, (key) =>
  acct.orders.place(
    { symbol: SYMBOL, type: "buy", volume, type_filling: filling, magic: MAGIC, ...buyPrice },
    { idempotencyKey: key },
  ),
);

switch (order.kind) {
  case "completed":
  case "placed":
  case "partial": {
    // MT5 took it. A fill has a deal; completed alone isn't one (a pending order placed is too).
    if (order.result.deal === 0) {
      console.log(`${order.state}, but not filled: order ${order.result.order}, no deal`);
      break;
    }
    console.log(`Filled: deal ${order.result.deal}, ${order.result.volume} at ${order.result.price}`);

    // The position this order opened: its identifier is the order's ticket.
    const opened = order.result.position || order.result.order;
    const mine = (await acct.positions.list()).filter((p) => p.identifier === opened && p.magic === MAGIC);
    if (mine.length === 0) console.log("Its position is already closed.");
    for (const position of mine) {
      const sellPrice = needsPrice ? { price: (await acct.symbols.tick(SYMBOL)).bid, deviation: DEVIATION } : {};
      const closed = await send(acct, `close ${position.volume} of position ${position.ticket}`, (key) =>
        acct.positions.close(
          position.ticket,
          // All of it: a partial fill opened less than was asked.
          { volume: position.volume, type_filling: filling, magic: MAGIC, ...sellPrice },
          { idempotencyKey: key },
        ),
      );
      const deal = closed.kind === "completed" || closed.kind === "partial" ? closed.result.deal : 0;
      if (deal !== 0) {
        console.log(`Closed position ${position.ticket}: deal ${deal}`);
      } else {
        console.log(`Position ${position.ticket} not closed: ${closed.state} ${closed.message}`);
      }
    }
    break;
  }
  case "rejected":
    console.log(`Not filled: rejected ${order.result.retcode_name} ${order.message}`);
    break;
  case "executed":
    console.log(`Settled as executed by the account's owner: look for magic ${MAGIC} in positions.`);
    break;
  default:
    console.log(`Not filled: ${order.state} ${order.message}`);
}
