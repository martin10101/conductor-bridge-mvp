"""MCP server exposing conductor-bridge tools over HTTP."""

import argparse
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable
from functools import wraps

from .state import StateManager, BridgeState
from .gemini_client import GeminiClient
from .implementer import get_implementer, get_best_available_implementer


class MCPServer:
    """MCP-compatible HTTP server for conductor-bridge."""

    def __init__(self, state_dir: str, port: int = 8765):
        self.state_manager = StateManager(state_dir)
        self.gemini_client = GeminiClient()
        self.port = port
        self.tools = self._register_tools()

    def _register_tools(self) -> dict[str, Callable]:
        """Register available MCP tools."""
        return {
            "ping": self.tool_ping,
            "get_state": self.tool_get_state,
            "set_state": self.tool_set_state,
            "append_event": self.tool_append_event,
            "run_cycle": self.tool_run_cycle,
            "pause": self.tool_pause,
            "resume": self.tool_resume,
            "get_artifacts": self.tool_get_artifacts,
            "get_status": self.tool_get_status,
        }

    def tool_ping(self, **kwargs) -> dict:
        """Ping the server."""
        return {"status": "ok", "message": "conductor-bridge is running"}

    def tool_get_state(self, **kwargs) -> dict:
        """Get current state."""
        state = self.state_manager.get_state()
        return state.model_dump()

    def tool_set_state(self, partial_update: dict = None, **kwargs) -> dict:
        """Update state with partial data."""
        if partial_update is None:
            partial_update = {}
        state = self.state_manager.set_state(partial_update)
        return state.model_dump()

    def tool_append_event(self, type: str = "unknown", payload: dict = None, **kwargs) -> dict:
        """Append an event to the event log."""
        event = self.state_manager.append_event(type, payload or {})
        return event.model_dump()

    def tool_run_cycle(self, implementer: str = "simulate", **kwargs) -> dict:
        """Run one full cycle: plan -> implement -> review."""
        state = self.state_manager.get_state()

        if state.paused:
            return {"error": "Loop is paused. Call resume() first."}

        results = {"phases": []}
        working_dir = Path(os.environ.get("CONDUCTOR_BRIDGE_WORKDIR", "."))

        # Phase 1: Planning
        self.state_manager.set_state({"phase": "planning", "current_task": "Generating plan"})
        self.state_manager.append_event("phase_start", {"phase": "planning"})

        if self.gemini_client.is_available:
            success, plan_content = self.gemini_client.generate_plan(
                "Create a simple demonstration task",
                "This is an automated test cycle"
            )
            if not success:
                plan_content = f"# Plan (Gemini unavailable)\n\n{plan_content}\n\n## Fallback Plan\n1. Create a simple test file\n2. Verify it works\n3. Document results"
        else:
            plan_content = """# Plan (Simulated)

## Goal
Demonstrate the conductor-bridge loop is working.

## Steps
1. Create a sample implementation
2. Verify the cycle completes
3. Document the results

## Expected Output
A completed cycle with artifacts in place.
"""

        self.state_manager.write_artifact("plan.md", plan_content)
        results["phases"].append({"name": "planning", "success": True})
        self.state_manager.append_event("phase_complete", {"phase": "planning"})

        # Phase 2: Implementing
        self.state_manager.set_state({"phase": "implementing", "current_task": "Running implementation"})
        self.state_manager.append_event("phase_start", {"phase": "implementing"})

        impl = get_implementer(implementer)
        if not impl.is_available:
            impl = get_best_available_implementer()

        success, handoff_content = impl.implement(plan_content, working_dir)

        self.state_manager.write_artifact("handoff.md", f"""# Implementation Handoff

## Implementer Used
{impl.name}

## Result
{"Success" if success else "Failed"}

## Details
{handoff_content}
""")
        results["phases"].append({"name": "implementing", "success": success, "implementer": impl.name})
        self.state_manager.append_event("phase_complete", {"phase": "implementing", "implementer": impl.name})

        # Phase 3: Review
        self.state_manager.set_state({"phase": "awaiting_review", "current_task": "Generating review"})
        self.state_manager.append_event("phase_start", {"phase": "awaiting_review"})

        if self.gemini_client.is_available:
            success, review_content = self.gemini_client.generate_review(plan_content, handoff_content)
            if not success:
                review_content = f"# Review (Gemini error)\n\n{review_content}"
        else:
            review_content = f"""# Review (Simulated)

## Plan Adherence
The implementation followed the plan structure.

## Quality Assessment
- Code appears functional
- Basic requirements met
- Loop completed successfully

## Recommendations
1. Consider adding more detailed logging
2. Add error handling for edge cases
3. Document the API endpoints

## Conclusion
Cycle completed. Ready for next iteration.
"""

        self.state_manager.write_artifact("review.md", review_content)
        results["phases"].append({"name": "review", "success": True})
        self.state_manager.append_event("phase_complete", {"phase": "awaiting_review"})

        # Complete cycle
        current_state = self.state_manager.get_state()
        self.state_manager.set_state({
            "phase": "planning",
            "cycle_count": current_state.cycle_count + 1,
            "current_task": None
        })
        self.state_manager.append_event("cycle_complete", {"cycle": current_state.cycle_count + 1})

        results["cycle_completed"] = current_state.cycle_count + 1
        return results

    def tool_pause(self, **kwargs) -> dict:
        """Pause the loop."""
        state = self.state_manager.set_state({"paused": True})
        self.state_manager.append_event("loop_paused", {})
        return {"paused": True, "state": state.model_dump()}

    def tool_resume(self, **kwargs) -> dict:
        """Resume the loop."""
        state = self.state_manager.set_state({"paused": False})
        self.state_manager.append_event("loop_resumed", {})
        return {"paused": False, "state": state.model_dump()}

    def tool_get_artifacts(self, **kwargs) -> dict:
        """Get all current artifacts."""
        return {
            "plan": self.state_manager.read_artifact("plan.md"),
            "handoff": self.state_manager.read_artifact("handoff.md"),
            "review": self.state_manager.read_artifact("review.md"),
        }

    def tool_get_status(self, **kwargs) -> dict:
        """Get comprehensive status including tool availability."""
        state = self.state_manager.get_state()

        from .implementer import CodexCliImplementer, ClaudeCliImplementer

        return {
            "state": state.model_dump(),
            "gemini_available": self.gemini_client.is_available,
            "gemini_version": self.gemini_client.get_version(),
            "conductor_installed": self.gemini_client.check_conductor_extension(),
            "codex_available": CodexCliImplementer().is_available,
            "claude_available": ClaudeCliImplementer().is_available,
            "recent_events": [e.model_dump() for e in self.state_manager.get_events(10)],
        }

    def handle_request(self, method: str, params: dict = None) -> dict:
        """Handle an MCP-style request."""
        params = params or {}

        if method not in self.tools:
            return {"error": f"Unknown method: {method}", "available": list(self.tools.keys())}

        try:
            result = self.tools[method](**params)
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}


class MCPHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for MCP requests."""

    server: 'MCPHTTPServer'

    def do_POST(self):
        """Handle POST requests to /mcp endpoint."""
        if self.path != "/mcp":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            request = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        method = request.get("method", "")
        params = request.get("params", {})

        response = self.server.mcp_server.handle_request(method, params)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def do_GET(self):
        """Handle GET requests for health check."""
        if self.path == "/health":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        """Custom log formatting."""
        print(f"[MCP Server] {args[0]}")


class MCPHTTPServer(HTTPServer):
    """HTTP server with MCP server instance."""

    def __init__(self, address, handler, mcp_server: MCPServer):
        super().__init__(address, handler)
        self.mcp_server = mcp_server


def main():
    parser = argparse.ArgumentParser(description="Conductor Bridge MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as HTTP server")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--state-dir", type=str, default=None, help="State directory")
    args = parser.parse_args()

    state_dir = args.state_dir or os.environ.get("CONDUCTOR_BRIDGE_STATE_DIR", "state")

    mcp_server = MCPServer(state_dir, args.port)

    if args.http:
        server_address = ('127.0.0.1', args.port)
        httpd = MCPHTTPServer(server_address, MCPHTTPHandler, mcp_server)
        print(f"Conductor Bridge MCP Server running at http://127.0.0.1:{args.port}/mcp")
        print(f"State directory: {state_dir}")
        print("Press Ctrl+C to stop.")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            httpd.shutdown()
    else:
        print("Use --http to start the HTTP server")
        print("Available tools:", list(mcp_server.tools.keys()))


if __name__ == "__main__":
    main()
