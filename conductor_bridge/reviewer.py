"""Senior-style review for Conductor track artifacts (spec.md + plan.md).

This is intentionally heuristic: it provides stable, repeatable feedback that
non-developers can trust as a baseline before any coding begins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .quality_gate import detect_latest_track_id
from .textio import read_text_auto


@dataclass(frozen=True)
class TrackReview:
    score_1_to_10: int
    track_id: str
    issues: list[str]
    strengths: list[str]
    revision_prompt: str


def _find_heading(md: str, candidates: list[str]) -> Optional[str]:
    for h in candidates:
        if re.search(rf"(?im)^\s*##+\s*{re.escape(h)}\s*$", md or ""):
            return h
    return None


def _count_checkboxes(md: str) -> int:
    return len(re.findall(r"(?m)^\s*-\s*\[\s*[xX ]\s*\]\s+", md or ""))


def _count_phases(md: str) -> int:
    return len(re.findall(r"(?im)^\s*##+\s*phase\b", md or ""))


def _count_bullets_in_section(md: str, heading: str) -> int:
    m = re.search(rf"(?im)^\s*##+\s*{re.escape(heading)}\s*$", md or "")
    if not m:
        return 0
    tail = (md or "")[m.end() :]
    stop = re.search(r"(?im)^\s*##+\s+", tail)
    section = tail[: stop.start()] if stop else tail
    return len(re.findall(r"(?m)^\s*-\s+", section))


def _contains_thinking(md: str) -> bool:
    markers = [
        r"\bwait,\b",
        r"\bthis is confusing\b",
        r"\blet'?s re-?read\b",
        r"\brevised logic\b",
        r"\bbrainstorm\b",
        r"\bi will now\b",
    ]
    text = (md or "").lower()
    return any(re.search(p, text) for p in markers)


def _scope_creep_issues(md: str, user_brief: str) -> list[str]:
    creep = [
        ("subtraction", "Subtraction"),
        ("authentication", "Authentication"),
        ("payments", "Payments"),
        ("invoice", "Invoicing"),
        ("database", "Database"),
        ("kubernetes", "Kubernetes"),
    ]
    md_l = (md or "").lower()
    brief_l = (user_brief or "").lower()
    issues: list[str] = []
    for needle, label in creep:
        if needle in md_l and needle not in brief_l:
            issues.append(f"Mentions '{label}' but it's not in the user brief (scope creep).")
    return issues


def review_track(
    *,
    repo_dir: str,
    track_id: Optional[str] = None,
    user_brief: str = "",
) -> TrackReview:
    repo = Path(repo_dir)
    tid = track_id or detect_latest_track_id(repo_dir)
    if not tid:
        return TrackReview(
            score_1_to_10=1,
            track_id="",
            issues=["No track_id found (missing conductor/tracks.md?)."],
            strengths=[],
            revision_prompt="Create a Conductor track first (spec.md + plan.md), then re-run this review.",
        )

    spec_path = repo / "conductor" / "tracks" / tid / "spec.md"
    plan_path = repo / "conductor" / "tracks" / tid / "plan.md"
    if not spec_path.exists() or not plan_path.exists():
        missing = []
        if not spec_path.exists():
            missing.append("spec.md")
        if not plan_path.exists():
            missing.append("plan.md")
        return TrackReview(
            score_1_to_10=1,
            track_id=tid,
            issues=[f"Missing track file(s): {', '.join(missing)}"],
            strengths=[],
            revision_prompt="Regenerate the track so both `spec.md` and `plan.md` exist.",
        )

    spec = read_text_auto(spec_path)
    plan = read_text_auto(plan_path)

    issues: list[str] = []
    strengths: list[str] = []

    if _contains_thinking(spec) or _contains_thinking(plan):
        issues.append("Contains 'thinking out loud' / contradictory drafting text; spec/plan must be final and clean.")

    overview_h = _find_heading(spec, ["Overview", "Summary", "Problem", "Context"])
    if overview_h:
        strengths.append(f"Has a clear `{overview_h}` section.")
    else:
        issues.append("Missing an `Overview`/`Summary` section.")

    ac_h = _find_heading(spec, ["Acceptance Criteria", "Success Criteria", "Definition of Done"])
    if not ac_h:
        issues.append("Missing `Acceptance Criteria` section.")
    else:
        ac_bullets = _count_bullets_in_section(spec, ac_h)
        if ac_bullets < 3:
            issues.append("Acceptance Criteria is too thin (needs at least 3 bullet points).")
        else:
            strengths.append("Has explicit Acceptance Criteria.")

    ng_h = _find_heading(spec, ["Non-goals", "Non Goals", "Out of Scope", "Out-of-scope", "Not in scope"])
    if not ng_h:
        issues.append("Missing `Non-goals`/`Out of Scope` section to prevent scope creep.")
    else:
        strengths.append("Has scope boundaries (Non-goals / Out of Scope).")

    if _count_phases(plan) < 2:
        issues.append("Plan should be organized into multiple phases (at least 2).")
    cb = _count_checkboxes(plan)
    if cb < 6:
        issues.append("Plan has too few actionable checkbox tasks; make tasks concrete and checkable.")
    else:
        strengths.append("Plan uses checkbox tasks.")

    if not re.search(r"(?i)\btest(s|ing)?\b", plan):
        issues.append("Plan is missing explicit testing tasks (unit/integration/manual checks).")
    if not re.search(r"(?i)\bedge case(s)?\b", spec) and not re.search(r"(?i)\bedge case(s)?\b", plan):
        issues.append("Does not call out edge cases explicitly.")

    issues.extend(_scope_creep_issues(spec + "\n" + plan, user_brief))

    score = 10
    for issue in issues:
        il = issue.lower()
        if "thinking out loud" in il:
            score -= 4
        elif "missing `acceptance criteria`" in il:
            score -= 3
        elif "missing `non-goals`" in il or "out of scope" in il:
            score -= 2
        elif "scope creep" in il:
            score -= 2
        else:
            score -= 1
    score = max(1, min(10, score))

    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "- None"
    revision_prompt = (
        "Revise this track's `spec.md` and `plan.md` in-place to be top-quality (senior engineer standard).\n\n"
        "Rules:\n"
        "- Do NOT add features beyond the user brief.\n"
        "- Remove any 'thinking out loud' or contradictory drafting text.\n"
        "- Use clear headings, bullets, and explicit scope boundaries.\n\n"
        "Spec requirements:\n"
        "- Include: Overview, Goals, Non-goals/Out of Scope, Requirements, Acceptance Criteria (bulleted, testable),"
        " Edge Cases, Open Questions.\n\n"
        "Plan requirements:\n"
        "- 2-4 phases.\n"
        "- Each phase has checkbox tasks (`- [ ] ...`).\n"
        "- Include explicit tasks for testing + manual verification.\n"
        "- Keep tasks small and implementable.\n\n"
        f"Issues to fix:\n{issues_text}\n\n"
        "When done, reply: DONE"
    )

    return TrackReview(
        score_1_to_10=int(score),
        track_id=tid,
        issues=issues,
        strengths=strengths,
        revision_prompt=revision_prompt,
    )

