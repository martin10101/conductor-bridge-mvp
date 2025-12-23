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

The server exposes these MCP tools at `http://127.0.0.1:8765/mcp`:

| Tool | Description |
|------|-------------|
| `ping()` | Health check |
| `get_state()` | Get current state |
| `set_state(partial_update)` | Update state |
| `append_event(type, payload)` | Log an event |
| `run_cycle(implementer)` | Run one planning->implementing->review cycle |
| `pause()` / `resume()` | Control the loop |
| `get_artifacts()` | Get plan/handoff/review artifacts |
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

### Option 1: Edit config.toml

Add to `~/.codex/config.toml`:

```toml
[[mcp_servers]]
name = "conductor-bridge"
type = "http"
url = "http://127.0.0.1:8765/mcp"
```

### Option 2: Use CLI

```bash
codex mcp add conductor-bridge --url http://127.0.0.1:8765/mcp
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

## MCP API Examples

```bash
# Ping
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" -d '{"method": "ping"}'

# Get status
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" -d '{"method": "get_status"}'

# Run a cycle
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" -d '{"method": "run_cycle", "params": {"implementer": "simulate"}}'
```
