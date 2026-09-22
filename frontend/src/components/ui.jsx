export function Panel({ title, right, children, className = "" }) {
  return (
    <div className={`rounded-xl border border-gray-800 bg-[#121826] p-4 ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between">
          {title && <h2 className="text-sm font-semibold tracking-wide text-gray-300 uppercase">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function StatCard({ label, value, sub, tone = "neutral" }) {
  const toneClass = {
    neutral: "text-gray-100",
    good: "text-emerald-400",
    bad: "text-red-400",
    warn: "text-amber-400",
  }[tone];

  return (
    <div className="rounded-xl border border-gray-800 bg-[#121826] p-4">
      <div className="text-xs font-medium tracking-wide text-gray-500 uppercase">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${toneClass}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }) {
  const toneClass = {
    neutral: "bg-gray-700/50 text-gray-300 border-gray-600",
    buy: "bg-emerald-500/10 text-emerald-400 border-emerald-500/40",
    sell: "bg-red-500/10 text-red-400 border-red-500/40",
    wait: "bg-slate-500/10 text-slate-400 border-slate-500/40",
    good: "bg-emerald-500/10 text-emerald-400 border-emerald-500/40",
    bad: "bg-red-500/10 text-red-400 border-red-500/40",
    warn: "bg-amber-500/10 text-amber-400 border-amber-500/40",
  }[tone];

  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${toneClass}`}>
      {children}
    </span>
  );
}

export function directionTone(direction) {
  if (direction === "BUY") return "buy";
  if (direction === "SELL") return "sell";
  return "wait";
}

export function fmtNumber(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtPct(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(decimals)}%`;
}

export function LoadingRow({ colSpan = 1 }) {
  return (
    <tr>
      <td colSpan={colSpan} className="py-6 text-center text-sm text-gray-500">
        Loading…
      </td>
    </tr>
  );
}

export function ErrorPanel({ message }) {
  return (
    <div className="rounded-xl border border-red-800/50 bg-red-950/30 p-4 text-sm text-red-300">
      {message}
    </div>
  );
}
