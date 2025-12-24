"""Heuristic spec/plan quality checks for Conductor artifacts.

Goal: catch the common failure modes:
- scope creep (inventing extra features)
- unfinished / contradictory specs ("wait, this is confusing...")
- missing non-goals / acceptance criteria
- plans that don't follow basic structure
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .textio import read_text_auto


@dataclass(frozen=True)
class QualityGateResult:
    ok: bool
    profile: str
    issues: list[str]
    track_id: Optional[str] = None


def _has_heading(md: str, heading: str) -> bool:
    pat = rf"(?im)^\s*##+\s*{re.escape(heading)}\s*$"
    return re.search(pat, md or "") is not None


def _count_bullets_in_section(md: str, heading: str) -> int:
    # Very lightweight: count "- " bullets until next "##" heading.
    m = re.search(rf"(?im)^\s*##+\s*{re.escape(heading)}\s*$", md or "")
    if not m:
        return 0
    tail = (md or "")[m.end() :]
    stop = re.search(r"(?im)^\s*##+\s+", tail)
    section = tail[: stop.start()] if stop else tail
    return len(re.findall(r"(?m)^\s*-\s+", section))


def _contains_thinking_out_loud(md: str) -> bool:
    bad_markers = [
        r"\bwait,\b",
        r"\bthis is confusing\b",
        r"\blet'?s re-?read\b",
        r"\brevised logic\b",
        r"\bi will now\b",  # shouldn't appear in a spec/plan file
    ]
    text = (md or "").lower()
    return any(re.search(p, text) for p in bad_markers)


def _mentions_unasked_features(md: str, user_brief: str) -> list[str]:
    # Minimal: only flag a small set of common scope-creep keywords if absent in the brief.
    creep = [
        ("subtraction", "Subtraction"),
        ("payments", "Payments"),
        ("invoic", "Invoicing"),
        ("kubernetes", "Kubernetes"),
        ("docker", "Docker"),
        ("rbac", "RBAC"),
    ]
    md_l = (md or "").lower()
    brief_l = (user_brief or "").lower()
    issues: list[str] = []
    for needle, label in creep:
        if needle in md_l and needle not in brief_l:
            issues.append(f"Spec/plan mentions '{label}' but it's not in the user brief (possible scope creep).")
    return issues


def detect_latest_track_id(repo_dir: str) -> Optional[str]:
    repo = Path(repo_dir)
    tracks_file = repo / "conductor" / "tracks.md"
    if not tracks_file.exists():
        return None
    txt = read_text_auto(tracks_file)
    matches = re.findall(r"\(\.\/conductor\/tracks\/([^/]+)\/\)", txt)
    return matches[-1] if matches else None


def evaluate_track(
    *,
    repo_dir: str,
    track_id: Optional[str] = None,
    profile: str = "auto",
    user_brief: str = "",
) -> QualityGateResult:
    repo = Path(repo_dir)
    tid = track_id or detect_latest_track_id(repo_dir)
    if not tid:
        return QualityGateResult(ok=False, profile=profile, issues=["No track_id found (missing conductor/tracks.md?)."])

    spec_path = repo / "conductor" / "tracks" / tid / "spec.md"
    plan_path = repo / "conductor" / "tracks" / tid / "plan.md"
    if not spec_path.exists() or not plan_path.exists():
        missing = []
        if not spec_path.exists():
            missing.append("spec.md")
        if not plan_path.exists():
            missing.append("plan.md")
        return QualityGateResult(ok=False, profile=profile, issues=[f"Missing track file(s): {', '.join(missing)}"], track_id=tid)

    spec = read_text_auto(spec_path)
    plan = read_text_auto(plan_path)

    chosen_profile = profile
    if profile == "auto":
        # If it looks like a small static project (no package.json backend), default to micro.
        chosen_profile = "micro" if not (repo / "package.json").exists() else "project"

    issues: list[str] = []

    if _contains_thinking_out_loud(spec):
        issues.append("Spec contains 'thinking out loud' / unresolved reasoning; it must be a clean, final spec.")
    if _contains_thinking_out_loud(plan):
        issues.append("Plan contains meta-commentary; it must be a clean, final plan.")

    # Always require non-goals + acceptance criteria.
    if not (_has_heading(spec, "Non-goals") or _has_heading(spec, "Out of Scope") or _has_heading(spec, "Non-Goals")):
        issues.append("Spec is missing a 'Non-goals' (or 'Out of Scope') section.")

    ac_bullets = _count_bullets_in_section(spec, "Acceptance Criteria")
    if ac_bullets < (4 if chosen_profile == "micro" else 6):
        issues.append(f"Spec acceptance criteria is too weak (found {ac_bullets} bullet(s)).")

    # Plan structure checks.
    if not re.search(r"(?im)^\s*##\s*Phase\b", plan):
        issues.append("Plan should be structured into 'Phase' sections (## Phase ...).")
    if not re.search(r"(?m)^\s*-\s*\[\s*\]\s*Task:", plan):
        issues.append("Plan should contain checkbox tasks in the form '- [ ] Task: ...'.")

    # Scope creep (best-effort).
    issues.extend(_mentions_unasked_features(spec, user_brief))
    issues.extend(_mentions_unasked_features(plan, user_brief))

    return QualityGateResult(ok=len(issues) == 0, profile=chosen_profile, issues=issues, track_id=tid)
