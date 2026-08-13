# tests/test_conflict_timeline_skip.py
"""Timeline fact pairs must not be flagged as contradictions."""
from app.consolidate import (
    _is_timeline_fact_summary,
    _skip_as_timeline_pair,
    CONFLICT_MIN_SIM,
    CONFLICT_MIN_SIM_FACT,
)


def test_timeline_detects_prod_tips():
    assert _is_timeline_fact_summary("Prod tips 2026-08-08 BE e402951 SF 6a11aff")
    assert _is_timeline_fact_summary("Production live: mobile luxury UX at storefront a728963")
    assert _is_timeline_fact_summary("C: cleanup ~3.3GB free to ~27.9GB free via Docker prune")
    assert not _is_timeline_fact_summary("Dad prefers honey packaging option B")


def test_skip_fact_fact_timeline_pair():
    assert _skip_as_timeline_pair(
        "fact", "fact",
        "Prod tips 2026-08-07 BE e402951 SF 1735a39",
        "Prod tips 2026-08-08 BE e402951 SF 6a11aff promo shipping",
    )
    # belief vs fact still eligible for conflict detection
    assert not _skip_as_timeline_pair(
        "belief", "fact",
        "The store is live on Vercel",
        "Prod tips 2026-08-08 BE e402951 SF 6a11aff",
    )
    # unrelated facts still eligible
    assert not _skip_as_timeline_pair(
        "fact", "fact",
        "Quote-lock must verify amount at payment boundary",
        "Admin notify emails use Resend templates",
    )


def test_fact_threshold_stricter_than_belief():
    assert CONFLICT_MIN_SIM_FACT > CONFLICT_MIN_SIM
