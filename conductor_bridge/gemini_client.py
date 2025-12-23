"""Wrapper for Gemini CLI commands with timeout handling."""

import subprocess
import shutil
from typing import Optional, Tuple


class GeminiClient:
    """Client for interacting with Gemini CLI."""

    def __init__(self, timeout: int = 120):
        self.timeout = timeout
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

    def run_prompt(self, prompt: str, timeout: Optional[int] = None) -> Tuple[bool, str]:
        """
        Run a prompt through Gemini CLI.

        Returns:
            Tuple of (success: bool, output: str)
        """
        if not self.is_available:
            return False, "Gemini CLI is not available"

        timeout = timeout or self.timeout

        try:
            result = subprocess.run(
                [self.gemini_path, "-p", prompt],
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

    def generate_plan(self, task_description: str, context: str = "") -> Tuple[bool, str]:
        """Generate a plan using Gemini."""
        prompt = f"""You are a planning agent. Create a detailed implementation plan.

Task: {task_description}

{f"Context: {context}" if context else ""}

Write a structured plan with:
1. Goal summary
2. Step-by-step implementation steps
3. Expected deliverables
4. Potential issues to watch for

Output the plan in markdown format."""

        return self.run_prompt(prompt)

    def generate_review(self, plan: str, implementation: str) -> Tuple[bool, str]:
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

Output in markdown format."""

        return self.run_prompt(prompt)
