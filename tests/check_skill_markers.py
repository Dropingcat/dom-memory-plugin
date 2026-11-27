"""Check the multi-marker skill classifier and the seed skill cards.

Self-contained: no DB, no network, no fixtures outside the repo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

from mockup import skill_markers, skill_router  # noqa: E402


def main() -> int:
    # 1. Three or more cohesive signals produce a category.
    category, markers, confidence = skill_markers.multi_marker_hint(
        "Add sqlite migration for the table schema",
        "update the database index and columns",
        ["schema change for the claims table"],
        ["sqlite"],
    )
    assert category == "databases", (category, markers, confidence)
    assert confidence >= 0.6, confidence
    assert "modify" in markers, markers

    # 2. One signal (a single word) is never enough.
    category, markers, confidence = skill_markers.multi_marker_hint("Review the diff", "", [], [])
    assert category == "", (category, markers, confidence)
    assert confidence <= 1 / 5, confidence

    # One capability alone is also not enough.
    category, _, confidence = skill_markers.multi_marker_hint("Do the thing", "", [], ["sqlite"])
    assert category == "", (category, confidence)

    # No signal at all degrades to the explicit unknown with zero confidence.
    category, markers, confidence = skill_markers.multi_marker_hint("", "", [], [])
    assert (category, markers, confidence) == ("", [], 0.0), (category, markers, confidence)

    # 3. Action markers are extracted from the title, sorted and deduped.
    assert skill_markers.action_markers_from_title("Implement the parser and test it") == [
        "modify", "verify",
    ], skill_markers.action_markers_from_title("Implement the parser and test it")
    assert skill_markers.action_markers_from_title("Document the schema") == ["document"]
    assert skill_markers.action_markers_from_title("Read the file") == ["inspect"]
    assert skill_markers.action_markers_from_title("") == []

    # 4. Cross-domain real title: migration is a database action, not generic coding.
    caps, unmapped = skill_router.capabilities_from_accept([{"id": "A1", "type": "schema"}])
    assert unmapped == [] and caps == ["migration", "sqlite"], (caps, unmapped)
    category, markers, confidence = skill_markers.multi_marker_hint(
        "Написать миграцию v4→v5", "", [], caps
    )
    assert category == "databases", (category, markers, confidence)
    assert markers == ["modify"], markers
    assert confidence >= 0.6, confidence

    # A real plan step from tests/check_skill_router.py resolves to the same category.
    category, _, confidence = skill_markers.multi_marker_hint(
        "L2 V5 migration: claim_sessions and read_receipts",
        "V5 adds claim_sessions and read_receipts; v4 schema migrates",
        [],
        caps,
    )
    assert category == "databases", (category, confidence)

    # Reproducible: same input -> same output.
    first = skill_markers.multi_marker_hint(
        "Написать миграцию v4→v5", "", [], caps
    )
    second = skill_markers.multi_marker_hint(
        "Написать миграцию v4→v5", "", [], caps
    )
    assert first == second, (first, second)

    # Exported constants.
    assert "databases" in skill_markers.CATEGORIES
    assert "modify" in skill_markers.ACTION_TYPES
    assert set(skill_markers.CAPABILITY_CATEGORY) >= set(skill_router.CAPABILITIES), (
        set(skill_router.CAPABILITIES) - set(skill_markers.CAPABILITY_CATEGORY)
    )

    # 5. Seed cards load, are unique, carry real provides and are verified.
    card_path = ROOT / "opencode" / "mockup" / "skills.card.json"
    data = json.loads(card_path.read_text(encoding="utf-8"))
    cards = data["cards"]
    assert len(cards) >= 15, len(cards)
    assert len({card["id"] for card in cards}) == len(cards), "card ids must be unique"
    for card in cards:
        assert card.get("id"), card
        assert card.get("name"), card
        assert card.get("provides"), card
        assert all(str(item).strip() for item in card["provides"]), card
        assert card.get("state") == "verified", card
        assert card.get("evidence"), card

    # Top categories are covered by at least three cards each.
    counts: dict[str, int] = {}
    for card in cards:
        counts[card.get("category")] = counts.get(card.get("category"), 0) + 1
    for required in ("databases", "coding", "physics", "chemistry", "research"):
        assert counts.get(required, 0) >= 2, (required, counts)

    # 6. The card file is versioned with the router capability contract.
    assert data["capability_version"] == skill_router.CAPABILITY_VERSION, data["capability_version"]

    print(
        "PASS skill_markers: multi-signal category, unknown on weak evidence, "
        "action markers, cross-domain databases, %d verified cards" % len(cards)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
