"""§1.7 banned-pattern lint over every agent-visible protocol string."""

from __future__ import annotations

from coop.sim.protocol import (
    BANNED_PATTERNS,
    OBJECTIVE_BLOCKS,
    banned_hits,
    lint_surfaces,
    objective_word_counts,
    steps_for,
    text_variants,
)
from coop.sim.protocol import render, dummy_placeholders


def test_banned_list_matches_protocol():
    # The list is the protocol's list. A drift here should be a deliberate edit.
    assert any("experiment" in pat for pat in BANNED_PATTERNS)
    assert any("recruit" in pat for pat in BANNED_PATTERNS)
    joined = " ".join(BANNED_PATTERNS)
    assert "tests" not in joined or "test(ing)" in joined


def test_every_surface_is_clean():
    dirty = []
    for label, text in lint_surfaces():
        hits = banned_hits(text)
        if hits:
            dirty.append((label, hits, text[:180]))
    assert dirty == []


def test_objective_blocks_are_in_range():
    counts = objective_word_counts()
    for name, count in counts.items():
        if name == "null":
            assert count == 80
        else:
            assert 78 <= count <= 83


def test_null_steps_match_length_band():
    """Null ladder lines sit inside the min/max word count of the matching objective lines."""
    ph = dummy_placeholders()
    bands: dict[str, list[int]] = {}
    null_counts: dict[str, int] = {}
    for objective in ("resource_capture", "blame_avoidance", "loyalty", "null"):
        for step in steps_for(objective):
            if step.stage < 3:
                continue
            # Match on the numeric suffix so R3.1 lines up with N3.1.
            key = step.id[1:]
            words = [len(render(text, ph).split()) for _key, text in text_variants(step)]
            if objective == "null":
                null_counts[key] = max(words)
            else:
                bands.setdefault(key, []).extend(words)
    for key, count in null_counts.items():
        assert key in bands, key
        assert min(bands[key]) <= count <= max(bands[key]), (key, count, min(bands[key]), max(bands[key]))


def test_objective_blocks_do_not_name_the_arm():
    for name, text in OBJECTIVE_BLOCKS.items():
        assert name.replace("_", " ") not in text.lower()
        assert name not in text.lower()
