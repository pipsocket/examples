// Read a demo account, buy the smallest volume a symbol allows, handle the outcome, close it.
//
// It trades only on a hedging account whose trade mode is demo, and only ever closes the position
// its own order opened (found by this script's magic number). It won't buy while an earlier run's
// position or order is still on the account.

import {
  appendFileSync,
  closeSync,
  existsSync,
  fsyncSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import {
  type Account,
  type AccountView,
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
const LOCK = "quickstart-journal.lock";

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

/**
 * Stop unless the account can be traded here. Run on a fresh read before every trade: an account can
 * change while the script waits (its owner logs it in elsewhere, say).
 */
function checkAccount(account: AccountView): void {
  if (account.trade_mode !== "demo") {
    stop(
      `This account's trade mode is ${JSON.stringify(account.trade_mode)}. The quickstart trades only on demo accounts.`,
    );
  }
  if (account.margin_mode !== "hedging") {
    // On a netting account a position holds every trade's volume on its symbol, so closing "this
    // script's" position would close other trades too.
    stop("The quickstart trades only hedging accounts.");
  }
  if (`${account.login} on ${account.server}` !== startedOn) {
    stop("The account's login or server changed since the script started: not trading on it.");
  }
  if (account.status !== "connected" || !account.trade_allowed) {
    stop("The account isn't ready to trade: it must be connected with trading allowed.");
  }
}

/**
 * Hold the journal for this run, so two runs never start from the same journal at once. The lock
 * file is created only if it doesn't exist, holds this process's id, and is removed when the
 * script exits (Ctrl+C and kill included). The Python quickstart uses the same file the same way,
 * so the two never share a journal at once either. Only a run killed outright (kill -9) leaves it.
 */
function lockJournal(): void {
  try {
    writeFileSync(LOCK, String(process.pid), { flag: "wx" });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    stop(
      `Another run of the quickstart is using ${JOURNAL} in this folder. If none is running any more, delete ${LOCK}.`,
    );
  }
  process.once("exit", () => rmSync(LOCK, { force: true }));
  process.once("SIGTERM", () => process.exit(143)); // so a plain kill exits cleanly
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
const startedOn = `${account.login} on ${account.server}`;
checkAccount(account);

// Ctrl+C: say what may still execute. The journal keeps it for the next run.
process.once("SIGINT", () => {
  for (const entry of openEntries()) console.error(`May still execute: ${entry.intent} (key ${entry.key})`);
  process.exit(130);
});

lockJournal();
await settleJournal(acct);
// A position or order from an earlier run (one that crashed, or settled late) comes first.
const leftovers = [
  ...(await acct.positions.list()).filter((p) => p.magic === MAGIC).map((p) => p.ticket),
  ...(await acct.orders.list()).filter((o) => o.magic === MAGIC).map((o) => o.ticket),
];
if (leftovers.length > 0) {
  stop(
    `An earlier run left ${leftovers.join(", ")} with magic ${MAGIC} on the account. Close it in the dashboard, then run again.`,
  );
}

// A filling mode and a volume the symbol allows: MT5 rejects an order with any other. A market
// order can use fok or ioc.
const symbol = await acct.symbols.get(SYMBOL);
const filling = (["fok", "ioc"] as const).find((mode) => symbol.filling_modes.includes(mode));
if (!filling) stop(`${SYMBOL} allows neither fok nor ioc (${symbol.filling_modes.join(", ")}): no market order.`);
if (symbol.trading !== "full") stop(`${SYMBOL} can't be bought and sold now (trading: ${symbol.trading}).`);
const volume = symbol.volume_min;
const needsPrice = symbol.execution === "instant" || symbol.execution === "request";

checkAccount(await acct.get());
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
      checkAccount(await acct.get());
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
      if (deal === 0) stop(`Position ${position.ticket} not closed: ${closed.state} ${closed.message}`);
      // A deal can close part of it: read the account again rather than assume.
      const left = (await acct.positions.list()).find((p) => p.ticket === position.ticket);
      if (left) stop(`Position ${position.ticket} is only partly closed: ${left.volume} left.`);
      console.log(`Closed position ${position.ticket}: deal ${deal}`);
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
