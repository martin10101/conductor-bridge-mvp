"""Conductor Bridge MCP server (Streamable HTTP + stdio).

This implements MCP using JSON-RPC 2.0 (required by Codex/Cursor MCP clients).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import tempfile
import subprocess
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .autopilot import create_project, push_branch
from .conductor_cli_driver import ConductorCliDriver
from .gemini_client import GeminiClient
from .implementer import get_best_available_implementer, get_implementer
from .quality_gate import evaluate_track
from .state import StateManager

MCP_PROTOCOL_VERSION = "2024-11-05"


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _as_text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}


def _parse_extensions_list(extensions: Optional[list[str]]) -> list[str]:
    if extensions is None:
        env_extensions = os.environ.get("CONDUCTOR_BRIDGE_GEMINI_EXTENSIONS") or ""
        return [e.strip() for e in env_extensions.split(",") if e.strip()]
    return [e for e in extensions if e]


def _artifact_header(model: Optional[str], extensions: list[str]) -> str:
    ext = ",".join(extensions) if extensions else "default"
    return f"<!-- conductor-bridge: model={model or 'default'} extensions={ext} -->\n\n"


def _strip_markdown_preface(text: str) -> str:
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            return "\n".join(lines[i:]).lstrip() + "\n"
    return text


def _validate_artifact_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("Artifact name must be a non-empty string")
    if Path(name).name != name:
        raise ValueError("Artifact name must be a filename (no directories)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("Artifact name contains invalid characters")
    return name


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        finally:
            raise


class Bridge:
    """Business logic for conductor-bridge tools."""

    def __init__(self, state_dir: str):
        self.state_manager = StateManager(state_dir)
        self.gemini_client = GeminiClient()

    def _write_artifact(self, name: str, content: str, artifacts_dir: Optional[str] = None) -> None:
        safe_name = _validate_artifact_name(name)
        if artifacts_dir:
            _atomic_write_text(Path(artifacts_dir) / safe_name, content)
            return
        self.state_manager.write_artifact(safe_name, content)

    def _read_artifact(self, name: str, artifacts_dir: Optional[str] = None) -> Optional[str]:
        safe_name = _validate_artifact_name(name)
        if artifacts_dir:
            p = Path(artifacts_dir) / safe_name
            return p.read_text(encoding="utf-8") if p.exists() else None
        return self.state_manager.read_artifact(safe_name)

    def tools_list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "ping",
                "description": "Health check for conductor-bridge.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_status",
                "description": "Return tool + environment status.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_state",
                "description": "Read the current bridge state.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "set_state",
                "description": "Merge a partial update into bridge state.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"partial_update": {"type": "object", "additionalProperties": True}},
                    "required": ["partial_update"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "append_event",
                "description": "Append an event to the event log (state/events.jsonl).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "payload": {"type": "object", "additionalProperties": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "run_shell_command",
                "description": "Execute a PowerShell command (Gemini CLI Conductor compatibility).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout_s": {"type": "integer"},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_artifacts",
                "description": "Read spec.md, plan.md, handoff.md, and review.md from artifacts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"artifacts_dir": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "write_artifact",
                "description": "Write an artifact under state/artifacts (e.g. plan.md, handoff.md, review.md).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "content": {"type": "string"},
                        "artifacts_dir": {"type": "string"},
                    },
                    "required": ["name", "content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "autopilot_create_project",
                "description": "Create a new local project folder + GitHub repo (pushes main).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "idea": {"type": "string"},
                        "name": {"type": "string"},
                        "root_dir": {"type": "string"},
                        "visibility": {"type": "string", "enum": ["public", "private", "internal"]},
                        "artifacts_subdir": {"type": "string"},
                    },
                    "required": ["idea"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "autopilot_push_branch",
                "description": "Create a new branch, commit all changes, and push to origin.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {"type": "string"},
                        "message": {"type": "string"},
                        "branch_prefix": {"type": "string"},
                    },
                    "required": ["repo_dir"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "conductor_setup",
                "description": "Run real Gemini CLI Conductor `/conductor:setup` inside a repo to generate `conductor/` markdown files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {"type": "string"},
                        "project_brief": {"type": "string"},
                        "track_description": {"type": "string"},
                        "model": {"type": "string"},
                        "approval_mode": {"type": "string", "enum": ["default", "auto_edit", "yolo"]},
                        "timeout_s": {"type": "integer"},
                        "quality_profile": {"type": "string", "enum": ["auto", "micro", "project"]},
                        "quality_retries": {"type": "integer"},
                    },
                    "required": ["repo_dir", "project_brief", "track_description"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "conductor_new_track",
                "description": "Run real Gemini CLI Conductor `/conductor:newTrack` inside a repo to generate track spec/plan files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {"type": "string"},
                        "project_brief": {"type": "string"},
                        "track_description": {"type": "string"},
                        "model": {"type": "string"},
                        "approval_mode": {"type": "string", "enum": ["default", "auto_edit", "yolo"]},
                        "timeout_s": {"type": "integer"},
                        "quality_profile": {"type": "string", "enum": ["auto", "micro", "project"]},
                        "quality_retries": {"type": "integer"},
                    },
                    "required": ["repo_dir", "project_brief", "track_description"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "conductor_continue",
                "description": "Continue a paused Conductor session with a user answer (e.g. 'A', 'B', 'yes', 'no').",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {"type": "string"},
                        "session_id": {"type": "string"},
                        "user_input": {"type": "string"},
                        "project_brief": {"type": "string"},
                        "track_description": {"type": "string"},
                        "model": {"type": "string"},
                        "approval_mode": {"type": "string", "enum": ["default", "auto_edit", "yolo"]},
                        "timeout_s": {"type": "integer"},
                    },
                    "required": ["repo_dir", "session_id", "user_input"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "generate_spec",
                "description": "Ask Gemini to generate spec.md (requirements).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_description": {"type": "string"},
                        "context": {"type": "string"},
                        "model": {"type": "string"},
                        "extensions": {"type": "array", "items": {"type": "string"}},
                        "artifacts_dir": {"type": "string"},
                    },
                    "required": ["task_description"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "generate_plan",
                "description": "Ask Gemini to generate plan.md and advance the state to implementing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_description": {"type": "string"},
                        "context": {"type": "string"},
                        "model": {"type": "string"},
                        "extensions": {"type": "array", "items": {"type": "string"}},
                        "artifacts_dir": {"type": "string"},
                    },
                    "required": ["task_description"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "submit_handoff",
                "description": "Write handoff.md and advance the state to awaiting_review.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"handoff_markdown": {"type": "string"}, "artifacts_dir": {"type": "string"}},
                    "required": ["handoff_markdown"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "generate_review",
                "description": "Ask Gemini to review handoff.md against plan.md; writes review.md and completes the cycle.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plan": {"type": "string"},
                        "implementation": {"type": "string"},
                        "model": {"type": "string"},
                        "extensions": {"type": "array", "items": {"type": "string"}},
                        "artifacts_dir": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "run_cycle",
                "description": "Run a full plan->implement->review cycle using an implementer backend.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"implementer": {"type": "string", "enum": ["simulate", "codex_cli", "claude_cli"]}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "pause",
                "description": "Pause the loop.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "resume",
                "description": "Resume the loop.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = getattr(self, f"tool_{name}", None)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        return tool(**arguments)

    def tool_ping(self) -> dict[str, Any]:
        return {"status": "ok", "message": "conductor-bridge is running"}

    def tool_get_state(self) -> dict[str, Any]:
        return self.state_manager.get_state().to_dict()

    def tool_set_state(self, partial_update: dict[str, Any]) -> dict[str, Any]:
        return self.state_manager.set_state(partial_update).to_dict()

    def tool_get_artifacts(self, artifacts_dir: Optional[str] = None) -> dict[str, Any]:
        return {
            "spec": self._read_artifact("spec.md", artifacts_dir=artifacts_dir),
            "plan": self._read_artifact("plan.md", artifacts_dir=artifacts_dir),
            "handoff": self._read_artifact("handoff.md", artifacts_dir=artifacts_dir),
            "review": self._read_artifact("review.md", artifacts_dir=artifacts_dir),
        }

    def tool_append_event(self, type: str = "unknown", payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self.state_manager.append_event(type, payload or {}).to_dict()

    def tool_run_shell_command(self, command: str, cwd: Optional[str] = None, timeout_s: int = 60) -> dict[str, Any]:
        workdir = cwd or os.getcwd()
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=False,
            )
            return {
                "ok": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "exit_code": -1, "stdout": "", "stderr": f"Timed out after {timeout_s}s"}
        except Exception as e:
            return {"ok": False, "exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}

    def tool_write_artifact(self, name: str, content: str, artifacts_dir: Optional[str] = None) -> dict[str, Any]:
        self._write_artifact(name, content, artifacts_dir=artifacts_dir)
        return {"ok": True, "artifact": _validate_artifact_name(name), "artifacts_dir": artifacts_dir}

    def tool_autopilot_create_project(
        self,
        idea: str,
        name: str = "",
        root_dir: str = "",
        visibility: str = "public",
        artifacts_subdir: str = ".conductor-bridge/artifacts",
    ) -> dict[str, Any]:
        default_root = str(Path.home() / "Downloads" / "codex-projects")
        chosen_root = root_dir or os.environ.get("CONDUCTOR_BRIDGE_PROJECTS_DIR") or default_root
        info = create_project(
            idea=idea,
            name=name or None,
            root_dir=chosen_root,
            visibility=visibility,
            artifacts_subdir=artifacts_subdir,
        )
        return {
            "ok": True,
            "project_dir": info.project_dir,
            "repo_name": info.repo_name,
            "remote_url": info.remote_url,
            "artifacts_dir": info.artifacts_dir,
        }

    def tool_autopilot_push_branch(
        self,
        repo_dir: str,
        message: str = "Auto update",
        branch_prefix: str = "auto",
    ) -> dict[str, Any]:
        result = push_branch(repo_dir=repo_dir, message=message, branch_prefix=branch_prefix)
        return {"ok": True, "result": result}

    def tool_conductor_setup(
        self,
        repo_dir: str,
        project_brief: str,
        track_description: str,
        model: Optional[str] = None,
        approval_mode: str = "yolo",
        timeout_s: int = 900,
        quality_profile: str = "auto",
        quality_retries: int = 2,
    ) -> dict[str, Any]:
        driver = ConductorCliDriver(gemini_path=self.gemini_client.gemini_path or "gemini")
        selected_model = model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL") or "gemini-3-flash-preview"
        result = driver.run_setup(
            repo_dir=repo_dir,
            model=selected_model,
            approval_mode=approval_mode,
            timeout_s=timeout_s,
            project_brief=project_brief,
            track_description=track_description,
        )

        gate = None
        if result.ok:
            gate = evaluate_track(repo_dir=repo_dir, profile=quality_profile, user_brief=project_brief)
            attempt = 0
            while gate is not None and (not gate.ok) and attempt < max(0, int(quality_retries)) and result.session_id:
                attempt += 1
                issues_text = "\n".join(f"- {i}" for i in gate.issues)
                revise_prompt = (
                    "Quality gate failed for the generated track `spec.md`/`plan.md`.\n\n"
                    "Fix the track's files now, WITHOUT adding extra scope.\n"
                    "Hard requirements:\n"
                    "- Spec must include: Non-goals/Out of Scope AND Acceptance Criteria (bullet list).\n"
                    "- Spec must be final and clean (no 'wait/confusing/revised logic' or brainstorming).\n"
                    "- Plan must be phased and contain checkbox tasks ('- [ ] Task: ...').\n\n"
                    f"Issues found:\n{issues_text}\n\n"
                    "When done, reply: DONE"
                )
                cont = driver.continue_session(
                    repo_dir=repo_dir,
                    model=selected_model,
                    session_id=result.session_id,
                    user_input=revise_prompt,
                    approval_mode=approval_mode,
                    timeout_s=timeout_s,
                    project_brief=project_brief,
                    track_description=track_description,
                    done_check=lambda: False,
                )
                result = cont
                if result.paused_for_user:
                    break
                gate = evaluate_track(repo_dir=repo_dir, profile=quality_profile, user_brief=project_brief)
        return {
            "ok": result.ok,
            "model": selected_model,
            "session_id": result.session_id,
            "created_paths": result.created_paths,
            "error": result.error,
            "paused_for_user": result.paused_for_user,
            "user_prompt": result.user_prompt,
            "user_choices": result.user_choices,
            "quality_gate": None
            if gate is None
            else {"ok": gate.ok, "profile": gate.profile, "issues": gate.issues, "track_id": gate.track_id},
            "transcript_tail": result.transcript[-4000:],
        }

    def tool_conductor_new_track(
        self,
        repo_dir: str,
        project_brief: str,
        track_description: str,
        model: Optional[str] = None,
        approval_mode: str = "yolo",
        timeout_s: int = 900,
        quality_profile: str = "auto",
        quality_retries: int = 2,
    ) -> dict[str, Any]:
        driver = ConductorCliDriver(gemini_path=self.gemini_client.gemini_path or "gemini")
        selected_model = model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL") or "gemini-3-flash-preview"
        result = driver.run_new_track(
            repo_dir=repo_dir,
            model=selected_model,
            approval_mode=approval_mode,
            timeout_s=timeout_s,
            project_brief=project_brief,
            track_description=track_description,
        )

        gate = None
        if result.ok:
            gate = evaluate_track(repo_dir=repo_dir, profile=quality_profile, user_brief=project_brief)
            attempt = 0
            while gate is not None and (not gate.ok) and attempt < max(0, int(quality_retries)) and result.session_id:
                attempt += 1
                issues_text = "\n".join(f"- {i}" for i in gate.issues)
                revise_prompt = (
                    "Quality gate failed for the generated track `spec.md`/`plan.md`.\n\n"
                    "Fix the track's files now, WITHOUT adding extra scope.\n"
                    "Hard requirements:\n"
                    "- Spec must include: Non-goals/Out of Scope AND Acceptance Criteria (bullet list).\n"
                    "- Spec must be final and clean (no 'wait/confusing/revised logic' or brainstorming).\n"
                    "- Plan must be phased and contain checkbox tasks ('- [ ] Task: ...').\n\n"
                    f"Issues found:\n{issues_text}\n\n"
                    "When done, reply: DONE"
                )
                cont = driver.continue_session(
                    repo_dir=repo_dir,
                    model=selected_model,
                    session_id=result.session_id,
                    user_input=revise_prompt,
                    approval_mode=approval_mode,
                    timeout_s=timeout_s,
                    project_brief=project_brief,
                    track_description=track_description,
                    done_check=lambda: False,
                )
                result = cont
                if result.paused_for_user:
                    break
                gate = evaluate_track(repo_dir=repo_dir, profile=quality_profile, user_brief=project_brief)
        return {
            "ok": result.ok,
            "model": selected_model,
            "session_id": result.session_id,
            "created_paths": result.created_paths,
            "error": result.error,
            "paused_for_user": result.paused_for_user,
            "user_prompt": result.user_prompt,
            "user_choices": result.user_choices,
            "quality_gate": None
            if gate is None
            else {"ok": gate.ok, "profile": gate.profile, "issues": gate.issues, "track_id": gate.track_id},
            "transcript_tail": result.transcript[-4000:],
        }

    def tool_conductor_continue(
        self,
        repo_dir: str,
        session_id: str,
        user_input: str,
        project_brief: str = "",
        track_description: str = "",
        model: Optional[str] = None,
        approval_mode: str = "yolo",
        timeout_s: int = 900,
    ) -> dict[str, Any]:
        driver = ConductorCliDriver(gemini_path=self.gemini_client.gemini_path or "gemini")
        selected_model = model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL") or "gemini-3-flash-preview"
        result = driver.continue_session(
            repo_dir=repo_dir,
            model=selected_model,
            session_id=session_id,
            user_input=user_input,
            approval_mode=approval_mode,
            timeout_s=timeout_s,
            project_brief=project_brief,
            track_description=track_description,
            done_check=lambda: False,
        )
        return {
            "ok": result.ok,
            "model": selected_model,
            "session_id": result.session_id,
            "created_paths": result.created_paths,
            "error": result.error,
            "paused_for_user": result.paused_for_user,
            "user_prompt": result.user_prompt,
            "user_choices": result.user_choices,
            "transcript_tail": result.transcript[-4000:],
        }

    def tool_generate_spec(
        self,
        task_description: str,
        context: str = "",
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
        artifacts_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        if self.state_manager.get_state().paused:
            return {"error": "Loop is paused. Call resume() first."}

        chosen_model = model or self.gemini_client.model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL")
        chosen_extensions = _parse_extensions_list(extensions)

        self.state_manager.set_state({"phase": "planning", "current_task": task_description, "error": None})
        self.state_manager.append_event("phase_start", {"phase": "spec"})

        if self.gemini_client.is_available:
            success, spec_content = self.gemini_client.generate_spec(
                task_description,
                context,
                model=chosen_model,
                extensions=chosen_extensions or None,
            )
            if not success:
                spec_content = f"# Spec (Gemini error)\n\n{spec_content}"
            else:
                spec_content = _strip_markdown_preface(spec_content)
        else:
            spec_content = f"""# Spec (Simulated)

