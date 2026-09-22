import { lazy, Suspense, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Badge } from "./components/ui.jsx";
import { useLiveSocket } from "./hooks/useLiveSocket";
import { usePolling } from "./hooks/usePolling";
import Dashboard from "./pages/Dashboard.jsx";
import Markets from "./pages/Markets.jsx";
import OpenTrades from "./pages/OpenTrades.jsx";
import RiskManagement from "./pages/RiskManagement.jsx";
import Signals from "./pages/Signals.jsx";
import SystemLogs from "./pages/SystemLogs.jsx";
import TradeHistory from "./pages/TradeHistory.jsx";

// Lazy: pulls in recharts (and its own react-redux/@reduxjs/toolkit deps)
// - no reason to load that on every page when only this one uses charts.
const Backtesting = lazy(() => import("./pages/Backtesting.jsx"));

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

function NavList({ onNavigate }) {
  return (
    <ul className="space-y-1">
      {NAV_ITEMS.map((item) => (
        <li key={item.to}>
          <NavLink
            to={item.to}
            end={item.end}
            onClick={onNavigate}
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
  );
}

function StatusBar({ onMenuClick }) {
  const { data: status, error: statusError } = usePolling(api.getStatus, 10000);
  const { data: live, connected } = useLiveSocket();

  const mt5Connected = !statusError && status?.connected;
  const tradingMode = statusError ? "?" : status?.trading_mode || "…";
  const modeTone = tradingMode === "LIVE" ? "bad" : tradingMode === "DEMO" ? "warn" : "neutral";

  return (
    <div className="flex items-center gap-3 border-b border-gray-800 bg-[#0b0f17] px-4 py-3 md:px-6">
      <button
        onClick={onMenuClick}
        aria-label="Toggle navigation menu"
        className="rounded-md border border-gray-700 p-1.5 text-gray-300 hover:bg-gray-800 md:hidden"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
        </svg>
      </button>
      <span className="text-lg font-bold tracking-tight text-gray-100">Trading Bot</span>
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        <Badge tone={mt5Connected ? "good" : "bad"}>{mt5Connected ? "MT5 Connected" : "MT5 Disconnected"}</Badge>
        <Badge tone={modeTone}>{tradingMode}</Badge>
        <Badge tone={connected ? "good" : "warn"}>{connected ? "Live feed" : "Reconnecting…"}</Badge>
        {live?.account && (
          <span className="ml-2 hidden text-sm text-gray-400 sm:inline">
            Equity <span className="font-semibold text-gray-200">${Number(live.account.equity).toLocaleString()}</span>
          </span>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex h-screen flex-col">
      <StatusBar onMenuClick={() => setNavOpen((v) => !v)} />
      <div className="flex min-h-0 flex-1">
        {navOpen && (
          <div className="fixed inset-0 z-30 md:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setNavOpen(false)} />
            <nav className="absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-gray-800 bg-[#0b0f17] p-3">
              <NavList onNavigate={() => setNavOpen(false)} />
            </nav>
          </div>
        )}
        <nav className="hidden w-56 shrink-0 overflow-y-auto border-r border-gray-800 bg-[#0b0f17] p-3 md:block">
          <NavList />
        </nav>
        <main className="min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/markets" element={<Markets />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/open-trades" element={<OpenTrades />} />
            <Route path="/history" element={<TradeHistory />} />
            <Route
              path="/backtesting"
              element={
                <Suspense fallback={<div className="text-sm text-gray-500">Loading…</div>}>
                  <Backtesting />
                </Suspense>
              }
            />
            <Route path="/risk" element={<RiskManagement />} />
            <Route path="/logs" element={<SystemLogs />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
