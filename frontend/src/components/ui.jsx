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

export function StatCard({ label, value, sub, tone = "neutral", loading = false }) {
  const toneClass = {
    neutral: "text-gray-100",
    good: "text-emerald-400",
    bad: "text-red-400",
    warn: "text-amber-400",
  }[tone];

  return (
    <div className="rounded-xl border border-gray-800 bg-[#121826] p-4">
      <div className="text-xs font-medium tracking-wide text-gray-500 uppercase">{label}</div>
      {loading ? (
        <div className="mt-2 h-7 w-20 animate-pulse rounded bg-gray-800" />
      ) : (
        <div className={`mt-1 text-2xl font-semibold ${toneClass}`}>{value}</div>
      )}
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }) {
  const toneClass = {
    neutral: "bg-gray-700/50 text-gray-300 border-gray-600",
    buy: "bg-[var(--color-buy)]/10 text-[var(--color-buy)] border-[var(--color-buy)]/40",
    sell: "bg-[var(--color-sell)]/10 text-[var(--color-sell)] border-[var(--color-sell)]/40",
    wait: "bg-[var(--color-wait)]/10 text-[var(--color-wait)] border-[var(--color-wait)]/40",
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

const fieldClass =
  "rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 focus:border-indigo-500 focus:outline-none";

export function Select({ className = "", ...props }) {
  return <select className={`${fieldClass} ${className}`} {...props} />;
}

export function TextInput({ className = "", ...props }) {
  return <input className={`${fieldClass} ${className}`} {...props} />;
}

const buttonVariants = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-500",
  danger: "bg-red-600 text-white hover:bg-red-500",
  success: "bg-emerald-600 text-white hover:bg-emerald-500",
  ghost: "border border-gray-700 text-gray-300 hover:bg-gray-800",
};

export function Button({ variant = "primary", className = "", ...props }) {
  return (
    <button
      className={`rounded-md px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${buttonVariants[variant]} ${className}`}
      {...props}
    />
  );
}

export function TableContainer({ children }) {
  return <div className="overflow-x-auto">{children}</div>;
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
