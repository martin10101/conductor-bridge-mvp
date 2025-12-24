# Conductor Bridge MVP

A minimal MCP hub + loop runner that orchestrates **Gemini Conductor** (for planning/review) with **Codex/Claude** (for implementation).

## Quick Start

### 1. Bootstrap Prerequisites

```powershell
.\scripts\bootstrap.ps1
```

This installs:
- Git
- Node.js LTS
- Python 3.12+
- Gemini CLI (`npm install -g @google/gemini-cli`)
- Conductor extension (`gemini extensions install https://github.com/gemini-cli-extensions/conductor`)

### 2. Setup Python Environment

```powershell
.\scripts\venv.ps1
```

### 3. Start the MCP Server

```powershell
.\.venv\Scripts\Activate.ps1
python -m conductor_bridge.server --http --port 8765
```

To run the server as a **stdio MCP server** (for Cursor-style `command` MCP configs), use:

```powershell
python -m conductor_bridge.server --stdio
```

The server exposes these MCP tools at `http://127.0.0.1:8765/mcp`:

| Tool | Description |
|------|-------------|
| `ping()` | Health check |
| `get_state()` | Get current state |
| `set_state(partial_update)` | Update state |
| `append_event(type, payload)` | Log an event |
| `generate_spec(task_description, ...)` | Generate `spec.md` (Gemini) |
| `generate_plan(task_description, ...)` | Generate `plan.md` (Gemini) |
| `submit_handoff(handoff_markdown)` | Write `handoff.md` |
| `generate_review(...)` | Generate `review.md` (Gemini) |
| `run_cycle(implementer)` | Run one planning->implementing->review cycle |
| `pause()` / `resume()` | Control the loop |
| `get_artifacts()` | Get plan/handoff/review artifacts |
| `write_artifact(name, content)` | Write an artifact file |
| `get_status()` | Full status including tool availability |

### 4. Run the Loop

```powershell
python -m conductor_bridge.runner --implementer simulate --cycles 3
```

Options:
- `--implementer`: `simulate` (default), `codex_cli`, or `claude_cli`
- `--cycles`: Number of cycles to run
- `--delay`: Seconds between cycles
- `--state-dir`: State directory (default: `state/`)

## Connecting Codex to the MCP Hub

## Using It From Chat (No Terminal)

Once configured, you can run the full loop just by chatting in the Codex panel (inside Cursor):

1. Tell Codex your idea (what to build/change) and which repo folder to work in.
2. Codex asks Gemini for a plan (`plan.md`), implements it, then asks Gemini to review (`review.md`).
3. Codex commits and pushes to a new GitHub branch (if the repo has an `origin` remote and you’re logged in).

Artifacts are written to `C:\conductor-bridge-mvp\state\artifacts\` by default.

### Option 1: Edit config.toml

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.conductor-bridge]
url = "http://127.0.0.1:8765/mcp"
```

### Option 2: Use CLI

```bash
codex mcp add --url http://127.0.0.1:8765/mcp conductor-bridge
```

**Note:** Codex MCP config is shared between CLI and IDE extension, so the Codex panel in Cursor will see the same tools.

## Running Codex as MCP Server

Codex can also run as an MCP server:

```bash
codex mcp-server
```

This exposes `codex` and `codex-reply` tools that the hub could call for implementation.

## State Machine

```
planning -> implementing -> awaiting_review -> planning (repeat)
```

Each phase:
1. **Planning**: Gemini generates `artifacts/plan.md`
2. **Implementing**: Codex/Claude/simulate creates `artifacts/handoff.md`
3. **Review**: Gemini generates `artifacts/review.md`

## Project Structure

```
conductor-bridge-mvp/
├── conductor_bridge/
│   ├── __init__.py
│   ├── server.py         # MCP HTTP server
│   ├── state.py          # Atomic state management
│   ├── gemini_client.py  # Gemini CLI wrapper
│   ├── implementer.py    # Implementation adapters
│   └── runner.py         # Cycle runner
├── scripts/
│   ├── bootstrap.ps1     # Prerequisites installer
│   └── venv.ps1          # Python venv setup
├── state/
│   ├── state.json        # Canonical state
│   ├── events.jsonl      # Event log
│   └── artifacts/
│       ├── plan.md
│       ├── handoff.md
│       └── review.md
├── logs/
│   └── install.log
├── .cursor/
│   └── mcp.json          # Cursor MCP config
├── pyproject.toml
└── README.md
```

## Authentication Notes

- **Gemini CLI**: Run `gemini` once to trigger browser authentication
- **Codex CLI**: Requires OpenAI API key or login
- **Claude CLI**: Requires Anthropic API key or login

## Gemini Model Selection

Set a default model for planning/review:

```powershell
$env:CONDUCTOR_BRIDGE_GEMINI_MODEL = "gemini-3-pro-preview"
```

Optionally force extensions (comma-separated):

```powershell
$env:CONDUCTOR_BRIDGE_GEMINI_EXTENSIONS = "conductor"
```

## MCP API Examples

```bash
# Initialize (JSON-RPC 2.0)
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# List tools
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# Call a tool (example: get_status)
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_status","arguments":{}}}'
```
