import { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from "recharts";

const pct = (x) => (x == null ? "—" : `${(x * 100).toFixed(2)}%`);
const money = (x) => (x == null ? "—" : x.toLocaleString(undefined, { style: "currency", currency: "USD" }));
const sign = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "muted");

// Read-only check-in view. It fetches the static data.json that `python -m dashboard.export`
// writes; it renders, it never trades.
export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("data.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="wrap">
        <h1>PaperHands — Rebalancer Check-in</h1>
        <p className="error">Could not load data.json: {error}</p>
        <p className="muted">Run <code>python -m dashboard.export --latest</code> first.</p>
      </div>
    );
  }
  if (!data) return <div className="wrap"><p className="muted">Loading…</p></div>;

  const { run, stats, equity_curve, positions, trades, target_weights } = data;

  return (
    <div className="wrap">
      <h1>PaperHands — Rebalancer Check-in</h1>
      <p className="framing">
        {run.start} → {run.end} · mode <strong>{run.strategy_mode}</strong> · benchmark{" "}
        {data.benchmark_label}. Goal: <strong>{data.goal}</strong>.
      </p>
      {data.yolo_label && (
        <p className="framing muted">
          Third line: <strong>{data.yolo_label}</strong> — a max-risk momentum/hype sleeve shown
          for contrast. Paper-only, never live; the proxy chases price/volume heat until a real
          social feed is wired.
        </p>
      )}

      <div className="panel">
        <h2>Equity vs benchmark{data.yolo_label ? " vs YOLO" : ""}</h2>
        <EquityChart curve={equity_curve} yoloLabel={data.yolo_label} />
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <StatsPanel stats={stats} yoloLabel={data.yolo_label} />
        <PositionsPanel positions={positions} targets={target_weights} />
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <h2>Rebalance trades &amp; realized gain/loss</h2>
        <TradesTable trades={trades} />
      </div>
    </div>
  );
}

function EquityChart({ curve, yoloLabel }) {
  if (!curve || curve.length === 0) return <p className="muted">No equity points.</p>;
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={curve} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
        <CartesianGrid stroke="#2a3441" strokeDasharray="3 3" />
        <XAxis dataKey="ts" tick={{ fill: "#8b97a7", fontSize: 11 }} minTickGap={40} />
        <YAxis tick={{ fill: "#8b97a7", fontSize: 11 }} domain={["auto", "auto"]}
               tickFormatter={(v) => `$${Math.round(v / 1000)}k`} width={52} />
        <Tooltip contentStyle={{ background: "#1a212b", border: "1px solid #2a3441" }}
                 formatter={(v) => money(v)} />
        <Legend />
        <Line type="monotone" dataKey="equity" name="Portfolio" stroke="#58a6ff" dot={false} strokeWidth={2} />
        <Line type="monotone" dataKey="benchmark_equity" name="SPY (buy & hold)" stroke="#d29922" dot={false} strokeWidth={2} />
        {yoloLabel && (
          <Line type="monotone" dataKey="yolo_equity" name={yoloLabel} stroke="#f85149"
                dot={false} strokeWidth={2} connectNulls />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

function StatsPanel({ stats, yoloLabel }) {
  const rows = [
    ["Total return", "total_return", pct],
    ["CAGR", "cagr", pct],
    ["Volatility (ann.)", "volatility", pct],
    ["Max drawdown", "max_drawdown", pct],
    ["Sharpe", "sharpe", (x) => x.toFixed(2)],
    ["Turnover", "turnover", (x) => x.toFixed(2)],
  ];
  const showYolo = yoloLabel && stats.yolo;
  return (
    <div className="panel">
      <h2>Risk stats — portfolio vs SPY{showYolo ? " vs YOLO" : ""}</h2>
      <table>
        <thead>
          <tr>
            <th>Metric</th><th>Portfolio</th><th>SPY</th>
            {showYolo && <th>{yoloLabel}</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, key, fmt]) => (
            <tr key={key}>
              <td>{label}</td>
              <td>{fmt(stats.portfolio[key])}</td>
              <td className="muted">{fmt(stats.benchmark[key])}</td>
              {showYolo && <td className="neg">{fmt(stats.yolo[key])}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PositionsPanel({ positions, targets }) {
  return (
    <div className="panel">
      <h2>Positions vs target (drift)</h2>
      <table>
        <thead><tr><th>Symbol</th><th>Current</th><th>Target</th><th>Drift</th></tr></thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol}>
              <td>{p.symbol}</td>
              <td>{pct(p.current_weight)}</td>
              <td className="muted">{pct(p.target_weight)}</td>
              <td className={sign(p.drift)}>{pct(p.drift)}</td>
            </tr>
          ))}
          {positions.length === 0 && (
            <tr><td colSpan={4} className="muted">No positions.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function TradesTable({ trades }) {
  const recent = [...trades].reverse().slice(0, 50);
  return (
    <table>
      <thead>
        <tr><th>#</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Notional</th><th>Realized P/L</th></tr>
      </thead>
      <tbody>
        {recent.map((t) => (
          <tr key={t.seq}>
            <td>{t.seq}</td>
            <td>{t.symbol}</td>
            <td className={t.side === "buy" ? "pos" : "neg"}>{t.side}</td>
            <td>{t.qty.toFixed(4)}</td>
            <td>{money(t.price)}</td>
            <td>{money(t.notional)}</td>
            <td className={sign(t.realized_pnl)}>{t.side === "sell" ? money(t.realized_pnl) : "—"}</td>
          </tr>
        ))}
        {recent.length === 0 && (
          <tr><td colSpan={7} className="muted">No trades recorded.</td></tr>
        )}
      </tbody>
    </table>
  );
}
