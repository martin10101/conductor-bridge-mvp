"""Automate Gemini CLI Conductor flows in non-interactive mode.

Conductor (the Gemini CLI extension) is designed as an interactive workflow, but
Gemini CLI supports resuming sessions via `--resume`. We use that to run the flow
turn-by-turn, automatically answering prompts with best-effort heuristics.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ConductorRunResult:
    ok: bool
    model: str
    session_id: Optional[str]
    transcript: str
    created_paths: list[str]
    error: Optional[str] = None


def _snapshot_tree(root: Path, relative_to: Path) -> set[str]:
    out: set[str] = set()
    if not root.exists():
        return out
    for p in root.rglob("*"):
        try:
            if p.is_file():
                out.add(str(p.relative_to(relative_to)))
        except Exception:
            continue
    return out


def _read_setup_state(repo_dir: Path) -> Optional[str]:
    state_file = repo_dir / "conductor" / "setup_state.json"
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            step = data.get("last_successful_step")
            return str(step) if step else None
    except Exception:
        return None
    return None


def _pick_choice_letter(text: str) -> Optional[str]:
    matches = list(re.finditer(r"(?m)^\s*([A-Z])\.\s*Type your own answer\s*$", text))
    if not matches:
        return None
    return matches[-1].group(1)


def _needs_yes_no(text: str) -> bool:
    lowered = text.lower()
    return ("(yes/no)" in lowered) or ("yes/no" in lowered) or ("do you approve" in lowered) or ("confirm" in lowered)


def _looks_like_question(text: str) -> bool:
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return False
    tail = lines[-1].strip()
    if tail.endswith("?"):
        return True
    lowered = tail.lower()
    return any(
        key in lowered
        for key in (
            "please provide",
            "please enter",
            "does this",
            "is this correct",
            "what would you like to do",
            "please suggest any changes",
        )
    )


def _build_generic_answer(prompt_text: str, *, project_brief: str, track_description: str) -> str:
    lowered = (prompt_text or "").lower()

    if "track" in lowered and ("description" in lowered or "brief" in lowered):
        return track_description
    if "users" in lowered or "audience" in lowered:
        return "Anyone who wants a quick laugh; non-developers; kids/teens."
    if "tech" in lowered or "technology" in lowered or "stack" in lowered or "language" in lowered:
        return "Plain HTML + CSS + JavaScript (no frameworks, no build step)."
    if "workflow" in lowered:
        return "Keep it simple: implement, manually test in browser, then commit."
    if "goal" in lowered or "vision" in lowered:
        return "A tiny funny web app that makes letters 'grow' when added to themselves."
    if "guidelines" in lowered or "tone" in lowered or "brand" in lowered or "design" in lowered:
        return "Playful but clear. Big readable text. Friendly error messages."
    if "brownfield" in lowered or "existing project" in lowered:
        return project_brief

    return project_brief


class ConductorCliDriver:
    def __init__(self, *, gemini_path: str):
        self.gemini_path = gemini_path

    def run_setup(
        self,
        *,
        repo_dir: str,
        model: str,
        approval_mode: str = "yolo",
        timeout_s: int = 900,
        project_brief: str,
        track_description: str,
        max_turns: int = 20,
    ) -> ConductorRunResult:
        repo = Path(repo_dir)
        before = _snapshot_tree(repo / "conductor", relative_to=repo)

        ok, session_id, transcript, error = self._run_flow(
            repo=repo,
            model=model,
            approval_mode=approval_mode,
            timeout_s=timeout_s,
            max_turns=max_turns,
            initial_prompt="/conductor:setup",
            project_brief=project_brief,
            track_description=track_description,
            done_check=lambda: _read_setup_state(repo) == "3.3_initial_track_generated",
        )

        after = _snapshot_tree(repo / "conductor", relative_to=repo)
        created = sorted(after - before)
        return ConductorRunResult(
            ok=ok,
            model=model,
            session_id=session_id,
            transcript=transcript,
            created_paths=created,
            error=error,
        )

    def run_new_track(
        self,
        *,
        repo_dir: str,
        model: str,
        approval_mode: str = "yolo",
        timeout_s: int = 900,
        project_brief: str,
        track_description: str,
        max_turns: int = 20,
    ) -> ConductorRunResult:
        repo = Path(repo_dir)
        tracks_dir = repo / "conductor" / "tracks"
        before = _snapshot_tree(tracks_dir, relative_to=repo)

        ok, session_id, transcript, error = self._run_flow(
            repo=repo,
            model=model,
            approval_mode=approval_mode,
            timeout_s=timeout_s,
            max_turns=max_turns,
            initial_prompt=f'/conductor:newTrack "{track_description}"',
            project_brief=project_brief,
            track_description=track_description,
            done_check=lambda: bool(_snapshot_tree(tracks_dir, relative_to=repo) - before),
        )

        after = _snapshot_tree(tracks_dir, relative_to=repo)
        created = sorted(after - before)
        return ConductorRunResult(
            ok=ok,
            model=model,
            session_id=session_id,
            transcript=transcript,
            created_paths=created,
            error=error,
        )

    def _run_flow(
        self,
        *,
        repo: Path,
        model: str,
        approval_mode: str,
        timeout_s: int,
        max_turns: int,
        initial_prompt: str,
        project_brief: str,
        track_description: str,
        done_check,
    ) -> tuple[bool, Optional[str], str, Optional[str]]:
        start = time.time()
        transcript_parts: list[str] = []

        session_id: Optional[str] = None
        prompt = initial_prompt

        for _turn in range(max_turns):
            if done_check():
                return True, session_id, "".join(transcript_parts), None
            if time.time() - start > timeout_s:
                return False, session_id, "".join(transcript_parts), "timeout"

            ok, new_session_id, assistant_text, raw_out, err = self._run_turn(
                repo=repo,
                model=model,
                approval_mode=approval_mode,
                session_id=session_id,
                prompt=prompt,
            )
            session_id = new_session_id or session_id

            transcript_parts.append(f"[user] {prompt}\n")
            if assistant_text:
                transcript_parts.append(assistant_text.rstrip() + "\n")
            if err:
                transcript_parts.append(f"[gemini_error] {err}\n")
            if raw_out and not assistant_text:
                # Keep a little raw output for debugging.
                transcript_parts.append(raw_out[-2000:] + "\n")

            if done_check():
                return True, session_id, "".join(transcript_parts), None

            if not ok and err:
                # If a turn fails, stop early (Conductor prompts often instruct to halt).
                return False, session_id, "".join(transcript_parts), err

            if not assistant_text:
                # No assistant output; nudge with brief.
                prompt = project_brief
                continue

            if _needs_yes_no(assistant_text):
                prompt = "yes"
                continue

            choice = _pick_choice_letter(assistant_text)
            if choice:
                prompt = choice
                continue

            if _looks_like_question(assistant_text):
                prompt = _build_generic_answer(
                    assistant_text,
                    project_brief=project_brief,
                    track_description=track_description,
                )
                continue

            # If it's not a question, ask it to continue.
            prompt = "continue"

        return False, session_id, "".join(transcript_parts), "max_turns_exceeded"

    def _run_turn(
        self,
        *,
        repo: Path,
        model: str,
        approval_mode: str,
        session_id: Optional[str],
        prompt: str,
    ) -> tuple[bool, Optional[str], str, str, Optional[str]]:
        gemini_exec: list[str]
        gemini_path = self.gemini_path
        if gemini_path.lower().endswith((".cmd", ".bat")):
            gemini_exec = ["cmd.exe", "/c", gemini_path]
        else:
            gemini_exec = [gemini_path]

        args = [
            *gemini_exec,
            "--output-format",
            "stream-json",
            "--approval-mode",
            approval_mode,
            "--model",
            model,
            "--extensions",
            "conductor",
        ]
        if session_id:
            args += ["--resume", session_id]
        args += [prompt]

        try:
            result = subprocess.run(
                args,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=600,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return False, session_id, "", "", "turn_timeout"
        except Exception as e:
            return False, session_id, "", "", f"{type(e).__name__}: {e}"

        raw = (result.stdout or "") + (result.stderr or "")

        sid: Optional[str] = None
        assistant_chunks: list[str] = []
        turn_err: Optional[str] = None

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "init" and obj.get("session_id"):
                sid = str(obj["session_id"])
            if obj.get("type") == "message" and obj.get("role") == "assistant":
                content = obj.get("content")
                if isinstance(content, str):
                    assistant_chunks.append(content)
            if obj.get("type") == "result":
                status = obj.get("status")
                if status and status != "success":
                    turn_err = f"result_status={status}"

        assistant_text = "".join(assistant_chunks).strip()
        ok = result.returncode == 0 and turn_err is None
        return ok, sid, assistant_text, raw, turn_err
