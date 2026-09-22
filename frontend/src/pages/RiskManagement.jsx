import { useState } from "react";
import { api } from "../api";
import { Badge, ErrorPanel, fmtPct, Panel, StatCard } from "../components/ui.jsx";
import { usePolling } from "../hooks/usePolling";

export default function RiskManagement() {
  const { data: risk, error, refresh } = usePolling(api.getRisk, 5000);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("Manual stop via dashboard");

  async function handleTrip() {
    setBusy(true);
    try {
      await api.tripEmergencyStop(reason);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    setBusy(true);
    try {
      await api.resetEmergencyStop();
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Risk Management</h1>
      {error && <ErrorPanel message={`Could not load risk settings: ${error}`} />}

      <Panel
        title="Emergency Stop"
        right={<Badge tone={risk?.emergency_stop_active ? "bad" : "good"}>{risk?.emergency_stop_active ? "ACTIVE" : "Not active"}</Badge>}
      >
        <p className="mb-3 text-sm text-gray-400">
          When active, the risk manager rejects every new trade regardless of signal quality. Existing open
          positions are left untouched — this only blocks new entries.
        </p>
        {risk?.emergency_stop_active && risk.emergency_stop_reason && (
          <div className="mb-3 rounded-lg border border-red-800/50 bg-red-950/30 p-3 text-sm text-red-300">
            {risk.emergency_stop_reason}
          </div>
        )}
        <div className="flex items-center gap-3">
          {!risk?.emergency_stop_active ? (
            <>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="w-80 rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
                placeholder="Reason for stopping"
              />
              <button
                onClick={handleTrip}
                disabled={busy}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
              >
                Trip Emergency Stop
              </button>
            </>
          ) : (
            <button
              onClick={handleReset}
              disabled={busy}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              Reset (Resume Trading)
            </button>
          )}
        </div>
      </Panel>

      <Panel title="Configured Limits">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="Risk per Trade" value={risk ? fmtPct(risk.risk_per_trade) : "—"} />
          <StatCard label="Max Daily Loss" value={risk ? fmtPct(risk.max_daily_loss) : "—"} />
          <StatCard label="Max Weekly Loss" value={risk ? fmtPct(risk.max_weekly_loss) : "—"} />
          <StatCard label="Max Drawdown Halt" value={risk ? fmtPct(risk.max_drawdown_halt) : "—"} />
          <StatCard label="Max Open Trades" value={risk?.max_open_trades ?? "—"} />
          <StatCard label="Max Trades / Day" value={risk?.max_trades_per_day ?? "—"} />
          <StatCard label="Max Exposure / Currency" value={risk ? fmtPct(risk.max_exposure_per_currency) : "—"} />
          <StatCard label="Max Spread (pts)" value={risk?.max_spread_points ?? "—"} />
          <StatCard label="Min Risk:Reward" value={risk ? `1:${risk.min_risk_reward}` : "—"} />
          <StatCard label="Trading Sessions" value={risk?.trading_sessions?.length ? risk.trading_sessions.join(", ") : "Unrestricted"} />
        </div>
        <p className="mt-4 text-xs text-gray-600">
          These are read from <code>.env</code> at process start. Change values there and restart the API to update
          them — the risk manager never allows a signal to bypass these limits (spec section 12).
        </p>
      </Panel>
    </div>
  );
}
