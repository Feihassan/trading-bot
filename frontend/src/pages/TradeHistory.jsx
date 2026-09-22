import { api } from "../api";
import { Badge, directionTone, ErrorPanel, fmtNumber, LoadingRow, Panel, TableContainer } from "../components/ui.jsx";
import { usePolling } from "../hooks/usePolling";

export default function TradeHistory() {
  const { data: entries, error, loading } = usePolling(() => api.getJournal(200), 8000);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Trade History</h1>
      <p className="text-sm text-gray-500">
        Every signal the execution engine has evaluated — approved, rejected, dry-run, or failed — straight from the
        trade journal. This is the full explainability record required by design: nothing is decided without a
        reason recorded here.
      </p>
      {error && <ErrorPanel message={`Could not load journal: ${error}`} />}

      <Panel>
        <TableContainer>
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-left text-xs text-gray-500 uppercase">
              <th className="py-2">Time</th>
              <th className="py-2">Symbol</th>
              <th className="py-2">Direction</th>
              <th className="py-2">Outcome</th>
              <th className="py-2 text-right">Volume</th>
              <th className="py-2 text-right">Risk $</th>
              <th className="py-2">Detail</th>
            </tr>
          </thead>
          <tbody>
            {loading && !entries && <LoadingRow colSpan={7} />}
            {entries && entries.length === 0 && (
              <tr>
                <td colSpan={7} className="py-6 text-center text-sm text-gray-500">
                  No journal entries yet — the journal fills in as the executor evaluates signals.
                </td>
              </tr>
            )}
            {(entries || []).map((e, i) => (
              <tr key={i} className="border-b border-gray-900 align-top hover:bg-gray-900/40">
                <td className="py-2 whitespace-nowrap text-gray-500">{new Date(e.timestamp).toLocaleString()}</td>
                <td className="py-2 font-medium text-gray-100">{e.symbol || "—"}</td>
                <td className="py-2">
                  <Badge tone={directionTone(e.direction)}>{e.direction || "—"}</Badge>
                </td>
                <td className="py-2">
                  {e.executed ? (
                    <Badge tone="good">Executed</Badge>
                  ) : e.dry_run ? (
                    <Badge tone="warn">Dry-run</Badge>
                  ) : (
                    <Badge tone="bad">Skipped</Badge>
                  )}
                </td>
                <td className="py-2 text-right font-mono">{e.volume ? fmtNumber(e.volume, 2) : "—"}</td>
                <td className="py-2 text-right font-mono">{e.risk_amount ? fmtNumber(e.risk_amount) : "—"}</td>
                <td className="py-2 max-w-md text-xs text-gray-500">
                  {e.skipped_reason || (e.reasons ? e.reasons.join("; ") : "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableContainer>
      </Panel>
    </div>
  );
}
