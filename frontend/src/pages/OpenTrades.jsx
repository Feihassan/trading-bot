import { api } from "../api";
import { Badge, directionTone, ErrorPanel, fmtNumber, LoadingRow, Panel } from "../components/ui.jsx";
import { usePolling } from "../hooks/usePolling";

export default function OpenTrades() {
  const { data: positions, error, loading } = usePolling(api.getPositions, 4000);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Open Trades</h1>
      {error && <ErrorPanel message={`Could not load positions: ${error}`} />}

      <Panel title={`Open positions ${positions ? `(${positions.length})` : ""}`}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-left text-xs text-gray-500 uppercase">
              <th className="py-2">Ticket</th>
              <th className="py-2">Symbol</th>
              <th className="py-2">Type</th>
              <th className="py-2 text-right">Volume</th>
              <th className="py-2 text-right">Open</th>
              <th className="py-2 text-right">Current</th>
              <th className="py-2 text-right">SL</th>
              <th className="py-2 text-right">TP</th>
              <th className="py-2 text-right">Profit</th>
            </tr>
          </thead>
          <tbody>
            {loading && !positions && <LoadingRow colSpan={9} />}
            {positions && positions.length === 0 && (
              <tr>
                <td colSpan={9} className="py-6 text-center text-sm text-gray-500">
                  No open positions.
                </td>
              </tr>
            )}
            {(positions || []).map((p) => (
              <tr key={p.ticket} className="border-b border-gray-900 hover:bg-gray-900/40">
                <td className="py-2 font-mono text-xs text-gray-500">{p.ticket}</td>
                <td className="py-2 font-medium text-gray-100">{p.symbol}</td>
                <td className="py-2">
                  <Badge tone={directionTone(p.type)}>{p.type}</Badge>
                </td>
                <td className="py-2 text-right font-mono">{fmtNumber(p.volume, 2)}</td>
                <td className="py-2 text-right font-mono">{fmtNumber(p.price_open, 5)}</td>
                <td className="py-2 text-right font-mono">{fmtNumber(p.price_current, 5)}</td>
                <td className="py-2 text-right font-mono text-gray-400">{fmtNumber(p.sl, 5)}</td>
                <td className="py-2 text-right font-mono text-gray-400">{fmtNumber(p.tp, 5)}</td>
                <td className={`py-2 text-right font-mono ${p.profit >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {p.profit >= 0 ? "+" : ""}
                  {fmtNumber(p.profit)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