## Summary
{task_description}
"""

        spec_with_meta = _artifact_header(chosen_model, chosen_extensions) + spec_content
        self._write_artifact("spec.md", spec_with_meta, artifacts_dir=artifacts_dir)
        self.state_manager.append_event("phase_complete", {"phase": "spec"})
        return {
            "ok": True,
            "artifact": "spec.md",
            "spec": spec_with_meta,
            "model": chosen_model,
            "extensions": chosen_extensions,
        }

    def tool_generate_plan(
        self,
        task_description: str,
        context: str = "",
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
        artifacts_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        if self.state_manager.get_state().paused:
            return {"error": "Loop is paused. Call resume() first."}

        self.state_manager.set_state({"phase": "planning", "current_task": task_description, "error": None})
        self.state_manager.append_event("phase_start", {"phase": "planning"})

        chosen_model = model or self.gemini_client.model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL")
        chosen_extensions = _parse_extensions_list(extensions)

        if self.gemini_client.is_available:
            success, plan_content = self.gemini_client.generate_plan(
                task_description,
                context,
                model=chosen_model,
                extensions=chosen_extensions or None,
            )
            if not success:
                plan_content = (
                    "# Plan (Gemini error)\n\n"
                    f"{plan_content}\n\n"
                    "## Fallback Plan\n"
                    "1. Re-run generate_plan\n"
                    "2. If it keeps failing, write plan.md manually\n"
                )
            else:
                plan_content = _strip_markdown_preface(plan_content)
        else:
            plan_content = """# Plan (Simulated)

