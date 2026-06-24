# ACP Adapter

MiniMax Agent exposes the workflow core through an Agent Client Protocol (ACP)
stdio adapter. This lets ACP clients such as Zed or OpenCode host the same
evidence-first multi-agent loop without using the custom Next.js GUI.

## Runtime Command

Run the adapter from the backend directory:

```powershell
cd C:\Workspace\MiniMax_Agent\backend
python -m app.adapters.acp_server
```

The process speaks JSON-RPC over stdio. Do not run it directly in a normal
interactive terminal unless an ACP client is attached.

## Configuration

The adapter loads `backend/.env` through the existing backend config loader.
Use the same MiniMax settings as the FastAPI server:

```env
MINIMAX_AGENT_LLM_MODE=real
MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic
MINIMAX_MODEL=MiniMax-M3
MINIMAX_API_KEY=...
MINIMAX_CONTEXT_WINDOW_TOKENS=1000000
MINIMAX_MAX_OUTPUT_TOKENS=131072
MINIMAX_THINKING=adaptive
MINIMAX_AGENT_DB_PATH=C:\Workspace\MiniMax_Agent\backend\data\minimax_agent.db
```

For local tests, set `MINIMAX_AGENT_LLM_MODE=fake`.

## Client Setup Shape

Exact UI fields vary by ACP client. The command shape is:

```json
{
  "command": "python",
  "args": ["-m", "app.adapters.acp_server"],
  "cwd": "C:\\Workspace\\MiniMax_Agent\\backend"
}
```

If the client supports environment variables in its agent config, it can pass
`MINIMAX_API_KEY` there. Otherwise keep credentials in `backend/.env`.

## What ACP Covers Now

- `initialize`
- `session/new`
- `session/prompt`
- `session/cancel`
- text prompt blocks
- embedded text resources and resource links converted into the task prompt
- MiniMax workflow token streaming back as ACP session updates
- stored tool calls, artifacts, review results, and hardware validation records
  surfaced as ACP tool-call records

The adapter intentionally does not expose direct filesystem or terminal ACP
tools. Tool execution stays behind the backend `ToolGateway`, so permission
checks and SQLite audit records remain the source of truth.

## Relationship To FastAPI

FastAPI remains available for the current web console:

```text
ACP client -> app.adapters.acp_server -> TaskRunner -> MiniMax-M3 -> SQLite
Web GUI    -> app.main FastAPI       -> TaskRunner -> MiniMax-M3 -> SQLite
```

Both paths share the same workflow runner, LLM client, tool gateway, and
persistence layer.
