"""Export a recorded backtest run to a static JSON file for the check-in dashboard.

    python -m dashboard.export [--run-id ID | --latest] [--db results.sqlite]
                               [--out dashboard/web/public/data.json]

Pure read side: opens the SQLite store (stdlib ``sqlite3`` only — no new dependency), pulls
the run summary, equity curve, fills, and reconstructs current positions, then writes one JSON
document. Nothing here trades or mutates the store. Target weights for the drift view come from
the current ``settings.rebalance`` config (the run did not persist them); this is a check-in
read, so the live config is the right reference.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from broker.simulated import Fill
from core.config import Settings, load_settings
from record.stats import realized_pnl_by_fill

_QTY_EPSILON = 1e-9


def _load_equity_curve(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ts, equity, benchmark_equity FROM equity_points "
        "WHERE run_id = ? ORDER BY ts",
        (run_id,),
    ).fetchall()
    return [
        {"ts": ts, "equity": equity, "benchmark_equity": benchmark}
        for ts, equity, benchmark in rows
    ]


def _load_fills(conn: sqlite3.Connection, run_id: str) -> list[Fill]:
    rows = conn.execute(
        "SELECT symbol, side, qty, price, commission FROM fills "
        "WHERE run_id = ? ORDER BY seq",
        (run_id,),
    ).fetchall()
    return [Fill(sym, side, qty, price, comm) for sym, side, qty, price, comm in rows]


def _trades(fills: list[Fill]) -> list[dict[str, Any]]:
    """Per-fill trade rows with notional and average-cost realized P&L (seq-ordered)."""
    realized = realized_pnl_by_fill(fills)
    return [
        {
            "seq": i,
            "symbol": f.symbol,
            "side": f.side,
            "qty": f.qty,
            "price": f.price,
            "notional": f.qty * f.price,
            "realized_pnl": pnl,
        }
        for i, (f, pnl) in enumerate(zip(fills, realized, strict=True))
    ]


def _positions(
    fills: list[Fill], target_weights: dict[str, float], final_equity: float
) -> list[dict[str, Any]]:
    """Reconstruct current holdings from signed fills and join against target weights.

    Current qty = Σ buy qty − Σ sell qty per symbol; valued at that symbol's last fill price
    (the only per-symbol price the store keeps). Drift = current_weight − target_weight, shown
    for the union of held symbols and configured targets so a missing target reads as a buy.
    """
    qty: dict[str, float] = {}
    last_price: dict[str, float] = {}
    for f in fills:
        qty[f.symbol] = qty.get(f.symbol, 0.0) + (f.qty if f.side == "buy" else -f.qty)
        last_price[f.symbol] = f.price

    out: list[dict[str, Any]] = []
    for symbol in sorted(set(qty) | set(target_weights)):
        held = qty.get(symbol, 0.0)
        price = last_price.get(symbol, 0.0)
        value = held * price
        if abs(value) < _QTY_EPSILON and symbol not in target_weights:
            continue  # fully-exited, non-target dust
        current_weight = value / final_equity if final_equity > 0 else 0.0
        target_weight = target_weights.get(symbol, 0.0)
        out.append(
            {
                "symbol": symbol,
                "qty": held,
                "price": price,
                "current_value": value,
                "current_weight": current_weight,
                "target_weight": target_weight,
                "drift": current_weight - target_weight,
            }
        )
    return out


def _merge_yolo(
    document: dict[str, Any], db_path: str, yolo_run_id: str
) -> dict[str, Any]:
    """Overlay a YOLO run's equity curve as a third series on the baseline document.

    The max-risk contrast line. Both runs share the SPY trading calendar, so the curves align
    by ``ts``; any baseline date missing from the YOLO curve gets ``None`` (the chart simply
    breaks the line there). Adds ``stats.yolo`` for the comparison table and a ``yolo_label``.
    Honest naming: until a real social feed is wired the sleeve trades a price/volume proxy, so
    the label says "proxy hype".
    """
    from record import BacktestStore

    summary = BacktestStore(db_path).load_summary(yolo_run_id)
    with sqlite3.connect(db_path) as conn:
        yolo_curve = _load_equity_curve(conn, yolo_run_id)
    yolo_by_ts = {p["ts"]: p["equity"] for p in yolo_curve}
    for point in document["equity_curve"]:
        point["yolo_equity"] = yolo_by_ts.get(point["ts"])

    document["yolo_label"] = "YOLO (proxy hype)"
    document["yolo_run"] = {
        "run_id": summary.run_id,
        "strategy_mode": summary.strategy_mode,
        "portfolio_final": summary.portfolio_final,
    }
    document["stats"]["yolo"] = summary.portfolio_stats.to_dict()
    return document


def build_export(
    db_path: str,
    run_id: str | None,
    settings: Settings,
    *,
    yolo_run_id: str | None = None,
) -> dict[str, Any]:
    """Build the dashboard JSON for ``run_id`` (or the latest run), optionally overlaying YOLO."""
    from record import BacktestStore  # local import keeps module import cheap

    store = BacktestStore(db_path)
    resolved = run_id or store.latest_run_id()
    if resolved is None:
        raise SystemExit("no runs found in the store — run a backtest first")
    summary = store.load_summary(resolved)

    with sqlite3.connect(db_path) as conn:
        equity_curve = _load_equity_curve(conn, resolved)
        fills = _load_fills(conn, resolved)

    final_equity = equity_curve[-1]["equity"] if equity_curve else summary.portfolio_final
    target_weights = dict(settings.rebalance.target_weights)

    document = {
        "run": {
            "run_id": summary.run_id,
            "start": summary.start.isoformat(),
            "end": summary.end.isoformat(),
            "starting_cash": summary.starting_cash,
            "strategy_mode": summary.strategy_mode,
            "portfolio_final": summary.portfolio_final,
            "benchmark_final": summary.benchmark_final,
        },
        "benchmark_label": "SPY (buy & hold)",
        "goal": "match SPY with less risk — not outperformance",
        "target_weights": target_weights,
        "equity_curve": equity_curve,
        "stats": {
            "portfolio": summary.portfolio_stats.to_dict(),
            "benchmark": summary.benchmark_stats.to_dict(),
        },
        "positions": _positions(fills, target_weights, final_equity),
        "trades": _trades(fills),
    }
    if yolo_run_id is not None:
        document = _merge_yolo(document, db_path, yolo_run_id)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dashboard.export",
        description="Export a recorded backtest run to static JSON for the check-in dashboard.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-id", help="run id to export (default: the latest run)")
    group.add_argument("--latest", action="store_true", help="export the latest run (default)")
    parser.add_argument(
        "--yolo-run", help="run id of a YOLO run to overlay as the max-risk contrast line")
    parser.add_argument("--db", help="path to the results SQLite db (default: from config)")
    parser.add_argument(
        "--out",
        default="dashboard/web/public/data.json",
        help="output JSON path (default: dashboard/web/public/data.json)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    db_path = args.db or settings.record.db_path
    document = build_export(db_path, args.run_id, settings, yolo_run_id=args.yolo_run)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"wrote {out_path} (run {document['run']['run_id']}, "
          f"{len(document['trades'])} trades, {len(document['positions'])} positions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