## Goal
Generate a plan artifact and proceed to implementation.

## Steps
1. Write plan.md
2. Implement based on plan.md
3. Write handoff.md
4. Generate review.md
"""

        plan_with_meta = _artifact_header(chosen_model, chosen_extensions) + plan_content
        self._write_artifact("plan.md", plan_with_meta, artifacts_dir=artifacts_dir)
        self.state_manager.append_event("phase_complete", {"phase": "planning"})
        self.state_manager.set_state({"phase": "implementing"})
        return {
            "ok": True,
            "artifact": "plan.md",
            "plan": plan_with_meta,
            "model": chosen_model,
            "extensions": chosen_extensions,
        }

    def tool_submit_handoff(self, handoff_markdown: str, artifacts_dir: Optional[str] = None) -> dict[str, Any]:
        if self.state_manager.get_state().paused:
            return {"error": "Loop is paused. Call resume() first."}

        self.state_manager.set_state({"phase": "implementing", "error": None})
        self.state_manager.append_event("phase_start", {"phase": "implementing"})
        self._write_artifact("handoff.md", handoff_markdown, artifacts_dir=artifacts_dir)
        self.state_manager.append_event("phase_complete", {"phase": "implementing"})
        self.state_manager.set_state({"phase": "awaiting_review"})
        return {"ok": True, "artifact": "handoff.md"}

    def tool_generate_review(
        self,
        plan: Optional[str] = None,
        implementation: Optional[str] = None,
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
        artifacts_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        if self.state_manager.get_state().paused:
            return {"error": "Loop is paused. Call resume() first."}

        plan = plan if plan is not None else (self._read_artifact("plan.md", artifacts_dir=artifacts_dir) or "")
        implementation = (
            implementation
            if implementation is not None
            else (self._read_artifact("handoff.md", artifacts_dir=artifacts_dir) or "")
        )

        chosen_model = model or self.gemini_client.model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL")
        chosen_extensions = _parse_extensions_list(extensions)

        self.state_manager.set_state({"phase": "awaiting_review", "error": None})
        self.state_manager.append_event("phase_start", {"phase": "awaiting_review"})

        if self.gemini_client.is_available:
            success, review_content = self.gemini_client.generate_review(
                plan,
                implementation,
                model=chosen_model,
                extensions=chosen_extensions or None,
            )
            if not success:
                review_content = f"# Review (Gemini error)\n\n{review_content}"
            else:
                review_content = _strip_markdown_preface(review_content)
        else:
            review_content = """# Review (Simulated)

