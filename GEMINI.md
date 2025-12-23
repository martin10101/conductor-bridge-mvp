# Conductor Bridge Project Context

## Project Overview
This is the Conductor Bridge MVP - an MCP hub that orchestrates between Gemini Conductor (for planning/review) and implementers like Codex or Claude (for coding).

## Current State
- Check `state/state.json` for current phase and cycle count
- Check `state/events.jsonl` for history of actions
- Check `state/artifacts/` for plan.md, handoff.md, review.md

## Architecture
```
User Request → Claude/Codex → Conductor Bridge → Gemini (plan)
                                              → Implementer (code)
                                              → Gemini (review)
                                              → Loop
```

## Key Files
- `conductor_bridge/server.py` - MCP HTTP server
- `conductor_bridge/runner.py` - Cycle runner
- `conductor_bridge/state.py` - State management
- `conductor_bridge/gemini_client.py` - Gemini CLI wrapper
- `conductor_bridge/implementer.py` - Codex/Claude adapters

## How to Use
1. Start server: `python -m conductor_bridge.server --http --port 8765`
2. Run cycles: `python -m conductor_bridge.runner --implementer simulate --cycles 3`

## Conductor Instructions
When planning or reviewing:
1. Read the current artifacts in `state/artifacts/`
2. Consider the project context
3. Write structured markdown output
4. Focus on actionable steps
