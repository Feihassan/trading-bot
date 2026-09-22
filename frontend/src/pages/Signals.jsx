import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, SYMBOLS } from "../api";
import { Badge, directionTone, ErrorPanel, fmtNumber, Panel } from "../components/ui.jsx";

export default function Signals() {
  const [params, setParams] = useSearchParams();
  const [symbol, setSymbol] = useState(params.get("symbol") || SYMBOLS[0]);
  const [useMl, setUseMl] = useState(false);
  const [signal, setSignal] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function fetchSignal() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.getSignal(symbol, useMl);
      setSignal(result);
    } catch (err) {
      setError(err.message || String(err));
      setSignal(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Signals</h1>

      <Panel>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1 block text-xs text-gray-500 uppercase">Symbol</label>
            <select
              value={symbol}
              onChange={(e) => {
                setSymbol(e.target.value);
                setParams({ symbol: e.target.value });
              }}
              className="rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
            >
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm text-gray-400">
            <input type="checkbox" checked={useMl} onChange={(e) => setUseMl(e.target.checked)} />
            Include ML confirmation (trains/loads a model — slower first request)
          </label>
          <button
            onClick={fetchSignal}
            disabled={loading}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {loading ? "Analyzing…" : "Get Signal"}
          </button>
        </div>
      </Panel>

      {error && <ErrorPanel message={error} />}

      {signal && <SignalCard signal={signal} />}
    </div>
  );
}

function SignalCard({ signal }) {
  const tone = directionTone(signal.direction);
  return (
    <Panel>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-lg font-bold text-gray-100">{signal.symbol}</div>
          <div className="mt-1">
            <Badge tone={tone}>{signal.direction}</Badge>
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-gray-500 uppercase">Confidence</div>
          <div className="text-2xl font-semibold text-gray-100">{signal.confidence.toFixed(0)}%</div>
        </div>
      </div>

      {signal.direction !== "WAIT" ? (
        <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
          <Field label="Entry" value={fmtNumber(signal.entry, 5)} />
          <Field label="Stop Loss" value={fmtNumber(signal.stop_loss, 5)} />
          <Field label="Take Profit" value={fmtNumber(signal.take_profit, 5)} />
          <Field label="R:R" value={signal.reward_risk ? `1:${signal.reward_risk.toFixed(2)}` : "—"} />
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-slate-700/50 bg-slate-900/40 p-3 text-sm text-slate-400">
          No trade recommended right now.
          {signal.blocking_reasons.length > 0 && (
            <ul className="mt-2 list-inside list-disc space-y-1">
              {signal.blocking_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-3">
        <Field label="Technical" value={`${signal.technical_direction} (${signal.technical_score.toFixed(0)})`} />
        <Field label="Structure" value={signal.structure_trend} />
        <Field label="Regime" value={signal.regime} />
      </div>

      {signal.ml_probabilities && (
        <div className="mt-4">
          <div className="mb-1 text-xs text-gray-500 uppercase">ML Probabilities</div>
          <div className="flex gap-4 text-sm">
            <span className="text-emerald-400">BUY {(signal.ml_probabilities.BUY * 100).toFixed(0)}%</span>
            <span className="text-slate-400">NEUTRAL {(signal.ml_probabilities.NEUTRAL * 100).toFixed(0)}%</span>
            <span className="text-red-400">SELL {(signal.ml_probabilities.SELL * 100).toFixed(0)}%</span>
          </div>
        </div>
      )}

      <div className="mt-4">
        <div className="mb-1 text-xs text-gray-500 uppercase">Reasons</div>
        <ul className="space-y-1 text-sm text-gray-300">
          {signal.reasons.map((r, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-emerald-500">✓</span>
              {r}
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-4 text-xs text-gray-600">As of {signal.timestamp || "—"}</div>
    </Panel>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <div className="text-xs text-gray-500 uppercase">{label}</div>
      <div className="font-mono text-sm text-gray-200">{value}</div>
    </div>
  );
}