## Summary
Gemini CLI was not available, so this review is simulated.
"""

        review_with_meta = _artifact_header(chosen_model, chosen_extensions) + review_content
        self._write_artifact("review.md", review_with_meta, artifacts_dir=artifacts_dir)
        self.state_manager.append_event("phase_complete", {"phase": "awaiting_review"})

        current_state = self.state_manager.get_state()
        new_cycle_count = current_state.cycle_count + 1
        self.state_manager.set_state({"phase": "planning", "cycle_count": new_cycle_count, "current_task": None})
        self.state_manager.append_event("cycle_complete", {"cycle": new_cycle_count})
        return {
            "ok": True,
            "artifact": "review.md",
            "review": review_with_meta,
            "model": chosen_model,
            "extensions": chosen_extensions,
            "cycle_completed": new_cycle_count,
        }

    def tool_run_cycle(self, implementer: str = "simulate") -> dict[str, Any]:
        if self.state_manager.get_state().paused:
            return {"error": "Loop is paused. Call resume() first."}

        working_dir = Path(os.environ.get("CONDUCTOR_BRIDGE_WORKDIR", "."))
        results: dict[str, Any] = {"phases": []}

        plan_result = self.tool_generate_plan("Create a simple demonstration task", "Automated test cycle")
        plan_content = plan_result.get("plan") or ""
        results["phases"].append({"name": "planning", "success": True})

        self.state_manager.set_state({"phase": "implementing", "current_task": "Running implementation"})
        self.state_manager.append_event("phase_start", {"phase": "implementing"})
        impl = get_implementer(implementer)
        if not impl.is_available:
            impl = get_best_available_implementer()
        success, handoff_content = impl.implement(plan_content, working_dir)
        handoff_md = f"""# Implementation Handoff

