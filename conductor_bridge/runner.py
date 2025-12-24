"""Loop runner that executes cycles repeatedly."""

import argparse
import json
import os
import time
from pathlib import Path

from .state import StateManager
from .gemini_client import GeminiClient
from .implementer import get_implementer, get_best_available_implementer


class CycleRunner:
    """Runs planning -> implementing -> review cycles."""

    def __init__(self, state_dir: str, implementer_name: str = "simulate"):
        self.state_dir = Path(state_dir)
        self.state_manager = StateManager(state_dir)
        self.gemini_client = GeminiClient()
        self.implementer_name = implementer_name
        self.working_dir = Path(os.environ.get("CONDUCTOR_BRIDGE_WORKDIR", "."))

    def run_cycle(self) -> dict:
        """Run one complete cycle."""
        state = self.state_manager.get_state()

        if state.paused:
            print("Loop is paused. Use resume() to continue.")
            return {"skipped": True, "reason": "paused"}

        results = {"phases": [], "cycle": state.cycle_count + 1}
        print(f"\n{'='*60}")
        print(f"Starting Cycle {state.cycle_count + 1}")
        print(f"{'='*60}")

        # Phase 1: Planning
        print("\n[PHASE 1] Planning...")
        self.state_manager.set_state({"phase": "planning", "current_task": "Generating plan"})
        self.state_manager.append_event("phase_start", {"phase": "planning"})

        plan_content = self._generate_plan()
        self.state_manager.write_artifact("plan.md", plan_content)
        print(f"  Plan written to artifacts/plan.md")
        results["phases"].append({"name": "planning", "success": True})
        self.state_manager.append_event("phase_complete", {"phase": "planning"})

        # Phase 2: Implementing
        print(f"\n[PHASE 2] Implementing (using {self.implementer_name})...")
        self.state_manager.set_state({"phase": "implementing", "current_task": "Running implementation"})
        self.state_manager.append_event("phase_start", {"phase": "implementing"})

        impl = get_implementer(self.implementer_name)
        if not impl.is_available:
            print(f"  {self.implementer_name} not available, falling back...")
            impl = get_best_available_implementer()
            print(f"  Using: {impl.name}")

        success, handoff_content = impl.implement(plan_content, self.working_dir)

        handoff_md = f"""# Implementation Handoff

## Implementer Used
{impl.name}

## Result
{"Success" if success else "Failed"}

## Details
{handoff_content}
"""
        self.state_manager.write_artifact("handoff.md", handoff_md)
        print(f"  Handoff written to artifacts/handoff.md")
        results["phases"].append({"name": "implementing", "success": success, "implementer": impl.name})
        self.state_manager.append_event("phase_complete", {"phase": "implementing", "implementer": impl.name})

        # Phase 3: Review
        print("\n[PHASE 3] Reviewing...")
        self.state_manager.set_state({"phase": "awaiting_review", "current_task": "Generating review"})
        self.state_manager.append_event("phase_start", {"phase": "awaiting_review"})

        review_content = self._generate_review(plan_content, handoff_content)
        self.state_manager.write_artifact("review.md", review_content)
        print(f"  Review written to artifacts/review.md")
        results["phases"].append({"name": "review", "success": True})
        self.state_manager.append_event("phase_complete", {"phase": "awaiting_review"})

        # Complete cycle
        new_cycle_count = state.cycle_count + 1
        self.state_manager.set_state({
            "phase": "planning",
            "cycle_count": new_cycle_count,
            "current_task": None
        })
        self.state_manager.append_event("cycle_complete", {"cycle": new_cycle_count})

        print(f"\n[COMPLETE] Cycle {new_cycle_count} finished!")
        return results

    def _generate_plan(self) -> str:
        """Generate a plan using Gemini or stub."""
        if self.gemini_client.is_available:
            print("  Using Gemini CLI for planning...")
            success, content = self.gemini_client.generate_plan(
                "Create a demonstration task for the conductor-bridge loop",
                "This is cycle testing for the MCP hub"
            )
            if success:
                return content
            else:
                print(f"  Gemini failed: {content[:100]}...")

        print("  Using simulated plan...")
        return """# Plan (Simulated)

## Goal
Demonstrate the conductor-bridge loop is working correctly.

## Steps
1. Verify state management is functional
2. Confirm artifact writing works
3. Ensure event logging is operational
4. Complete the cycle successfully

## Expected Output
- All phases complete without errors
- Artifacts are written to disk
- Events are logged
- State is updated correctly

## Success Criteria
The cycle completes and returns to planning phase.
"""

    def _generate_review(self, plan: str, implementation: str) -> str:
        """Generate a review using Gemini or stub."""
        if self.gemini_client.is_available:
            print("  Using Gemini CLI for review...")
            success, content = self.gemini_client.generate_review(plan, implementation)
            if success:
                return content
            else:
                print(f"  Gemini failed: {content[:100]}...")

        print("  Using simulated review...")
        return f"""# Review (Simulated)

## Plan Adherence
The implementation followed the plan structure correctly.

## Quality Assessment
- All required steps were executed
- Artifacts were generated successfully
- State transitions worked as expected
- Event logging captured all phases

## Observations
1. Loop mechanics are functioning
2. State persistence is working
3. File I/O is reliable

## Recommendations
- Consider adding retry logic for network calls
- Add more detailed error messages
- Implement progress callbacks for long operations

## Conclusion
Cycle completed successfully. The conductor-bridge loop is operational.
Ready for next iteration.
"""

    def run_cycles(self, count: int, delay: float = 1.0) -> list:
        """Run multiple cycles with optional delay between them."""
        results = []

        for i in range(count):
            result = self.run_cycle()
            results.append(result)

            if result.get("skipped"):
                print("Loop paused, stopping...")
                break

            if i < count - 1:
                print(f"\nWaiting {delay}s before next cycle...")
                time.sleep(delay)

        return results


