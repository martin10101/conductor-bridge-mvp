"""Helpers for creating and pushing new projects without manual terminal work."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


def _utc_slug_timestamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def slugify(text: str, *, max_len: int = 40) -> str:
    s = (text or "").strip().lower()
    if not s:
        return "project"
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        return "project"
    return s[:max_len].strip("-") or "project"


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def _require(cmd_name: str) -> None:
    from shutil import which

    if which(cmd_name) is None:
        raise RuntimeError(f"Required command not found in PATH: {cmd_name}")


@dataclass(frozen=True)
class ProjectInfo:
    project_dir: str
    repo_name: str
    remote_url: str
    artifacts_dir: str


def create_project(
    *,
    idea: str,
    name: Optional[str] = None,
    root_dir: str,
    visibility: str = "public",
    artifacts_subdir: str = ".conductor-bridge/artifacts",
) -> ProjectInfo:
    _require("git")
    _require("gh")

    if visibility not in {"public", "private", "internal"}:
        raise ValueError("visibility must be one of: public, private, internal")

    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)

    title_line = (idea or "").splitlines()[0] if idea else ""
    base_slug = slugify(name or title_line)
    timestamp = _utc_slug_timestamp()

    project_dir = root / f"{timestamp}-{base_slug}"
    project_dir.mkdir(parents=True, exist_ok=False)

    readme = f"""# {base_slug}

## Idea

{idea.strip() if idea else ""}
"""
    (project_dir / "README.md").write_text(readme, encoding="utf-8")

    gitignore = """# Secrets
.env
.env.*
!.env.example

# OS junk
.DS_Store
Thumbs.db

# Dependencies / caches
node_modules/
.venv/
__pycache__/
.pytest_cache/
"""
    (project_dir / ".gitignore").write_text(gitignore, encoding="utf-8")

    artifacts_dir = project_dir / artifacts_subdir
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / ".keep").write_text("", encoding="utf-8")

    # git init + initial commit
    if _run(["git", "init"], cwd=project_dir).returncode != 0:
        raise RuntimeError("git init failed")
    _run(["git", "checkout", "-b", "main"], cwd=project_dir)
    _run(["git", "add", "."], cwd=project_dir)
    if _run(["git", "commit", "-m", "chore: initial project scaffold"], cwd=project_dir).returncode != 0:
        raise RuntimeError("git commit failed (is your git user.name/user.email set?)")

    # Create GitHub repo; if name exists, suffix with timestamp.
    repo_name = base_slug
    exists = _run(["gh", "repo", "view", repo_name, "--json", "name,url"], cwd=project_dir).returncode == 0
    if exists:
        repo_name = f"{base_slug}-{timestamp}"

    create = _run(
        ["gh", "repo", "create", repo_name, f"--{visibility}", "--source=.", "--remote=origin", "--push"],
        cwd=project_dir,
    )
    if create.returncode != 0:
        raise RuntimeError(f"gh repo create failed: {create.stderr.strip() or create.stdout.strip()}")

    remote = _run(["git", "remote", "get-url", "origin"], cwd=project_dir)
    if remote.returncode != 0:
        raise RuntimeError("Failed to read git remote URL after repo creation")

    return ProjectInfo(
        project_dir=str(project_dir),
        repo_name=repo_name,
        remote_url=remote.stdout.strip(),
        artifacts_dir=str(artifacts_dir),
    )


def push_branch(
    *,
    repo_dir: str,
    message: str = "Auto update",
    branch_prefix: str = "auto",
) -> dict[str, str]:
    _require("git")

    root = Path(repo_dir)
    if not root.exists():
        raise RuntimeError(f"Repo dir not found: {repo_dir}")

    status = _run(["git", "status", "--porcelain"], cwd=root)
    if status.returncode != 0:
        raise RuntimeError("git status failed")
    if not status.stdout.strip():
        return {"ok": "true", "pushed": "false", "reason": "no changes"}

    timestamp = _utc_slug_timestamp()
    branch_name = f"{branch_prefix}/{timestamp}"

    if _run(["git", "checkout", "-b", branch_name], cwd=root).returncode != 0:
        raise RuntimeError("git checkout -b failed")
    _run(["git", "add", "-A"], cwd=root)
    if _run(["git", "commit", "-m", message], cwd=root).returncode != 0:
        raise RuntimeError("git commit failed")
    if _run(["git", "push", "-u", "origin", branch_name], cwd=root).returncode != 0:
        raise RuntimeError("git push failed")

    return {"ok": "true", "pushed": "true", "branch": branch_name}

