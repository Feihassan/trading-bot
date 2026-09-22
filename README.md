# AI-Assisted Forex Trading Bot (MT5)

A modular, risk-first trading system for MetaTrader 5. It analyzes live
market data, scores trade setups using technical analysis + market
structure + regime detection + an ML probability model, and — once you
explicitly enable it — executes trades through MT5 under strict,
non-bypassable risk controls.

**This system does not predict the market with certainty and is not
guaranteed to be profitable.** Its job is to let you objectively test
whether a strategy has a statistical edge, control risk mechanically, and
execute reliably. Capital preservation always takes priority over taking a
trade.

Status: under active phased build. See [Project status](#project-status)
for what's implemented so far.

## Safety model (read this first)

- Default `TRADING_MODE` is `DRY_RUN`: the bot generates and logs signals
  but **never calls the MT5 order API**.
- `DEMO` mode places real orders, but the execution engine (Phase 6) will
  refuse to run against an account MT5 reports as anything but a demo.
- `LIVE` mode requires **two** separate settings to agree:
  `TRADING_MODE=LIVE` *and* `LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND_THE_RISK`.
  Missing either one, config validation raises an error and the bot refuses
  to start. This is enforced in [`app/config.py`](app/config.py) and is
  covered by tests in [`tests/test_config.py`](tests/test_config.py).
- The risk manager (Phase 5) is designed so no signal, ML output, or LLM
  suggestion can bypass it — it is the last gate before any order is sent.

## Requirements

- Windows (the `MetaTrader5` Python package only works on Windows, talking
  to a local MT5 terminal over IPC — there is no Mac/Linux equivalent).
- Python 3.11+ (this repo was set up with 3.12).
- A MetaTrader 5 terminal installed and logged into a **demo** account to
  start with.

## Setup

### 1. Install the MT5 terminal and open a demo account

1. Download and install the MetaTrader 5 terminal from your broker (or the
   generic installer at metatrader5.com if you don't have a broker yet).
2. In the terminal: *File → Open an Account* → choose your broker server →
   select **"New demo account"** → fill in the form. Note down:
   - Login (account number)
   - Password (the *investor* password won't allow trading — use the main
     password)
   - Server name (exactly as shown, e.g. `ICMarketsSC-Demo`)
3. Leave the terminal running and logged in (simplest setup), or note its
   install path if you want the bot to launch it itself via `MT5_PATH`.
4. Confirm broker-specific symbol names. Many brokers append a suffix, e.g.
   gold might be `XAUUSD`, `XAUUSDm`, or `GOLD#`. Check "Market Watch" in
   the terminal and set the `SYMBOLS` mapping in `.env` accordingly (see
   below) — never assume a name is universal.

### 2. Python environment

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

(This repo's `.venv` was already created and populated during setup.)

### 3. Configure

```powershell
copy .env.example .env
```

Edit `.env`:

```
MT5_LOGIN=12345678
MT5_PASSWORD=your-demo-password
MT5_SERVER=YourBroker-Demo
TRADING_MODE=DRY_RUN
SYMBOLS=EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD,XAUUSD:XAUUSDm
```

Never commit `.env` — it's already in `.gitignore`.

### 4. Verify the connection (Phase 1 diagnostic)

```powershell
.venv\Scripts\python.exe -m app.main
```

Expected output: connection confirmation, account balance/equity/leverage,
open position count, and a spread + last-close sample for each configured
symbol. If it fails, the error will tell you whether it's a terminal
connectivity issue (terminal not running / wrong path) or a login issue
(wrong login/password/server).

### 5. Enable AutoTrading (required before DEMO or LIVE execution)

MT5 blocks all programmatic order placement by default, even with valid
credentials and a connected terminal — this is a deliberate MetaQuotes
safety gate, separate from anything in this codebase. Without it, every
order attempt fails with `TRADE_RETCODE_CLIENT_DISABLES_AT: AutoTrading
disabled by client`. To enable it:

1. In the MT5 terminal: **Tools → Options → Expert Advisors** tab.
2. Check **"Allow algorithmic trading"**.
3. Also check/uncheck as needed **"Disable algorithmic trading via
   external Python API"** — it must be **unchecked** for the
   `MetaTrader5` Python package to be able to place orders.
4. Click OK.

This only affects `DEMO`/`LIVE` execution (Phase 6 onward). `DRY_RUN`
mode and everything in Phases 1-5 (data, backtesting, ML, signals) work
without it.

### 6. Run tests

```powershell
.venv\Scripts\python.exe -m pytest -v
```

### 7. Run the dashboard (API + frontend)

The dashboard is two processes: the FastAPI backend (talks to MT5) and
the Vite dev server (talks to the backend). Run both, in separate
terminals:

```powershell
# Terminal 1 - API backend, http://127.0.0.1:8000
.venv\Scripts\python.exe -m app.api.server

# Terminal 2 - frontend dev server, http://localhost:5173
cd frontend
npm install    # first time only
npm run dev
```

Open `http://localhost:5173`. Everything shown is live from your MT5
account — quotes, account equity, positions, signals, the risk limits
from `.env`, and a real backtest runner. The dashboard works in
`DRY_RUN` mode with no changes; switching `TRADING_MODE` in `.env` just
changes the badge shown and what the (separate) executor is allowed to
do — the dashboard itself never places orders.

For a production build of the frontend: `npm run build` in `frontend/`
outputs static files to `frontend/dist/`, which can be served by any
static file host (or FastAPI itself, with a couple of lines added to
`app/api/server.py` — not wired up yet since local dev is the target for
now).

### 8. Docker (dashboard frontend only — read why before filing this as broken)

`docker-compose.yml` containerizes **only the frontend**, not the
backend, and that's deliberate, not an oversight: `MetaTrader5` talks to
the MT5 terminal over local IPC (named pipes/shared memory on the same
Windows machine), not a network protocol — there's no host:port for a
container to connect to, and no Linux build of the terminal for it to
talk to even if there were. The backend has to run natively on a Windows
machine that also runs the MT5 terminal; only the static dashboard
benefits from containerizing.

```bash
# 1. On the Windows host, run the backend natively (as in step 7 above):
python -m app.api.server

# 2. Build and run the dashboard container:
docker compose up --build

# 3. Open http://localhost:8080
```

By default the container reaches the backend via
`host.docker.internal:8000` (works out of the box with Docker Desktop on
Windows). Override `BACKEND_HOST`/`BACKEND_PORT` env vars, and set
`VITE_API_KEY` at build time, if you've configured `API_KEY` on the
backend (mandatory once `API_HOST` is anything other than loopback — see
the safety model section above).

**Honesty note:** Docker isn't available in the sandboxed environment
this project was built in, so `docker compose up --build` has not
actually been run and verified end-to-end here — unlike everything else
in this README, which has been. The Dockerfile follows standard,
well-established patterns (multi-stage Node build, the official nginx
image's own documented `envsubst`-on-templates mechanism for
`BACKEND_HOST`/`BACKEND_PORT`), but you should treat the first `docker
compose up --build` you run as the actual first test of it, not a
formality.

## Project structure

```
trading_bot/
├── app/
│   ├── main.py            # Phase 1 diagnostic CLI entrypoint
│   ├── config.py           # Settings, env validation, LIVE-mode safety gate
│   ├── logging_setup.py    # Structured (JSON) logging, log_decision() helper
│   ├── api/
│   │   ├── server.py       # FastAPI app: account/positions/symbols/signals/journal/risk/backtest + /ws/live
│   │   ├── schemas.py      # Pydantic response models (the API's own contract, not the internal dataclasses)
│   │   └── state.py        # Shared app state - the one live MT5 connection + risk/execution components
│   ├── mt5/
│   │   ├── connection.py   # Thread-safe MT5 connect/reconnect/shutdown
│   │   ├── market_data.py  # Candles, ticks, symbol specs, live spread
│   │   ├── account.py      # Account info, open positions
│   │   └── orders.py       # order submission / SL-TP modify / close - broker-agnostic filling mode
│   ├── strategy/
│   │   ├── indicators.py    # EMA/RSI/MACD/ATR/ADX/Bollinger/Stochastic - all causal, no look-ahead
│   │   ├── market_structure.py  # Swing highs/lows, HH/HL/LH/LL trend, break of structure, S/R
│   │   ├── regime.py        # Trend/volatility regime classification (7 regimes, spec section 7)
│   │   └── signals.py       # Signal engine: technical + structure + regime + ML -> BUY/SELL/WAIT
│   ├── ai/
│   │   ├── features.py      # causal feature matrix + forward-looking labels (kept physically separate)
│   │   ├── model.py         # RandomForest/GradientBoosting/XGBoost wrapper, fixed 3-class output, joblib persistence
│   │   ├── trainer.py       # chronological train/val/test split, walk-forward model evaluation
│   │   ├── predictor.py     # live inference using the exact training feature pipeline
│   │   └── confidence.py    # probabilities -> BUY/SELL/WAIT, biased toward WAIT on any ambiguity
│   ├── llm/
│   │   ├── context.py       # structured, JSON-serializable market context handed to the LLM - no raw OHLCV
│   │   ├── client.py        # OpenAI-compatible chat completions call + strict response validation/coercion
│   │   └── overlay.py       # merges LLM output into a TradeIdea - can only downgrade to WAIT, never create/upgrade a trade
│   ├── risk/
│   │   ├── position_sizing.py  # equity/risk/stop-distance -> broker-valid lot size
│   │   ├── risk_manager.py     # the mandatory gate: daily/weekly loss, drawdown halt, exposure, sessions, sizing
│   │   └── safeguards.py       # emergency stop, stale-data guard, spread-anomaly guard, error tripwire
│   ├── execution/
│   │   ├── executor.py      # the pre-trade checklist (spec section 14) + DRY_RUN/DEMO/LIVE mode handling
│   │   └── trade_manager.py # EquityTracker (persisted day/week/peak equity), TradeJournal, AccountState assembly
│   ├── backtesting/
│   │   ├── engine.py           # bar-by-bar simulator: SL/TP fills, spread cost, equity curve
│   │   ├── metrics.py          # win rate, profit factor, expectancy, drawdown, Sharpe, streaks
│   │   ├── walk_forward.py     # chronological train/test windowing (rolling or expanding)
│   │   └── example_strategy.py # placeholder EMA-crossover strategy, for testing the engine only
│   ├── database/              # (Phase 1b) models, repository
│   └── notifications/telegram.py  # optional outbound alerts - httpx, never raises, no-op if disabled
├── frontend/                # React 19 + Vite 8 + Tailwind v4 dashboard
│   ├── Dockerfile, nginx.conf.template  # containerizes the dashboard only - see Setup step 8
│   └── src/
│       ├── api.js, hooks/    # fetch wrapper + /ws/live websocket hook + REST polling hook
│       ├── components/ui.jsx # Panel/StatCard/Badge - shared presentational primitives
│       └── pages/            # Dashboard, Markets, Signals, Open Trades, Trade History, Backtesting, Risk, Logs
├── data/, models/, logs/    # gitignored, created at runtime
├── tests/
├── .env.example
├── docker-compose.yml       # frontend container only - backend must run natively (MT5's local-IPC constraint)
├── requirements.txt
└── README.md
```

## Project status

**Phase 1 — done:** project scaffold, configuration + validation (incl. the
LIVE-mode safety gate), structured logging, MT5 connection management with
retry/reconnect, market data (candles/ticks/symbol specs/spread), account
info + open positions, diagnostic CLI, first test suite.

**Phase 2 — done:** technical indicators (EMA 20/50/200, RSI, MACD, ATR,
ADX/DI, Bollinger Bands, Stochastic, volume ratio, rolling highs/lows),
market structure (fractal swing highs/lows with an explicit confirmation
lag, HH/HL/LH/LL trend classification, break of structure, ATR-padded
support/resistance), and a 7-state market regime classifier (strong/weak
bullish/bearish trend, range, high/low volatility). All of it is causal —
every function is proven, by test, to produce the same value at row *t*
whether computed on the full series or a series truncated at *t* — so it's
safe to reuse unmodified for ML features (Phase 4) without look-ahead bias.

**Phase 3 — done:** bar-by-bar backtesting engine (SL/TP fill simulation
using real historical spread, ATR-based stops, one-position-at-a-time),
broker-correct position sizing (`app/risk/position_sizing.py` — pulled
forward from Phase 5 since the backtester needs it; the live execution
engine will import the same module, not a second copy), a full performance
metrics suite (win rate, profit factor, expectancy in both currency and
R-multiples, max drawdown, Sharpe, streaks — reported together so a high
win rate can't hide a negative-expectancy strategy), and chronological
walk-forward windowing (rolling or expanding) that the ML trainer will
reuse unmodified in Phase 4. A placeholder EMA-crossover + regime-filter
strategy exists solely to exercise the engine end-to-end — it is not the
production strategy.

**Phase 4 — done:** ML pipeline. Feature engineering builds ~40 causal
features (returns, ATR-normalized EMA distances, RSI/MACD/ADX/Bollinger/
Stochastic, candle structure, one-hot trend/regime, cyclic time-of-day/
day-of-week) on top of the Phase 2 indicators. Labels (BUY/SELL/NEUTRAL,
by whether price moves > 1 ATR within a configurable horizon) are built in
a physically separate, clearly-marked function since it's the one place
in the codebase deliberately allowed to look at future bars — it is never
joined into the feature matrix. Training uses a strict chronological
train/validation/test split (asserted, not just intended) plus
walk-forward evaluation reusing Phase 3's windowing utility unmodified.
Default model is Random Forest (Gradient Boosting / XGBoost available
behind the same interface) with joblib save/load. `confidence.py` turns
probabilities into BUY/SELL/WAIT, defaulting to WAIT whenever the top
class is NEUTRAL, under-confident, or too close to the runner-up.

**Phase 5 — done:** the signal engine (`app/strategy/signals.py`) combines
a weighted technical score (EMA alignment/MACD/RSI/ADX-DI/Stochastic),
market structure, regime, and (optionally) the ML model's probabilities
into one BUY/SELL/WAIT decision. Components are required to *agree*, not
be averaged — if technical says SELL while structure says UPTREND, the
result is WAIT with the conflict named explicitly, never a blended
low-confidence trade. SL/TP are ATR-based (1.5x/3x by default) and
computed as soon as a directional candidate exists, even if a later gate
(spread, minimum score, minimum R:R, regime) downgrades the final call to
WAIT — so every decision stays explainable. An adapter
(`make_signal_engine_strategy`) lets the *real* signal engine run through
the Phase 3 backtester, not just the placeholder strategy.

The risk manager (`app/risk/risk_manager.py`) is the mandatory gate every
proposed trade passes through: daily/weekly loss limits, max-drawdown
halt, max open trades, max trades/day, max spread, trading-session
windows, minimum R:R, mandatory SL/TP, per-currency exposure caps, and
position sizing (reusing Phase 3's `position_sizing` module) — all in one
place, so no signal can bypass it. `app/risk/safeguards.py` adds an
on-disk emergency-stop switch (survives a process restart), a stale-data
guard, a spread-anomaly guard (relative to recent median, not just a
fixed threshold), and a consecutive-error tripwire, ready to be wired
into the execution loop in Phase 6.

**Phase 6 — done:** the execution engine. `app/mt5/orders.py` submits
market orders, modifies SL/TP, and closes positions — every request goes
through `order_check()` before `order_send()`, and the order-filling mode
(FOK/IOC/Return) is chosen per-symbol from the broker's own supported
modes rather than hardcoded, since a wrong hardcoded mode is silently
broker-specific and was in fact caught live during testing (see below).
`app/execution/executor.py` runs the full pre-trade checklist from spec
section 14 in order — safety guards, signal validity, duplicate-position
prevention, account-state assembly, risk manager approval, then a
DEMO/LIVE **account-type consistency check** (if config says DEMO but the
connected account isn't flagged as a demo account, execution refuses and
trips the emergency stop — defense in depth beyond config.py's LIVE gate)
— before ever calling `orders.py`. `app/execution/trade_manager.py` adds
`EquityTracker` (JSON-file-backed day/week/peak equity, survives a
restart) and `TradeJournal` (append-only JSONL, a real working
implementation of spec section 21's trade journal pending the full
database phase).

**Verified on the live demo account, not just mocked:** placed a real
market order (0.64 lots EURUSD, sized from a 0.1% test risk), confirmed
it via `positions_get()`, modified its SL/TP, and closed it — full
round-trip success. Getting there surfaced two real broker-specific
issues that are now permanently fixed in the code: (1) `order_check()`'s
success code is `0`, not `TRADE_RETCODE_DONE` as originally assumed —
that code is specific to `order_send()`'s result, a different call with a
different meaning; (2) the hardcoded `ORDER_FILLING_IOC` was rejected by
this broker outright, which is why filling mode is now selected
per-symbol from `symbol_info().filling_mode`. Also required enabling MT5's
own AutoTrading permission (see Setup step 5) — a terminal-level gate
entirely outside this codebase's control, and worth knowing about before
assuming DEMO/LIVE execution is broken.

**Phase 7 — done:** the dashboard. `app/api/server.py` is a FastAPI
backend exposing everything built so far — account/positions/live quotes,
on-demand signal generation (optionally with ML, cached per symbol so
repeat requests don't retrain), the trade journal, risk limits with a
working emergency-stop trip/reset, a real backtest runner, tailing system
logs, and a `/ws/live` websocket pushing account/position/quote updates
every 3 seconds. Nothing is mocked: every endpoint calls the exact same
modules the CLI and executor use, verified live against the demo account
(curl for every endpoint, including running an actual backtest through
the API). MT5's Python API is not thread-safe, so routes are `async def`
and call MT5 wrappers directly rather than through FastAPI's thread pool
— the single event loop thread serializes access by construction rather
than needing a lock.

The frontend (`frontend/`) is React 19 + Vite 8 + Tailwind v4 + Recharts,
with 8 pages: Dashboard, Markets, Signals (the spec section 22 signal
card, with a live/WAIT example and its conflict reasons), Open Trades,
Trade History (the raw trade journal), Backtesting (form + equity curve
chart + trade list), Risk Management (limits + emergency stop control),
and System Logs. Live data flows through a `/ws/live` websocket hook with
automatic reconnect; everything else polls REST on a short interval.
Verified with a headless-browser pass through every page against the live
backend — zero console errors, and the screenshots show real account
data, not placeholders.

**Known limitation carried from Phase 6:** without a trade database, the
dashboard can't yet show true historical win-rate/profit-factor
performance for live trading (the Dashboard page says so explicitly,
pointing at Backtesting for real, data-backed performance metrics
instead of fabricating live ones).

**Phase 8 — done:** Telegram notifications (`app/notifications/telegram.py`),
optional and off by default. It's a plain `httpx` POST to Telegram's Bot
API — not the `python-telegram-bot` framework, which is built for bots
that *receive* updates; this only ever pushes one-way alerts, so a
framework built for the opposite problem was dropped as a dependency.
Every `notify_*` call catches its own errors and returns `False` rather
than raising, so a Telegram outage can never interrupt trading.

Wired into real, already-existing call sites rather than left dangling:
new-signal-worthy risk halts (daily loss/weekly loss/max-drawdown - not
routine skips like a wide spread, which would make Telegram unusable if
every one paged you) and order failures fire from the executor; trade-
opened fires on a successful fill; emergency-stop fires from both the
executor's DEMO/LIVE account-mismatch safety trip and the dashboard's
manual trip endpoint; bot-disconnected fires from both the CLI diagnostic
and the API server's startup path. "Trade closed / SL hit / TP hit" is
the one alert spec section 20 lists that has no natural synchronous
trigger — MT5 doesn't push fill/close events — so `app/mt5/account.py`
gained `get_closed_position_info()` (reads the real closing deal via
`history_deals_get(position=ticket)`, including *why* it closed via
MT5's own `DEAL_REASON_SL`/`DEAL_REASON_TP`), and the API server runs a
single background watcher task (independent of how many dashboard tabs
are open, so a close is never reported twice) that diffs the open-ticket
set every 5 seconds and fires the alert the moment a tracked position
disappears.

To enable: set `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN` (from
@BotFather), and `TELEGRAM_CHAT_ID` (message your bot once, then check
`https://api.telegram.org/bot<token>/getUpdates` for your chat id) in
`.env`. Left disabled, nothing changes — every notifier call is a no-op.

**Phase 9 — done:** the optional LLM analysis layer (spec section 18) —
disabled by default, and structurally incapable of executing a trade or
overriding a risk limit, not just by convention.

- `app/llm/context.py` builds a JSON-serializable `MarketContext` from
  data the deterministic pipeline already computed (trend, regime,
  indicators, support/resistance, ML probabilities, open positions, risk
  state) - the LLM never sees raw candles or gets to run its own
  "analysis," which bounds any disagreement to interpretation of known
  facts rather than invented ones.
- `app/llm/client.py` calls an OpenAI-compatible chat completions
  endpoint (plain `httpx`, same reasoning as Telegram: no framework
  needed for one outbound call) and validates every field of the
  response - an invalid `decision` becomes `"WAIT"`, out-of-range
  `confidence` is clamped, non-string/non-list fields are coerced,
  malformed JSON (including markdown-fenced JSON, which models routinely
  emit despite instructions) or a network failure both resolve to a safe
  `WAIT` rather than raising into trading code.
- `app/llm/overlay.py` is the deterministic validation gate itself:
  `apply_llm_overlay` has exactly one assignment target for `direction`
  besides "leave unchanged," and it is the literal string `"WAIT"` - there
  is no code path that can set it to `"BUY"` or `"SELL"`. The LLM can veto
  a trade the signal engine already proposed; it can never create one,
  promote a WAIT, or touch entry/stop-loss/take-profit/position sizing.
  This is enforced by the function's structure, verified by a test that
  exhaustively checks every decision/validity combination can't turn a
  WAIT into a trade.
- Wired into `GET /api/signals/{symbol}?use_llm=true` in the dashboard
  API - verified live (LLM disabled, no key configured): the endpoint
  returned its normal real WAIT decision unchanged, with `"LLM_ENABLED is
  false"` transparently noted rather than silently ignored or treated as
  agreement.

**Honesty note:** without an LLM API key configured, this has not been
verified against a real model's output end-to-end - only the full
validation/coercion/overlay pipeline has been (13 + 10 + 3 tests
covering malformed responses, network failures, and every agree/disagree/
invalid combination). To use it: set `LLM_ENABLED=true`, `LLM_API_KEY`,
and optionally `LLM_MODEL`/`LLM_API_BASE` (defaults to OpenAI) in `.env`.

**Phase 10 — done:** final hardening.

- **Test coverage**: went from 84% to **96%** line coverage (`pytest
  --cov=app --cov-report=term-missing`, 331 tests). The push specifically
  targeted the modules that had only ever been live-tested against the
  real demo account rather than unit-tested — `app/mt5/connection.py` (33%
  → 100%), `app/mt5/market_data.py` (39% → 99%), `app/mt5/account.py`
  (66% → 100%) — plus the API server's remaining endpoints (backtest,
  symbols, ML signal path, websocket, the lifespan connection-failure
  path, the closed-trade watcher's tick logic), Gradient Boosting/XGBoost
  estimator branches, and error-handling paths across position sizing,
  order submission, and equity tracking.
- **Security review**: a fresh, independent review of the actual current
  code (every safety-critical claim re-verified by tracing call sites,
  not trusted from comments) found one real gap — the dashboard API had
  no authentication, safe only because it binds to loopback by default —
  plus three low-severity findings (unbounded request parameters,
  internal exception text returned to callers, a theoretical token-in-
  logs path in the Telegram error handler). All four are now fixed:
  - `API_KEY` (`app/config.py`) is enforced by an auth middleware on every
    `/api/*` route and the `/ws/live` websocket handshake, and a new
    config validator **refuses to start** if `API_HOST` is ever set off
    loopback without one — the same "refuse to start unsafely" pattern as
    the LIVE-mode gate.
  - `BacktestRequest.days`, `/api/journal`'s and `/api/logs`' `limit` are
    now bounded (`Field`/`Query` constraints) instead of accepting
    arbitrary values.
  - `/api/status`, `/api/account`, `/api/positions` now log the full
    exception server-side and return a generic message to the caller.
  - The Telegram client no longer logs raw exception text (which could
    theoretically echo a URL containing the bot token) — only the
    exception type and HTTP status code, if any.
  - No secrets were found committed anywhere in the repo — the real demo
    MT5 credentials exist only in the local, git-ignored `.env`, which is
    expected and correct.
- **Docker**: added for the dashboard frontend (see Setup step 8) — not
  the backend, deliberately: MT5's local-IPC-only architecture (spec
  section — see that step for the full explanation) makes a standard
  containerized backend a non-starter, and shipping one anyway that
  silently fails to connect would violate this project's whole "don't
  fake it" premise.

This completes all 10 phases of the original spec.

## Known limitations

- No real database yet — equity history and the trade journal are
  file-backed (JSON/JSONL) rather than SQLite; the schema is deliberately
  flat/JSON-serializable so migrating to the real database later is a
  data-loading exercise, not a redesign.
- No scheduler/long-running loop yet — `Executor.execute_signal` is
  called once per invocation (via the CLI, or on-demand through the API);
  wiring it into a persistent bar-by-bar loop with reconnect handling is
  the natural next step beyond the original 10-phase spec.
- Connection retry is bounded and synchronous; a background
  auto-reconnect loop for a long-running process is part of that
  same future scheduler work.
- Live performance analytics (win rate/profit factor on *real* executed
  trades, as opposed to backtests) need the database above to be
  trustworthy — the dashboard says so explicitly rather than
  approximating it from the file-backed journal.
- The Docker Compose setup for the frontend has not been run end-to-end
  in this environment (no Docker daemon available here) — see Setup step
  8 for the honesty note.
# trading-bot
