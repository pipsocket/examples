// A Cloudflare Worker that reads a Pipsocket account on a schedule (a cron trigger) and logs a
// report: its status, equity and margin, its open positions, and the last day's deals. It only
// reads, so it is safe on any account.

import { AccountUnavailable, Pipsocket, type PositionView } from "pipsocket";

interface Env {
  /** A secret: `npx wrangler secret put PIPSOCKET_KEY`. */
  PIPSOCKET_KEY: string;
  /** The account to report on, set in wrangler.jsonc. */
  PIPSOCKET_ACCOUNT: string;
  /** For tests against a local mock; leave it unset. */
  PIPSOCKET_BASE_URL?: string;
}

const DAY = 86_400;

export default {
  async scheduled(_controller, env): Promise<void> {
    const ps = new Pipsocket({ apiKey: env.PIPSOCKET_KEY, baseUrl: env.PIPSOCKET_BASE_URL || undefined });
    const acct = ps.account(env.PIPSOCKET_ACCOUNT);

    // The account answers even while its terminal is away; positions and history need the terminal.
    const account = await acct.get();
    let positions: PositionView[] | null = null;
    try {
      positions = await acct.positions.list();
    } catch (error) {
      if (!(error instanceof AccountUnavailable)) throw error;
    }

    // History ranges are the broker's server time, not UTC and not the Worker's clock.
    const now = account.server_time;
    const deals =
      typeof now === "number" && positions !== null ? await acct.history.deals({ from: now - DAY, to: now }) : null;

    const report = {
      account: account.id,
      status: account.status,
      trade_mode: account.trade_mode,
      balance: account.balance,
      equity: account.equity,
      // Equity over margin, as a percent; null with no margin used.
      margin_level: account.margin ? Math.round(((account.equity ?? 0) / account.margin) * 10_000) / 100 : null,
      kill_switch: account.kill_switch.engaged,
      // null: unknown, as the terminal isn't running (which is not the same as none).
      positions: positions?.map((p) => `${p.type} ${p.volume} ${p.symbol}`) ?? null,
      deals_last_day: deals?.length ?? null,
      net_last_day:
        deals === null ? null : round(deals.reduce((sum, d) => sum + d.profit + d.commission + d.swap + d.fee, 0)),
    };
    console.log(JSON.stringify(report));
    if (account.status !== "connected") console.warn(`Account ${account.id} is ${account.status}`);
  },
} satisfies ExportedHandler<Env>;

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