## Implementer Used
{impl.name}

## Result
{"Success" if success else "Failed"}

## Details
{handoff_content}
"""
        self.state_manager.write_artifact("handoff.md", handoff_md)
        self.state_manager.append_event("phase_complete", {"phase": "implementing", "implementer": impl.name})
        results["phases"].append({"name": "implementing", "success": success, "implementer": impl.name})

        review_result = self.tool_generate_review(plan=plan_content, implementation=handoff_content)
        results["phases"].append({"name": "review", "success": True})
        results["cycle_completed"] = review_result.get("cycle_completed")
        return results

    def tool_pause(self) -> dict[str, Any]:
        state = self.state_manager.set_state({"paused": True})
        self.state_manager.append_event("loop_paused", {})
        return {"paused": True, "state": state.to_dict()}

    def tool_resume(self) -> dict[str, Any]:
        state = self.state_manager.set_state({"paused": False})
        self.state_manager.append_event("loop_resumed", {})
        return {"paused": False, "state": state.to_dict()}

    def tool_get_status(self) -> dict[str, Any]:
        from .implementer import ClaudeCliImplementer, CodexCliImplementer

        state = self.state_manager.get_state()
        return {
            "state": state.to_dict(),
            "gemini_available": self.gemini_client.is_available,
            "gemini_version": self.gemini_client.get_version(),
            "gemini_model_configured": os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL"),
            "gemini_extensions_configured": os.environ.get("CONDUCTOR_BRIDGE_GEMINI_EXTENSIONS"),
            "conductor_installed": self.gemini_client.check_conductor_extension(),
            "codex_available": CodexCliImplementer().is_available,
            "claude_available": ClaudeCliImplementer().is_available,
            "recent_events": [e.to_dict() for e in self.state_manager.get_events(10)],
        }


class MCPRouter:
    """MCP JSON-RPC router (tools/list, tools/call, initialize, etc.)."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    def handle(self, payload: Any, session_id: str) -> Any:
        if isinstance(payload, list):
            responses = []
            for item in payload:
                resp = self._handle_one(item, session_id=session_id)
                if resp is not None:
                    responses.append(resp)
            return responses or None
        return self._handle_one(payload, session_id=session_id)

    def _handle_one(self, req: dict[str, Any], session_id: str) -> Optional[dict[str, Any]]:
        if not isinstance(req, dict):
            return _jsonrpc_error(None, -32600, "Invalid Request")

        # Legacy (pre-JSON-RPC) compatibility: { "method": "...", "params": {...} }
        if req.get("jsonrpc") != "2.0":
            if "method" in req:
                method = req.get("method", "")
                params = req.get("params", {}) or {}
                try:
                    result = self.bridge.call_tool(method, params)
                    return {"result": result}
                except Exception as e:
                    return {"error": str(e)}
            return _jsonrpc_error(None, -32600, "Invalid Request")

        request_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if not method:
            return _jsonrpc_error(request_id, -32600, "Missing method")

        is_notification = "id" not in req

        try:
            if method == "initialize":
                protocol_version = (req.get("params") or {}).get("protocolVersion") or MCP_PROTOCOL_VERSION
                result = {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {"name": "conductor-bridge", "version": "0.1.0"},
                }
                return None if is_notification else _jsonrpc_result(request_id, result)

            if method == "notifications/initialized":
                return None

            if method == "ping":
                return None if is_notification else _jsonrpc_result(request_id, {})

            if method == "tools/list":
                result = {"tools": self.bridge.tools_list()}
                return None if is_notification else _jsonrpc_result(request_id, result)

            if method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments") or {}
                if not tool_name:
                    return _jsonrpc_error(request_id, -32602, "Missing params.name")
                tool_result = self.bridge.call_tool(tool_name, arguments)
                return None if is_notification else _jsonrpc_result(request_id, _as_text_result(tool_result))

            if method == "resources/list":
                return None if is_notification else _jsonrpc_result(request_id, {"resources": []})

            if method == "prompts/list":
                return None if is_notification else _jsonrpc_result(request_id, {"prompts": []})

            return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")

        except Exception as e:
            return _jsonrpc_error(request_id, -32603, str(e))


class BridgeHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, router: MCPRouter):
        super().__init__(server_address, handler_class)
        self.router = router
        self._sessions: dict[str, queue.Queue[str]] = {}

    def get_session_queue(self, session_id: str) -> queue.Queue[str]:
        q = self._sessions.get(session_id)
        if q is None:
            q = queue.Queue()
            self._sessions[session_id] = q
        return q


class MCPHTTPHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer

    def do_POST(self):
        if self.path != "/mcp":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        session_id = self._get_session_id() or uuid.uuid4().hex
        response = self.server.router.handle(payload, session_id=session_id)

        if response is None:
            # Streamable HTTP expects an HTTP 202 Accepted for notifications.
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", session_id)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def do_DELETE(self):
        if self.path != "/mcp":
            self.send_error(404, "Not Found")
            return

        session_id = self._get_session_id() or self._get_session_id_from_query()
        if session_id and session_id in self.server._sessions:
            del self.server._sessions[session_id]

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
            return

        if self.path.startswith("/mcp"):
            accept = self.headers.get("Accept", "")
            if "text/event-stream" not in accept:
                self.send_error(405, "Use POST /mcp for JSON-RPC")
                return

            session_id = self._get_session_id_from_query() or self._get_session_id() or uuid.uuid4().hex
            q = self.server.get_session_queue(session_id)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Mcp-Session-Id", session_id)
            self.end_headers()

            try:
                self.wfile.write(b": open\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(f"event: message\ndata: {msg}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError):
                return

        self.send_error(404, "Not Found")

    def _get_session_id(self) -> Optional[str]:
        return self.headers.get("Mcp-Session-Id") or self.headers.get("MCP-Session-Id")

    def _get_session_id_from_query(self) -> Optional[str]:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query or "")
        for key in ("sessionId", "session_id", "mcpSessionId"):
            values = qs.get(key)
            if values:
                return values[0]
        return None

    def log_message(self, fmt: str, *args: Any):
        return


