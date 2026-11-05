# Monty

Monty is your butterfly friend you text for money advice. It is a personal Signal bot that
answers questions about your Monarch Money finances. You text the bot's number from your own
phone, a gateway forwards the message to one of three interchangeable agents (echo, Pi, or
deepagents), and the agent reads your data through a read-only Monarch MCP proxy.

```
Signal -> signal-cli -> gateway -> agent-echo | agent-pi | agent-deep -> monarch-proxy -> Monarch
```

Design: `docs/superpowers/specs/2026-11-04-signal-monarch-agent-design.md`.

## Prerequisites

- Docker (Compose v2) for `make up`.
- [uv](https://docs.astral.sh/uv/) for the Python packages and `make monarch-login`, `make test`, `make contract`.
- Node >= 22.19, only if you run the Pi agent outside Docker (including `make contract`). Node
  23.0 to 23.7 will not work (no zstd in zlib); try `nvm install 22`. Docker builds Pi with
  its own Node.
- A Signal number for the bot (a spare SIM) and an Anthropic API key.

## Setup

1. Configure: `cp .env.example .env`, then fill in `ANTHROPIC_API_KEY`, `OWNER_NUMBER` (your
   personal number, E.164), `OWNER_UUID` (optional), `BOT_NUMBER` (the bot's number), and
   optionally `MODEL` and `DEFAULT_AGENT` (`echo`, `pi` or `deep`).
2. Log in to Monarch: `make monarch-login`. It opens the OAuth flow (loopback on port 8765) and
   stores tokens in `data/monarch/tokens.json`. Check them later, non-interactively, with
   `uv run monarch-login --check`.
3. Register the bot's Signal number: follow `docs/runbooks/signal-registration.md`.
4. Start everything: `make up`. Use `make logs` to follow logs and `make down` to stop.

Then text the bot `hi` from your own phone. `docs/runbooks/manual-checks.md` is the full
acceptance script.

## Signal commands

- `/agent <name>` switches agent (`echo`, `pi`, `deep`).
- `/new` starts a fresh session with the current agent.
- `/which` shows the current agent, session and model.
- `/help` lists the commands.

Anything else is a question for the current agent. If Monarch login expires, Monty says so;
run `make monarch-login` on the Mac again.

## Development

- `make test` runs the unit tests for every Python package.
- `make contract` starts fake-monarch and each agent locally and runs the contract suite
  (`contract/tests`) against echo, Pi and deep. It loads `.env` if present and runs the LLM
  tests only when `ANTHROPIC_API_KEY` is set (otherwise it says so and runs the rest). Pi's
  own tests: `cd agents/pi && npm ci && npm test`.

## Where things live

- `data/gateway/gateway.db`: SQLite. The `turns` table has one row per question (agent, session,
  inbound, reply, status, tool calls, tokens).
- `data/logs/monarch-calls.jsonl`: one line per Monarch tool call (tool, args, ms, ok, size;
  never the results).
- `data/agents/pi/<session_id>/` and `data/agents/deep/checkpoints.db`: agent session state.
- `data/monarch/tokens.json` (mode 0600) and `data/signal/`: credentials. Back up
  `data/signal/`; losing it means re-registering.

`data/` and `.env` are gitignored.

## Security posture

- Monarch access is the read-only `mcp:read` OAuth scope, and the proxy exposes only a
  17-tool read allowlist and refuses everything else.
- The bot answers only `OWNER_NUMBER` in direct messages. Other senders and groups get no reply.
- Pi is created with zero built-in tools (no bash, read, write or edit); its only tools are the
  Monarch ones.
- deepagents uses its in-memory backend only, never a host-filesystem backend.
- Only the proxy publishes a port, on `127.0.0.1:8100`, for debugging.
