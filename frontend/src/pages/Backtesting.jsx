import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, SYMBOLS } from "../api";
import { ErrorPanel, fmtNumber, fmtPct, Panel, StatCard } from "../components/ui.jsx";

const TIMEFRAMES = ["M15", "M30", "H1", "H4", "D1"];

export default function Backtesting() {
  const [form, setForm] = useState({
    symbol: "EURUSD",
    timeframe: "H1",
    days: 365,
    initial_balance: 10000,
    risk_per_trade: 0.01,
    min_risk_reward: 2.0,
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function runBacktest() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.runBacktest(form);
      setResult(res);
    } catch (err) {
      setError(err.message || String(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Backtesting</h1>
      <p className="text-sm text-gray-500">
        Runs the Phase 3 engine against real historical MT5 data using the baseline EMA-crossover + regime-filter
        strategy. This tests whether a strategy has statistical edge — it is not a promise that the past predicts
        the future.
      </p>

      <Panel>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
          <Field label="Symbol">
            <select value={form.symbol} onChange={(e) => update("symbol", e.target.value)} className={inputClass}>
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Timeframe">
            <select value={form.timeframe} onChange={(e) => update("timeframe", e.target.value)} className={inputClass}>
              {TIMEFRAMES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Days of history">
            <input type="number" value={form.days} onChange={(e) => update("days", Number(e.target.value))} className={inputClass} />
          </Field>
          <Field label="Initial balance">
            <input
              type="number"
              value={form.initial_balance}
              onChange={(e) => update("initial_balance", Number(e.target.value))}
              className={inputClass}
            />
          </Field>
          <Field label="Risk / trade">
            <input
              type="number"
              step="0.001"
              value={form.risk_per_trade}
              onChange={(e) => update("risk_per_trade", Number(e.target.value))}
              className={inputClass}
            />
          </Field>
          <Field label="Min R:R">
            <input
              type="number"
              step="0.1"
              value={form.min_risk_reward}
              onChange={(e) => update("min_risk_reward", Number(e.target.value))}
              className={inputClass}
            />
          </Field>
        </div>
        <button
          onClick={runBacktest}
          disabled={loading}
          className="mt-4 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? "Running backtest…" : "Run Backtest"}
        </button>
      </Panel>

      {error && <ErrorPanel message={error} />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="Total Trades" value={result.total_trades} sub={`${result.bars_used} bars used`} />
            <StatCard label="Win Rate" value={fmtPct(result.win_rate)} sub={`${result.winning_trades}W / ${result.losing_trades}L`} />
            <StatCard
              label="Profit Factor"
              value={result.profit_factor < 0 ? "∞" : result.profit_factor.toFixed(2)}
              tone={result.profit_factor >= 1.5 ? "good" : result.profit_factor < 1 ? "bad" : "warn"}
            />
            <StatCard
              label="Net Return"
              value={fmtPct(result.net_return_pct)}
              tone={result.net_return_pct > 0 ? "good" : "bad"}
            />
            <StatCard label="Expectancy (R)" value={result.expectancy_r.toFixed(2)} />
            <StatCard label="Max Drawdown" value={fmtPct(result.max_drawdown_pct)} tone="warn" />
            <StatCard label="Sharpe" value={result.sharpe_ratio !== null ? result.sharpe_ratio.toFixed(2) : "n/a"} />
            <StatCard label="Longest Losing Streak" value={result.longest_losing_streak} />
          </div>

          <Panel title="Equity Curve">
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={result.equity_curve}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="time" tick={{ fontSize: 10, fill: "#6b7280" }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10, fill: "#6b7280" }} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ background: "#121826", border: "1px solid #1f2937", fontSize: 12 }} />
                <Line type="stepAfter" dataKey="equity" stroke="#6366f1" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </Panel>

          <Panel title={`Trades (${result.trades.length})`}>
            <div className="max-h-96 overflow-y-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800 text-left text-xs text-gray-500 uppercase">
                    <th className="py-2">Direction</th>
                    <th className="py-2">Entry</th>
                    <th className="py-2">Exit</th>
                    <th className="py-2 text-right">PnL</th>
                    <th className="py-2 text-right">R</th>
                    <th className="py-2">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => (
                    <tr key={i} className="border-b border-gray-900">
                      <td className="py-1.5">{t.direction}</td>
                      <td className="py-1.5 font-mono text-xs">{fmtNumber(t.entry_price, 5)}</td>
                      <td className="py-1.5 font-mono text-xs">{fmtNumber(t.exit_price, 5)}</td>
                      <td className={`py-1.5 text-right font-mono ${t.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {fmtNumber(t.pnl)}
                      </td>
                      <td className="py-1.5 text-right font-mono">{t.r_multiple.toFixed(2)}</td>
                      <td className="py-1.5 text-xs text-gray-500">{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

const inputClass = "rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 w-full";

function Field({ label, children }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-gray-500 uppercase">{label}</label>
      {children}
    </div>
  );
}