def _read_lsp_message(stream) -> Optional[dict[str, Any]]:
    headers: dict[str, str] = {}
    header_bytes = b""
    while True:
        chunk = stream.readline()
        if not chunk:
            return None
        if chunk in (b"\n", b"\r\n"):
            break
        header_bytes += chunk

    for line in header_bytes.decode("utf-8", errors="replace").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    length_str = headers.get("content-length")
    if not length_str:
        return None
    length = int(length_str)
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _read_lsp_message_with_first_line(first_line: bytes, stream) -> Optional[dict[str, Any]]:
    headers: dict[str, str] = {}
    header_bytes = first_line
    while True:
        chunk = stream.readline()
        if not chunk:
            return None
        if chunk in (b"\n", b"\r\n"):
            break
        header_bytes += chunk

    for line in header_bytes.decode("utf-8", errors="replace").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    length_str = headers.get("content-length")
    if not length_str:
        return None
    length = int(length_str)
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_lsp_message(stream, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    stream.write(header)
    stream.write(body)
    stream.flush()


def _read_json_line(stream) -> Optional[dict[str, Any]]:
    line = stream.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    return json.loads(line.decode("utf-8"))


def _write_json_line(stream, payload: dict[str, Any]) -> None:
    stream.write((json.dumps(payload) + "\n").encode("utf-8"))
    stream.flush()


def run_stdio(router: MCPRouter) -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    session_id = "stdio"
    log_path = os.environ.get("CONDUCTOR_BRIDGE_STDIO_LOG")
    log_file = open(log_path, "a", encoding="utf-8") if log_path else None

    while True:
        first_line = stdin.readline()
        if not first_line:
            if log_file:
                log_file.write("EOF\n")
                log_file.flush()
            return

        try:
            if first_line.lstrip().startswith(b"Content-Length:"):
                framing = "lsp"
                if log_file:
                    log_file.write(f"LSP first_line={first_line!r}\n")
                    log_file.flush()
                req = _read_lsp_message_with_first_line(first_line, stdin)
            else:
                framing = "jsonl"
                line = first_line.strip()
                if not line:
                    continue
                if log_file:
                    log_file.write(f"JSONL line={line[:200]!r}\n")
                    log_file.flush()
                req = json.loads(line.decode("utf-8"))
        except Exception as e:
            # If we can't decode input, ignore it. (We can't safely reply without an id.)
            if log_file:
                log_file.write(f"DECODE_ERROR {type(e).__name__}: {e}\n")
                log_file.flush()
            continue

        if req is None:
            if log_file:
                log_file.write("REQ_NONE\n")
                log_file.flush()
            return

        resp = router.handle(req, session_id=session_id)
        if resp is None:
            if log_file:
                log_file.write("RESP_NONE\n")
                log_file.flush()
            continue
        if isinstance(resp, list):
            for item in resp:
                (_write_lsp_message if framing == "lsp" else _write_json_line)(stdout, item)
        else:
            (_write_lsp_message if framing == "lsp" else _write_json_line)(stdout, resp)
        if log_file:
            log_file.write(f"SENT framing={framing} id={req.get('id', 'no-id')}\n")
            log_file.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Conductor Bridge MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as Streamable HTTP server")
    parser.add_argument("--stdio", action="store_true", help="Run as stdio MCP server")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--state-dir", type=str, default=None, help="State directory")
    args = parser.parse_args()

    state_dir = args.state_dir or os.environ.get("CONDUCTOR_BRIDGE_STATE_DIR", "state")
    bridge = Bridge(state_dir)
    router = MCPRouter(bridge)

    if args.http == args.stdio:
        parser.error("Choose exactly one transport: --http or --stdio")

    if args.http:
        server_address = ("127.0.0.1", args.port)
        httpd = BridgeHTTPServer(server_address, MCPHTTPHandler, router)
        print(f"Conductor Bridge MCP server: http://127.0.0.1:{args.port}/mcp")
        print(f"State directory: {state_dir}")
        print("Press Ctrl+C to stop.")
        httpd.serve_forever()
        return

    run_stdio(router)


if __name__ == "__main__":
    main()
