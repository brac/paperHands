"""Tests for the proxy-hype signal: the volume_spike indicator + SignalSet passthrough.

The YOLO sleeve's hype proxy must be point-in-time safe (no future bars) and the social feed's
``HypeContext`` must flow through ``compute_signals`` onto the ``SignalSet`` unchanged. Offline
and deterministic over hand-built bar frames.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.config import SignalConfig
from core.contracts import AccountState, HypeContext
from data.frame import COLUMNS, INDEX_NAME
from ingest.snapshot import MarketSnapshot
from signals.indicators import volume_spike
from signals.signals import compute_signals


def _frame(volumes: list[float], price: float = 10.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=len(volumes))
    data = {c: [price] * len(volumes) for c in COLUMNS}
    data["volume"] = list(volumes)
    data["adj_volume"] = list(volumes)
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name=INDEX_NAME))


# -- volume_spike indicator -----------------------------------------------------------
def test_volume_spike_math_against_trailing_average():
    # window=4, last 4 volumes [100, 100, 100, 300] -> mean 150 -> 300/150 - 1 = 1.0.
    df = _frame([100, 100, 100, 100, 100, 300])
    assert volume_spike(df, 4) == pytest.approx(300 / 150 - 1.0)


def test_volume_spike_none_on_short_history():
    assert volume_spike(_frame([100, 200]), 5) is None


def test_volume_spike_none_on_nonpositive_average():
    assert volume_spike(_frame([0, 0, 0, 0]), 4) is None


def test_volume_spike_uses_only_the_trailing_window_no_lookahead():
    # A huge volume bar appended *after* the window must not change the score for an earlier
    # as-of slice. Compute on the full frame vs a slice ending before the spike.
    full = _frame([100, 100, 100, 100, 9999])
    sliced = full.iloc[:4]  # as-of before the spike day
    assert volume_spike(sliced, 4) == pytest.approx(0.0)  # all 100s -> no spike
    # The indicator only ever reads the last `window` rows of what it is given.
    assert volume_spike(full, 4) != volume_spike(sliced, 4)


# -- compute_signals passthrough ------------------------------------------------------
def _snapshot(social: dict[str, HypeContext]) -> MarketSnapshot:
    account = AccountState(cash=0.0, equity=0.0, buying_power=0.0)
    return MarketSnapshot(
        as_of=date(2024, 1, 10),
        prices={"GME": _frame([100, 100, 100, 100, 500])},
        account=account,
        social=social,
    )


def test_compute_signals_attaches_volume_spike_and_social_fields():
    snap = _snapshot({"GME": HypeContext(social_score=7.5, trump_mention=True,
                                         reddit_mentions=42)})
    sigs = compute_signals(snap, ["GME"], SignalConfig(zscore_window=4))
    gme = sigs["GME"]
    assert gme.volume_spike == pytest.approx(500 / 200 - 1.0)  # last 4: [100,100,100,500]->mean200
    assert gme.social_score == 7.5
    assert gme.trump_mention is True
    assert gme.reddit_mentions == 42


def test_compute_signals_defaults_social_fields_when_no_feed():
    snap = _snapshot({})  # NullSocial-equivalent: no entries
    gme = compute_signals(snap, ["GME"], SignalConfig(zscore_window=4))["GME"]
    assert gme.social_score is None
    assert gme.trump_mention is False
    assert gme.reddit_mentions == 0
    # The price/volume proxy is still computed with no feed at all.
    assert gme.volume_spike is not None
