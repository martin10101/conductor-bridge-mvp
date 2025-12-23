"""Implementer adapters for different execution backends."""

import subprocess
import shutil
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from pathlib import Path


class Implementer(ABC):
    """Base class for implementer adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return implementer name."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if this implementer is available."""
        pass

    @abstractmethod
    def implement(self, plan: str, working_dir: Path) -> Tuple[bool, str]:
        """
        Implement the given plan.

        Args:
            plan: The plan to implement
            working_dir: Directory to work in

        Returns:
            Tuple of (success: bool, result_summary: str)
        """
        pass


class SimulateImplementer(Implementer):
    """Simulated implementer for testing the loop."""

    @property
    def name(self) -> str:
        return "simulate"

    @property
    def is_available(self) -> bool:
        return True  # Always available

    def implement(self, plan: str, working_dir: Path) -> Tuple[bool, str]:
        """Simulate implementation by creating a stub response."""
        summary = f"""# Implementation Summary (Simulated)

## Plan Received
{plan[:500]}{'...' if len(plan) > 500 else ''}

## Actions Taken (Simulated)
- Parsed the plan successfully
- Identified key implementation steps
- Created placeholder implementations
- Ran simulated tests (all passed)

## Files Modified (Simulated)
- src/feature.py - Added new feature code
- tests/test_feature.py - Added unit tests
- README.md - Updated documentation

## Status
Implementation simulated successfully. This is a test run.
"""
        return True, summary


class CodexCliImplementer(Implementer):
    """Implementer using Codex CLI."""

    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        self._codex_path: Optional[str] = None

    @property
    def name(self) -> str:
        return "codex_cli"

    @property
    def codex_path(self) -> Optional[str]:
        if self._codex_path is None:
            self._codex_path = shutil.which("codex")
        return self._codex_path

    @property
    def is_available(self) -> bool:
        return self.codex_path is not None

    def implement(self, plan: str, working_dir: Path) -> Tuple[bool, str]:
        """Run Codex CLI to implement the plan."""
        if not self.is_available:
            return False, "Codex CLI is not available"

        prompt = f"""Implement the following plan. Work in the current directory.

## Plan
{plan}

## Instructions
1. Read and understand the plan
2. Implement each step
3. Create/modify necessary files
4. Provide a summary of what was done

Be concise and focus on the implementation."""

        try:
            result = subprocess.run(
                [self.codex_path, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(working_dir),
                shell=False
            )

            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, f"Codex error (exit {result.returncode}): {result.stderr}"

        except subprocess.TimeoutExpired:
            return False, f"Codex timed out after {self.timeout}s"
        except Exception as e:
            return False, f"Exception: {str(e)}"


class ClaudeCliImplementer(Implementer):
    """Implementer using Claude Code CLI."""

    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        self._claude_path: Optional[str] = None

    @property
    def name(self) -> str:
        return "claude_cli"

    @property
    def claude_path(self) -> Optional[str]:
        if self._claude_path is None:
            self._claude_path = shutil.which("claude")
        return self._claude_path

    @property
    def is_available(self) -> bool:
        return self.claude_path is not None

    def implement(self, plan: str, working_dir: Path) -> Tuple[bool, str]:
        """Run Claude CLI to implement the plan."""
        if not self.is_available:
            return False, "Claude CLI is not available"

        prompt = f"""Implement the following plan. Work in the current directory.

## Plan
{plan}

## Instructions
1. Read and understand the plan
2. Implement each step
3. Create/modify necessary files
4. Provide a summary of what was done"""

        try:
            result = subprocess.run(
                [self.claude_path, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(working_dir),
                shell=False
            )

            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, f"Claude error (exit {result.returncode}): {result.stderr}"

        except subprocess.TimeoutExpired:
            return False, f"Claude timed out after {self.timeout}s"
        except Exception as e:
            return False, f"Exception: {str(e)}"


def get_implementer(name: str) -> Implementer:
    """Get an implementer by name."""
    implementers = {
        "simulate": SimulateImplementer,
        "codex_cli": CodexCliImplementer,
        "claude_cli": ClaudeCliImplementer,
    }

    if name not in implementers:
        available = ", ".join(implementers.keys())
        raise ValueError(f"Unknown implementer '{name}'. Available: {available}")

    return implementers[name]()


def get_best_available_implementer() -> Implementer:
    """Get the best available implementer, preferring real over simulated."""
    for impl_class in [CodexCliImplementer, ClaudeCliImplementer, SimulateImplementer]:
        impl = impl_class()
        if impl.is_available:
            return impl
    return SimulateImplementer()
