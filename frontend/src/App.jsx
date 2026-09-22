import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Badge } from "./components/ui.jsx";
import { useLiveSocket } from "./hooks/useLiveSocket";
import Backtesting from "./pages/Backtesting.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Markets from "./pages/Markets.jsx";
import OpenTrades from "./pages/OpenTrades.jsx";
import RiskManagement from "./pages/RiskManagement.jsx";
import Signals from "./pages/Signals.jsx";
import SystemLogs from "./pages/SystemLogs.jsx";
import TradeHistory from "./pages/TradeHistory.jsx";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/markets", label: "Markets" },
  { to: "/signals", label: "Signals" },
  { to: "/open-trades", label: "Open Trades" },
  { to: "/history", label: "Trade History" },
  { to: "/backtesting", label: "Backtesting" },
  { to: "/risk", label: "Risk Management" },
  { to: "/logs", label: "System Logs" },
];

function StatusBar() {
  const [status, setStatus] = useState(null);
  const { data: live, connected } = useLiveSocket();

  useEffect(() => {
    let mounted = true;
    async function poll() {
      try {
        const s = await api.getStatus();
        if (mounted) setStatus(s);
      } catch {
        if (mounted) setStatus({ connected: false, trading_mode: "?", error: "API unreachable" });
      }
    }
    poll();
    const id = setInterval(poll, 10000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  const modeTone = status?.trading_mode === "LIVE" ? "bad" : status?.trading_mode === "DEMO" ? "warn" : "neutral";

  return (
    <div className="flex items-center gap-3 border-b border-gray-800 bg-[#0b0f17] px-6 py-3">
      <span className="text-lg font-bold tracking-tight text-gray-100">Trading Bot</span>
      <div className="ml-auto flex items-center gap-2">
        <Badge tone={status?.connected ? "good" : "bad"}>{status?.connected ? "MT5 Connected" : "MT5 Disconnected"}</Badge>
        <Badge tone={modeTone}>{status?.trading_mode || "…"}</Badge>
        <Badge tone={connected ? "good" : "warn"}>{connected ? "Live feed" : "Reconnecting…"}</Badge>
        {live?.account && (
          <span className="ml-2 text-sm text-gray-400">
            Equity <span className="font-semibold text-gray-200">${Number(live.account.equity).toLocaleString()}</span>
          </span>
        )}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <div className="flex h-screen flex-col">
      <StatusBar />
      <div className="flex min-h-0 flex-1">
        <nav className="w-56 shrink-0 border-r border-gray-800 bg-[#0b0f17] p-3">
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `block rounded-lg px-3 py-2 text-sm font-medium ${
                      isActive ? "bg-gray-800 text-white" : "text-gray-400 hover:bg-gray-800/50 hover:text-gray-200"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/markets" element={<Markets />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/open-trades" element={<OpenTrades />} />
            <Route path="/history" element={<TradeHistory />} />
            <Route path="/backtesting" element={<Backtesting />} />
            <Route path="/risk" element={<RiskManagement />} />
            <Route path="/logs" element={<SystemLogs />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
