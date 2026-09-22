import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { ErrorPanel, fmtNumber, LoadingRow, Panel } from "../components/ui.jsx";
import { useLiveSocket } from "../hooks/useLiveSocket";
import { usePolling } from "../hooks/usePolling";

export default function Markets() {
  const { data: symbols, error, loading } = usePolling(api.getSymbols, 5000);
  const { data: live } = useLiveSocket();
  const navigate = useNavigate();

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Markets</h1>
      {error && <ErrorPanel message={`Could not load symbols: ${error}`} />}

      <Panel title="Live quotes">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-left text-xs text-gray-500 uppercase">
              <th className="py-2">Symbol</th>
              <th className="py-2">Broker Symbol</th>
              <th className="py-2 text-right">Bid</th>
              <th className="py-2 text-right">Ask</th>
              <th className="py-2 text-right">Spread (pts)</th>
              <th className="py-2 text-right"></th>
            </tr>
          </thead>
          <tbody>
            {loading && !symbols && <LoadingRow colSpan={6} />}
            {(symbols || []).map((s) => {
              const liveQuote = live?.quotes?.[s.symbol];
              const bid = liveQuote?.bid ?? s.bid;
              const ask = liveQuote?.ask ?? s.ask;
              return (
                <tr key={s.symbol} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-2 font-medium text-gray-100">{s.symbol}</td>
                  <td className="py-2 text-gray-500">{s.broker_symbol}</td>
                  <td className="py-2 text-right font-mono text-gray-200">{fmtNumber(bid, 5)}</td>
                  <td className="py-2 text-right font-mono text-gray-200">{fmtNumber(ask, 5)}</td>
                  <td className="py-2 text-right text-gray-400">{fmtNumber(s.spread_points, 1)}</td>
                  <td className="py-2 text-right">
                    <button
                      onClick={() => navigate(`/signals?symbol=${s.symbol}`)}
                      className="rounded-md border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800"
                    >
                      View signal
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
