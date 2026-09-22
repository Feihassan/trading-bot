import { useState } from "react";
import { api } from "../api";
import { Badge, ErrorPanel, Panel, Select } from "../components/ui.jsx";
import { usePolling } from "../hooks/usePolling";

const LEVELS = ["", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVEL_TONE = { INFO: "neutral", WARNING: "warn", ERROR: "bad", CRITICAL: "bad" };

export default function SystemLogs() {
  const [level, setLevel] = useState("");
  const { data: logs, error, loading } = usePolling(() => api.getLogs(300, level || undefined), 5000, [level]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-100">System Logs</h1>
        <Select value={level} onChange={(e) => setLevel(e.target.value)}>
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {l || "All levels"}
            </option>
          ))}
        </Select>
      </div>

      {error && <ErrorPanel message={`Could not load logs: ${error}`} />}

      <Panel>
        <div className="max-h-[70vh] space-y-1 overflow-y-auto font-mono text-xs">
          {loading && !logs && <div className="py-6 text-center text-gray-500">Loading…</div>}
          {(logs || []).map((entry, i) => (
            <div key={i} className="flex gap-2 border-b border-gray-900 py-1.5">
              <span className="shrink-0 text-gray-600">{entry.timestamp ? new Date(entry.timestamp).toLocaleString() : ""}</span>
              {entry.level && (
                <span className="shrink-0">
                  <Badge tone={LEVEL_TONE[entry.level] || "neutral"}>{entry.level}</Badge>
                </span>
              )}
              <span className="shrink-0 text-gray-600">{entry.logger}</span>
              <span className="text-gray-300">{entry.message}</span>
            </div>
          ))}
          {logs && logs.length === 0 && <div className="py-6 text-center text-gray-500">No log entries yet.</div>}
        </div>
      </Panel>
    </div>
  );
}
