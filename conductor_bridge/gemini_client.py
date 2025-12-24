"""Wrapper for Gemini CLI commands with timeout handling."""

import subprocess
import shutil
import os
from typing import Optional, Tuple


class GeminiClient:
    """Client for interacting with Gemini CLI."""

    def __init__(self, timeout: int = 120, model: Optional[str] = None):
        self.timeout = timeout
        self.model = model or os.environ.get("CONDUCTOR_BRIDGE_GEMINI_MODEL")
        self._gemini_path: Optional[str] = None

    @property
    def is_available(self) -> bool:
        """Check if Gemini CLI is available."""
        return self.gemini_path is not None

    @property
    def gemini_path(self) -> Optional[str]:
        """Get path to gemini executable."""
        if self._gemini_path is None:
            self._gemini_path = shutil.which("gemini")
        return self._gemini_path

    def run_prompt(
        self,
        prompt: str,
        timeout: Optional[int] = None,
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
    ) -> Tuple[bool, str]:
        """
        Run a prompt through Gemini CLI.

        Returns:
            Tuple of (success: bool, output: str)
        """
        if not self.is_available:
            return False, "Gemini CLI is not available"

        timeout = timeout or self.timeout

        args: list[str] = [self.gemini_path]

        selected_model = model or self.model
        if selected_model:
            args += ["--model", selected_model]

        selected_extensions = extensions
        if selected_extensions is None:
            env_extensions = os.environ.get("CONDUCTOR_BRIDGE_GEMINI_EXTENSIONS")
            if env_extensions:
                selected_extensions = [e.strip() for e in env_extensions.split(",") if e.strip()]
        if selected_extensions:
            args += ["--extensions", *selected_extensions]

        try:
            result = subprocess.run(
                [*args, prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False
            )

            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, f"Error (exit {result.returncode}): {result.stderr}"

        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {timeout} seconds"
        except Exception as e:
            return False, f"Exception: {str(e)}"

    def get_version(self) -> Optional[str]:
        """Get Gemini CLI version."""
        if not self.is_available:
            return None

        try:
            result = subprocess.run(
                [self.gemini_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def check_conductor_extension(self) -> bool:
        """Check if Conductor extension is installed."""
        if not self.is_available:
            return False

        try:
            result = subprocess.run(
                [self.gemini_path, "extensions", "list"],
                capture_output=True,
                text=True,
                timeout=30
            )
            return "conductor" in result.stdout.lower()
        except Exception:
            return False

    def generate_plan(
        self,
        task_description: str,
        context: str = "",
        *,
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
    ) -> Tuple[bool, str]:
        """Generate a plan using Gemini."""
        prompt = f"""You are a planning agent. Create a detailed implementation plan.

Task: {task_description}

{f"Context: {context}" if context else ""}

Do not ask clarifying questions. If information is missing, make reasonable assumptions and list them.
Do not mention tools, tool availability, or tool usage. Output only the markdown plan.
Do not include any preface, meta-commentary, or "I will..." statements.
Start the response with exactly: `# Plan`

Write a structured plan with:
1. Goal summary
2. Step-by-step implementation steps
3. Expected deliverables
4. Potential issues to watch for

Output the plan in markdown format."""

        return self.run_prompt(prompt, model=model, extensions=extensions)

    def generate_spec(
        self,
        task_description: str,
        context: str = "",
        *,
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
    ) -> Tuple[bool, str]:
        """Generate a spec (requirements) using Gemini."""
        prompt = f"""You are a product and engineering spec writer. Create a clear, testable spec.

Task: {task_description}

{f"Context: {context}" if context else ""}

Do not ask clarifying questions. If information is missing, make reasonable assumptions and list them.
Do not mention tools, tool availability, or tool usage. Output only the markdown spec.
Do not include any preface, meta-commentary, or "I will..." statements.
Start the response with exactly: `# Spec`

Write a structured spec with:
1. Summary
2. Users / audience
3. Requirements (must-have)
4. Non-goals (out of scope)
5. Acceptance criteria (bullet list, testable)
6. Risks / edge cases

Output in markdown format."""

        return self.run_prompt(prompt, model=model, extensions=extensions)

    def generate_review(
        self,
        plan: str,
        implementation: str,
        *,
        model: Optional[str] = None,
        extensions: Optional[list[str]] = None,
    ) -> Tuple[bool, str]:
        """Generate a review of the implementation against the plan."""
        prompt = f"""You are a code review agent. Review this implementation against the plan.

## Original Plan
{plan}

## Implementation Summary
{implementation}

Provide:
1. Completion assessment (what was done vs planned)
2. Quality observations
3. Suggested improvements
4. Next steps

Do not ask clarifying questions. If information is missing, note it briefly and proceed.
Do not mention tools, tool availability, or tool usage. Output only the markdown review.
Do not include any preface, meta-commentary, or "I will..." statements.
Start the response with exactly: `# Review`

Output in markdown format."""

        return self.run_prompt(prompt, model=model, extensions=extensions)
