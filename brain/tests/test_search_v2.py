import pytest
from datetime import datetime, timezone, timedelta
from app.search import reciprocal_rank_fusion, recency_factor


def test_recency_factor_same_day():
    now_iso = datetime.now(timezone.utc).isoformat()
    factor = recency_factor(now_iso, decay_rate=0.02)
    assert factor == pytest.approx(1.0, abs=0.01)


def test_recency_factor_7_days_old():
    ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    factor = recency_factor(ts, decay_rate=0.02)
    # 1 / (1 + 7 * 0.02) = 1 / 1.14 ≈ 0.877
    assert factor == pytest.approx(0.877, abs=0.01)


def test_recency_factor_30_days_old():
    ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    factor = recency_factor(ts, decay_rate=0.02)
    # 1 / (1 + 30 * 0.02) = 1 / 1.6 = 0.625
    assert factor == pytest.approx(0.625, abs=0.01)


def test_recency_factor_bad_timestamp_returns_one():
    factor = recency_factor("not-a-date", decay_rate=0.02)
    assert factor == 1.0


def test_rrf_with_decay_boosts_recent():
    recent_ts = datetime.now(timezone.utc).isoformat()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

    kw = [{"id": "old", "timestamp": old_ts}, {"id": "recent", "timestamp": recent_ts}]
    sem = [{"id": "old", "timestamp": old_ts}, {"id": "recent", "timestamp": recent_ts}]

    # Without decay: scores equal, stable sort preserves insertion order (old first)
    no_decay = reciprocal_rank_fusion(kw, sem, k=60, decay_rate=0.0)
    # With decay: recent should rank higher
    with_decay = reciprocal_rank_fusion(kw, sem, k=60, decay_rate=0.02)
    assert with_decay.index("recent") < with_decay.index("old")
