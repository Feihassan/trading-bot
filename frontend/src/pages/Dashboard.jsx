import { api } from "../api";
import { ErrorPanel, fmtNumber, fmtPct, Panel, StatCard } from "../components/ui.jsx";
import { usePolling } from "../hooks/usePolling";
import { useLiveSocket } from "../hooks/useLiveSocket";

export default function Dashboard() {
  const { data: account, error: accountError, loading: accountLoading } = usePolling(api.getAccount, 5000);
  const { data: positions } = usePolling(api.getPositions, 5000);
  const { data: journal } = usePolling(() => api.getJournal(20), 8000);
  const { data: live } = useLiveSocket();

  const equity = live?.account?.equity ?? account?.equity;
  const dailyPnl = live?.account?.daily_pnl ?? account?.daily_pnl;

  const executedToday = (journal || []).filter((e) => e.executed).length;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Dashboard</h1>

      {accountError && <ErrorPanel message={`Account data unavailable: ${accountError}`} />}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          label="Balance"
          value={account ? `$${fmtNumber(account.balance)}` : "—"}
          sub={account?.currency}
          loading={accountLoading && !account}
        />
        <StatCard
          label="Equity"
          value={equity !== undefined ? `$${fmtNumber(equity)}` : "—"}
          loading={accountLoading && equity === undefined}
        />
        <StatCard
          label="Today's P/L"
          value={dailyPnl !== undefined ? `${dailyPnl >= 0 ? "+" : ""}$${fmtNumber(dailyPnl)}` : "—"}
          sub={account ? fmtPct(account.daily_pnl_pct) : undefined}
          tone={dailyPnl > 0 ? "good" : dailyPnl < 0 ? "bad" : "neutral"}
          loading={accountLoading && dailyPnl === undefined}
        />
        <StatCard
          label="Drawdown from Peak"
          value={account ? fmtPct(account.drawdown_from_peak_pct) : "—"}
          tone={account && account.drawdown_from_peak_pct > 0.05 ? "warn" : "neutral"}
          loading={accountLoading && !account}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Open Positions" value={positions ? positions.length : accountLoading ? "…" : "0"} />
        <StatCard label="Trades Recorded Today" value={executedToday} sub="from local trade journal" />
        <StatCard label="Margin Used" value={account ? `$${fmtNumber(account.margin)}` : "—"} loading={accountLoading && !account} />
        <StatCard
          label="Free Margin"
          value={account ? `$${fmtNumber(account.margin_free)}` : "—"}
          loading={accountLoading && !account}
        />
      </div>

      <Panel title="About this dashboard">
        <p className="text-sm text-gray-400">
          All figures above come from the live MT5 account configured in <code className="text-gray-300">.env</code>{" "}
          — nothing here is simulated. Historical win-rate / profit-factor style performance metrics require
          closed-trade analytics that aren't wired to a database yet (see README known limitations); for now, use
          the <span className="text-gray-300">Backtesting</span> page to evaluate strategy performance against real
          historical data, and <span className="text-gray-300">Trade History</span> for a raw log of every decision
          this bot has made.
        </p>
      </Panel>
    </div>
  );
}
