"""Optional JSON run-config for the CLIs (``--config X``). Flags override file values."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunConfig:
    """A backtest run's parameters, from a JSON file and/or CLI flags."""

    start: date
    end: date
    universe: tuple[str, ...] | None = None  # None -> the full seed universe
    mode: str | None = None  # None -> the configured strategy_mode
    label: str | None = None


def load_run_config(path: str | Path) -> dict[str, object]:
    """Read a run-config JSON into a plain dict (caller merges with CLI flags)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"run config must be a JSON object, got {type(data).__name__}")
    return data


def resolve_run_config(
    *,
    config_path: str | None,
    start: str | None,
    end: str | None,
    universe: str | None,
    mode: str | None,
    label: str | None,
) -> RunConfig:
    """Merge an optional JSON config with CLI flags (flags win). ``universe`` is comma-separated."""
    data: dict[str, object] = load_run_config(config_path) if config_path else {}

    def pick(flag: object, key: str) -> object:
        return flag if flag is not None else data.get(key)

    start_val = pick(start, "start")
    end_val = pick(end, "end")
    if not isinstance(start_val, str) or not isinstance(end_val, str):
        raise ValueError("both --start and --end are required (via flag or --config)")

    uni_val = universe if universe is not None else data.get("universe")
    universe_tuple = _parse_universe(uni_val)

    return RunConfig(
        start=date.fromisoformat(start_val),
        end=date.fromisoformat(end_val),
        universe=universe_tuple,
        mode=pick(mode, "mode"),  # type: ignore[arg-type]
        label=pick(label, "label"),  # type: ignore[arg-type]
    )


def _parse_universe(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return tuple(s.strip().upper() for s in value.split(",") if s.strip()) or None
    if isinstance(value, Sequence):
        return tuple(str(s).strip().upper() for s in value) or None
    raise ValueError(f"universe must be a string or list, got {type(value).__name__}")
