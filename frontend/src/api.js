const BASE = "/api";

// Only needed when the API server is bound off loopback (see the backend's
// API_HOST/API_KEY docs) - set VITE_API_KEY at frontend build time to match.
// Empty/undefined is the normal local-dev case and adds no header.
export const API_KEY = import.meta.env.VITE_API_KEY || "";

async function request(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
    },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // ignore parse failure, keep statusText
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  getStatus: () => request("/status"),
  getAccount: () => request("/account"),
  getPositions: () => request("/positions"),
  getSymbols: () => request("/symbols"),
  getSignal: (symbol, useMl = false) => request(`/signals/${symbol}?use_ml=${useMl}`),
  getJournal: (limit = 100) => request(`/journal?limit=${limit}`),
  getRisk: () => request("/risk"),
  tripEmergencyStop: (reason) =>
    request("/risk/emergency-stop", { method: "POST", body: JSON.stringify({ reason }) }),
  resetEmergencyStop: () => request("/risk/reset-stop", { method: "POST" }),
  runBacktest: (params) => request("/backtest", { method: "POST", body: JSON.stringify(params) }),
  getLogs: (limit = 200, level) => request(`/logs?limit=${limit}${level ? `&level=${level}` : ""}`),
};

export const SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD", "XAUUSD"];
