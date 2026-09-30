# tests/test_conflict_timeline_skip.py
"""Timeline fact pairs must not be flagged as contradictions."""
from app.consolidate import (
    _is_timeline_fact_summary,
    _skip_as_timeline_pair,
    CONFLICT_MIN_SIM,
    CONFLICT_MIN_SIM_FACT,
)


def test_timeline_detects_deploy_and_status_facts():
    assert _is_timeline_fact_summary("Deployed to prod 2026-01-01: backend abc1234, web def5678")
    assert _is_timeline_fact_summary("Release v2.4.1 is live")
    assert _is_timeline_fact_summary("Disk cleanup: 5GB free to 40GB free")
    assert not _is_timeline_fact_summary("The team prefers packaging option B")


def test_skip_fact_fact_timeline_pair():
    assert _skip_as_timeline_pair(
        "fact", "fact",
        "Deployed to prod 2026-01-01: backend abc1234, web 0a1b2c3",
        "Deployed to prod 2026-01-02: backend abc1234, web def5678",
    )
    # belief vs fact still eligible for conflict detection
    assert not _skip_as_timeline_pair(
        "belief", "fact",
        "The shop is live",
        "Deployed to prod 2026-01-02: backend abc1234",
    )
    # unrelated facts still eligible
    assert not _skip_as_timeline_pair(
        "fact", "fact",
        "Payments must re-check the amount at the payment step",
        "Notification emails use templates",
    )


def test_fact_threshold_stricter_than_belief():
    assert CONFLICT_MIN_SIM_FACT > CONFLICT_MIN_SIM
