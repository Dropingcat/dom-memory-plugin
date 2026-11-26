"""Check the three-track outcome tracker (E1).

Run: python -B tests/check_skill_outcomes.py

Covers: track separation, C2 (read != call != accept), miss semantics, weight deltas
from the CALL track only, the structural invariant, and reproducibility.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup.skill_outcomes import (  # noqa: E402
    LABEL_ERROR,
    LABEL_EXCESS,
    LABEL_FAILURE,
    LABEL_HIT,
    LABEL_MISS,
    LABEL_UNKNOWN,
    LABELS_BY_TRACK,
    OutcomeTracker,
    SkillOutcome,
    TRACK_ACCEPT,
    TRACK_CALL,
    TRACK_READ,
    TRACKS,
    WEIGHT_DELTAS,
    clamp_weight,
)


def check_tracks_are_separate() -> None:
    """1. read/call/accept land in their own track and are counted there only."""
    tracker = OutcomeTracker()
    tracker.record_read("skill-a", "step-1", group="databases")
    tracker.record_call("skill-a", "step-1", was_queued=True, group="databases")
    tracker.record_accept("skill-a", "step-1", completed=True, was_queued=True, group="databases")

    assert [event.track for event in tracker.events] == [TRACK_READ, TRACK_CALL, TRACK_ACCEPT]
    counts = tracker.grouped_counts()
    assert set(counts) == set(TRACKS), counts
    assert counts[TRACK_READ] == {LABEL_HIT: 1}, counts
    assert counts[TRACK_CALL] == {LABEL_HIT: 1}, counts
    assert counts[TRACK_ACCEPT] == {LABEL_HIT: 1}, counts

    # A fresh tracker reports all three tracks, each empty -- never one merged bucket.
    empty = OutcomeTracker().grouped_counts()
    assert empty == {track: {} for track in TRACKS}, empty
    assert tracker.tracks_invariant()


def check_read_is_not_call() -> None:
    """2. A READ hit is NOT counted as a CALL hit (C2 invariant)."""
    tracker = OutcomeTracker()
    tracker.record_read("skill-a", "step-1", group="databases")

    counts = tracker.grouped_counts()
    assert counts[TRACK_READ] == {LABEL_HIT: 1}, counts
    assert counts[TRACK_CALL] == {}, counts

    # The read signal must not move a learned weight either.
    weights = tracker.update_weights({"databases": 0.5})
    assert weights == {"databases": 0.5}, weights

    # Nor may a read event be re-labelled with a call-only label.
    try:
        tracker.record_read("skill-b", "step-1", label=LABEL_ERROR)
    except ValueError as error:
        assert "read track" in str(error), error
    else:
        raise AssertionError("read track accepted the call-only label 'error'")
    assert tracker.tracks_invariant()


def check_call_labels() -> None:
    """3. CALL: queued -> hit, called outside the queue -> miss, failed -> error."""
    tracker = OutcomeTracker()
    tracker.record_call("skill-a", "step-1", was_queued=True, success=True)
    tracker.record_call("skill-b", "step-1", was_queued=False)          # router missed it
    tracker.record_call("skill-c", "step-1", was_queued=True, success=False)

    labels = [event.label for event in tracker.events]
    assert labels == [LABEL_HIT, LABEL_MISS, LABEL_ERROR], labels
    assert tracker.grouped_counts()[TRACK_CALL] == {
        LABEL_HIT: 1, LABEL_MISS: 1, LABEL_ERROR: 1,
    }
    # No accept label ever appears on the call track.
    assert set(tracker.grouped_counts()[TRACK_CALL]) <= LABELS_BY_TRACK[TRACK_CALL]
    assert tracker.tracks_invariant()


def check_accept_labels() -> None:
    """3b. ACCEPT: hit / failure / excess / unknown by the documented conditions."""
    tracker = OutcomeTracker()
    tracker.record_accept("s1", "step-1", completed=True, was_queued=True)
    tracker.record_accept("s2", "step-1", completed=False, was_queued=True)
    tracker.record_accept("s3", "step-1", completed=True, was_queued=True, was_called=False)
    tracker.record_accept("s4", "step-1", completed=True, was_queued=False)

    labels = [event.label for event in tracker.events]
    assert labels == [LABEL_HIT, LABEL_FAILURE, LABEL_EXCESS, LABEL_UNKNOWN], labels
    assert tracker.grouped_counts()[TRACK_ACCEPT] == {
        LABEL_HIT: 1, LABEL_FAILURE: 1, LABEL_EXCESS: 1, LABEL_UNKNOWN: 1,
    }
    # Accept outcomes never move weights, even the failure label.
    assert tracker.update_weights({"databases": 0.5}) == {"databases": 0.5}
    assert tracker.tracks_invariant()


def check_weight_updates() -> None:
    """4. CALL hit +0.05, miss -0.03 (clipped 0..1); read/accept do not move weights."""
    tracker = OutcomeTracker()
    tracker.record_read("read-only", "step-1", group="databases")
    tracker.record_call("skill-a", "step-1", was_queued=True, group="databases")
    tracker.record_call("skill-b", "step-1", was_queued=False, group="planning")
    tracker.record_call("skill-c", "step-1", was_queued=True, success=False, group="ledger")
    tracker.record_accept("skill-d", "step-1", completed=False, was_queued=True, group="accept-only")

    weights = tracker.update_weights({"databases": 0.5, "planning": 0.5, "ledger": 0.5})
    assert weights["databases"] == 0.55, weights
    assert weights["planning"] == 0.47, weights
    assert weights["ledger"] == 0.45, weights
    assert "accept-only" not in weights, weights
    assert WEIGHT_DELTAS == {LABEL_HIT: 0.05, LABEL_MISS: -0.03, LABEL_ERROR: -0.05}

    # Clipping at both ends, and the input mapping is never mutated.
    baseline = {"databases": 0.98, "planning": 0.01}
    clipped = tracker.update_weights(baseline)
    assert clipped["databases"] == 1.0, clipped
    assert clipped["planning"] == 0.0, clipped  # 0.01 - 0.03 floors at 0
    assert baseline == {"databases": 0.98, "planning": 0.01}, baseline

    # Call without a group cannot be attributed; unpriced labels are skipped.
    unattributed = OutcomeTracker()
    unattributed.record_call("skill-x", "step-1", was_queued=True)
    assert unattributed.update_weights({"databases": 0.5}) == {"databases": 0.5}
    assert clamp_weight(1.7) == 1.0 and clamp_weight(-0.2) == 0.0


def check_invariant() -> None:
    """5-6. tracks_invariant is True clean and False on a stored substitution."""
    clean = OutcomeTracker()
    clean.record_read("skill-a", "step-1")
    clean.record_call("skill-a", "step-1", was_queued=True)
    clean.record_accept("skill-a", "step-1", completed=True, was_queued=True)
    assert clean.tracks_invariant() is True

    # An accept-family label stored on the READ track: the mixed scenario.
    mixed = OutcomeTracker.from_events([
        SkillOutcome(track=TRACK_READ, skill_id="skill-a", label=LABEL_EXCESS, step_id="step-1"),
    ])
    assert mixed.tracks_invariant() is False

    # A CALL event holding an accept-only label is mixing too.
    mixed_call = OutcomeTracker.from_events([
        SkillOutcome(track=TRACK_CALL, skill_id="skill-a", label=LABEL_FAILURE, step_id="step-1"),
    ])
    assert mixed_call.tracks_invariant() is False

    # Unknown tracks are refused by the invariant as well.
    bogus = OutcomeTracker.from_events([
        SkillOutcome(track="merged", skill_id="skill-a", label=LABEL_HIT, step_id="step-1"),
    ])
    assert bogus.tracks_invariant() is False

    # Even mixed data is counted under its own track key, never merged into another.
    counts = mixed.grouped_counts()
    assert counts[TRACK_READ] == {LABEL_EXCESS: 1}, counts
    assert counts[TRACK_CALL] == {} and counts[TRACK_ACCEPT] == {}, counts


def check_reproducible() -> None:
    """Reproducibility: the same events produce byte-identical counts."""
    def build() -> OutcomeTracker:
        tracker = OutcomeTracker()
        tracker.record_read("s1", "p1", session_id="ses-1", group="g1", confidence_bp=700)
        tracker.record_call("s1", "p1", was_queued=True, session_id="ses-1", group="g1")
        tracker.record_call("s2", "p1", was_queued=False, session_id="ses-1", group="g2")
        tracker.record_accept("s1", "p1", completed=False, was_queued=True,
                              session_id="ses-1", group="g1")
        tracker.record_accept("s3", "p1", completed=True, was_queued=False,
                              session_id="ses-1", group="g3")
        return tracker

    left, right = build(), build()
    assert left.grouped_counts() == right.grouped_counts()
    assert left.update_weights({"g1": 0.5}) == right.update_weights({"g1": 0.5})
    assert [event.to_dict() for event in left.events] == [event.to_dict() for event in right.events]

    # Events are immutable: tampering fails instead of rewriting history.
    event = left.events[0]
    try:
        event.label = LABEL_MISS  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("SkillOutcome must be frozen")


def check_tau_parity() -> None:
    """7-8. The tau fingerprint is a fixed vector shared with the JS gate.

    The JS gate (``opencode/gates/tau_gate.js``) produces the same 64-hex digest for
    this state, so "the block changed" cannot mean different things in the two
    processes.  The idle counter is checked here on the Python side of the contract;
    the JS-side increment is covered by ``tests/check_tau_gate.mjs``.
    """
    from mockup.step_registry import BlockFingerprint

    golden = '75e3043b31cb45481a9563aee6104de61c0dd47aa4194e026a5dbf4d38007dc2'
    fingerprint = BlockFingerprint(
        step_id='pt-2', cap_set=frozenset({'testing', 'sqlite'}),
        category_hint='databases', markers=frozenset({'modify'}),
        plan_version=3, index_digest='idx-a',
    )
    shuffled = BlockFingerprint(
        step_id='pt-2', cap_set=frozenset({'sqlite', 'testing'}),
        category_hint='databases', markers=frozenset({'modify'}),
        plan_version=3, index_digest='idx-a',
    )
    assert fingerprint.digest() == golden, fingerprint.digest()
    assert shuffled.digest() == golden

    assert fingerprint.advance(None).idle_reissue_count == 0
    carried = fingerprint.advance(fingerprint)
    assert carried.idle_reissue_count == 1
    assert carried.advance(carried).idle_reissue_count == 2
    changed = BlockFingerprint(
        step_id='pt-2', cap_set=frozenset({'testing', 'sqlite'}),
        category_hint='databases', markers=frozenset({'modify'}),
        plan_version=4, index_digest='idx-a',
    )
    assert changed.advance(fingerprint).idle_reissue_count == 0


def main() -> int:
    check_tracks_are_separate()
    check_read_is_not_call()
    check_call_labels()
    check_accept_labels()
    check_weight_updates()
    check_invariant()
    check_reproducible()
    check_tau_parity()
    print("PASS skill_outcomes: three independent tracks, C2 invariant, "
          "call-only weight deltas, reproducible counts, tau fixed vector")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