def main():
    parser = argparse.ArgumentParser(description="Conductor Bridge Cycle Runner")
    parser.add_argument("--implementer", type=str, default="simulate",
                        choices=["simulate", "codex_cli", "claude_cli"],
                        help="Implementer to use (default: simulate)")
    parser.add_argument("--cycles", type=int, default=1,
                        help="Number of cycles to run (default: 1)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay between cycles in seconds (default: 1.0)")
    parser.add_argument("--state-dir", type=str, default=None,
                        help="State directory")
    args = parser.parse_args()

    state_dir = args.state_dir or os.environ.get("CONDUCTOR_BRIDGE_STATE_DIR", "state")

    print("="*60)
    print("Conductor Bridge Cycle Runner")
    print("="*60)
    print(f"State directory: {state_dir}")
    print(f"Implementer: {args.implementer}")
    print(f"Cycles to run: {args.cycles}")

    runner = CycleRunner(state_dir, args.implementer)

    # Print status
    from .implementer import CodexCliImplementer, ClaudeCliImplementer
    print(f"\nTool availability:")
    print(f"  Gemini CLI: {'Yes' if runner.gemini_client.is_available else 'No'}")
    print(f"  Codex CLI: {'Yes' if CodexCliImplementer().is_available else 'No'}")
    print(f"  Claude CLI: {'Yes' if ClaudeCliImplementer().is_available else 'No'}")

    results = runner.run_cycles(args.cycles, args.delay)

    # Print summary
    print("\n" + "="*60)
    print("Run Complete")
    print("="*60)
    print(f"Cycles completed: {len(results)}")

    # Print final state
    state = runner.state_manager.get_state()
    print(f"\nFinal State:")
    print(json.dumps(state.to_dict(), indent=2))

    # Print recent events
    events = runner.state_manager.get_events(30)
    print(f"\nRecent Events ({len(events)}):")
    for event in events[-10:]:
        print(f"  [{event.timestamp}] {event.type}: {event.payload}")

    # Print artifacts
    print("\nArtifacts:")
    for artifact in ["plan.md", "handoff.md", "review.md"]:
        content = runner.state_manager.read_artifact(artifact)
        if content:
            lines = content.split('\n')
            print(f"\n--- {artifact} (first 10 lines) ---")
            for line in lines[:10]:
                print(f"  {line}")
            if len(lines) > 10:
                print(f"  ... ({len(lines) - 10} more lines)")


if __name__ == "__main__":
    main()
