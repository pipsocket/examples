// Prices, fills and the account as they happen, through the SDK's stream helper.
//
// The helper keeps the socket open, reconnects, and after every connect and every gap hands you a
// `resync` event: a fresh baseline read over REST. This script keeps its own copy of the open
// positions and pending orders, replaces it on each resync, and treats every other event as a
// notice. It sends no trades. Stop it with Ctrl+C.

import { type DealView, isEvent, type PendingOrderView, Pipsocket, type PositionView, toTradeRequest } from "pipsocket";

const ACCOUNT = process.env.PIPSOCKET_ACCOUNT;
if (!ACCOUNT) throw new Error("Set PIPSOCKET_ACCOUNT (see .env.example).");
// The symbols to stream prices for, exactly as your broker names them, separated by commas.
const SYMBOLS = (process.env.PIPSOCKET_SYMBOLS ?? "EURUSD").split(",");

// PIPSOCKET_BASE_URL and PIPSOCKET_STREAM_URL are for tests against a local mock; leave them unset.
const ps = new Pipsocket({ baseUrl: process.env.PIPSOCKET_BASE_URL, streamUrl: process.env.PIPSOCKET_STREAM_URL });
const stream = ps.account(ACCOUNT).stream({ ticks: SYMBOLS }); // on Node it needs the ws package
process.once("SIGINT", () => stream.close()); // Ctrl+C ends the loop below

// The account as the last resync showed it. null means unknown (the terminal isn't running), which
// is not the same as empty.
let positions: Map<number, PositionView> | null = null;
let orders: Map<number, PendingOrderView> | null = null;
const dealsSeen = new Set<number>();

for await (const event of stream) {
  if (isEvent(event, "resync")) {
    // The new baseline: replace what we hold, never add events to it.
    positions = event.positions && new Map(event.positions.map((p) => [p.ticket, p]));
    orders = event.orders && new Map(event.orders.map((o) => [o.ticket, o]));
    console.log(
      `resync (${event.reason}): ${positions?.size ?? "unknown"} positions, ` +
        `${orders?.size ?? "unknown"} pending orders, equity ${event.account?.equity}`,
    );
    if (event.reason !== "connected") {
      console.log(
        "  after a gap: ticks and events from the gap are not sent again; the snapshot and the history below cover what changed",
      );
    }
    if (event.history === null) {
      if (event.history_reason !== null) console.log(`  no history read back (${event.history_reason})`);
    } else {
      for (const deal of event.history.deals) {
        if (dealsSeen.has(deal.ticket)) continue;
        dealsSeen.add(deal.ticket);
        console.log(`  deal from history: ${describe(deal)}`);
      }
    }
  } else if (isEvent(event, "tick")) {
    console.log(`${event.symbol} bid ${event.bid} ask ${event.ask}`);
  } else if (isEvent(event, "deal")) {
    // A notice: it may be older than the baseline, so don't apply it to the positions above. Read
    // them again (acct.positions.list()) when it matters.
    if (!dealsSeen.has(event.deal.ticket)) {
      dealsSeen.add(event.deal.ticket);
      console.log(`deal: ${describe(event.deal)}`);
    }
  } else if (isEvent(event, "request")) {
    const request = toTradeRequest(event);
    console.log(`request ${request.id} settled: ${request.state}`);
  } else if (isEvent(event, "status")) {
    console.log(`account status: ${event.status}`);
  }
  // account (every second), transaction, and types added later: ignored here.
}
console.log("stopped");

function describe(deal: DealView): string {
  return `${deal.ticket} ${deal.type} ${deal.volume} ${deal.symbol} at ${deal.price} (${deal.entry}, profit ${deal.profit})`;
}
