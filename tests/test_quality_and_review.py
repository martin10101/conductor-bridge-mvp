from __future__ import annotations

from pathlib import Path

from conductor_bridge.quality_gate import evaluate_track
from conductor_bridge.reviewer import review_track


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_good_track_passes_gate_and_scores_high(tmp_path: Path):
    repo = tmp_path
    _write(repo / "conductor" / "tracks.md", "# Project Tracks\n\n- [ ] Track: Demo\n  (./conductor/tracks/demo_1/)\n")

    spec = """# Track Specification: Demo

## Overview
- Build a tiny funny alphabet calculator.

## Goals
- Support A + A = B and B... growth.

## Non-goals
- No subtraction.
- No auth.

## Requirements
- Parse inputs like "BB + BB".
- Show a clear error for invalid input.

## Edge Cases
- Mixed tokens (A + B) should error.

## Acceptance Criteria
- A + A returns B.
- B + B returns BB.
- BB + BB returns BBB.
- Chain generator produces multiple lines.

## Open Questions
- None.
"""
    plan = """# Track Plan: Demo

## Phase 1: Logic
- [ ] Task: Implement parser for expressions like "BB + BB".
- [ ] Task: Implement evaluation rules for A and B strings.
- [ ] Task: Add explicit edge case handling (A + B, invalid chars).

## Phase 2: UI
- [ ] Task: Wire keypad buttons to input field and Calculate.
- [ ] Task: Show result and error message clearly.

## Phase 3: Verification
- [ ] Task: Manual test the acceptance criteria in a browser.
"""
    _write(repo / "conductor" / "tracks" / "demo_1" / "spec.md", spec)
    _write(repo / "conductor" / "tracks" / "demo_1" / "plan.md", plan)

    gate = evaluate_track(repo_dir=str(repo), profile="micro", user_brief="funny alphabet calculator; no subtraction")
    assert gate.ok is True

    r = review_track(repo_dir=str(repo), user_brief="funny alphabet calculator; no subtraction")
    assert r.score_1_to_10 >= 8


def test_bad_track_gets_low_score(tmp_path: Path):
    repo = tmp_path
    _write(repo / "conductor" / "tracks.md", "# Project Tracks\n\n- [ ] Track: Demo\n  (./conductor/tracks/demo_1/)\n")
    _write(
        repo / "conductor" / "tracks" / "demo_1" / "spec.md",
        "# Spec\n\n## Overview\nWait, this is confusing.\n",
    )
    _write(repo / "conductor" / "tracks" / "demo_1" / "plan.md", "# Plan\n\n- do stuff\n")

    r = review_track(repo_dir=str(repo), user_brief="funny alphabet calculator")
    assert r.score_1_to_10 <= 4
