# Monti

Monti is your butterfly friend you text for money advice. It is a household Signal bot that
answers questions about your Monarch Money finances. The people listed in `USERS` text the bot's
number, directly or in a Signal group with it; a gateway forwards each message to one of three
interchangeable agents (echo, Pi, or deepagents), and the agent reads the household's data through
a read-only Monarch MCP proxy.

```
Signal -> signal-cli -> gateway -> agent-echo | agent-pi | agent-deep -> monarch-proxy -> Monarch
```

Design: `docs/superpowers/specs/2026-11-04-signal-monarch-agent-design.md`, amended by
`docs/superpowers/specs/2026-11-05-monty-chats-and-mise-design.md` (users, chats, groups).

## Prerequisites

- Docker (Compose v2) for `mise run up`.
- [mise](https://mise.jdx.dev/): run `mise install` once. It provides Node 22 (Pi needs >= 22.19),
  Python 3.13 and uv, and runs the tasks below (`mise run monarch-login`, `test`, `contract`,
  `up`, `down`, `logs`). Docker builds Pi with its own Node.
- A Signal number for the bot (a spare SIM) and an Anthropic API key.

## Setup

1. Configure: `cp .env.example .env`, then fill in `ANTHROPIC_API_KEY`, `USERS` (see below),
   `BOT_NUMBER` (the bot's number, E.164), and optionally `BOT_UUID`, `MODEL` and
   `DEFAULT_AGENT` (`echo`, `pi` or `deep`). `OWNER_NUMBER` and `OWNER_UUID` are gone: if your
   `.env` predates `USERS`, replace them with a `USERS` line, or the gateway won't start. When
   upgrading from v1, also delete `data/gateway/` before the first run
   (leave `data/monarch/`, which holds your Monarch login).
2. Log in to Monarch: `mise run monarch-login`. It opens the OAuth flow (loopback on port 8765) and
   stores tokens in `data/monarch/tokens.json`. Check them later, non-interactively, with
   `uv run monarch-login --check`.
3. Register the bot's Signal number: follow `docs/runbooks/signal-registration.md`.
4. Start everything: `mise run up`. Use `mise run logs` to follow logs and `mise run down` to stop.

Then text the bot `hi` from your own phone. `docs/runbooks/manual-checks.md` is the full
acceptance script.

### Users

`USERS` lists everyone Monti answers, comma-separated, as `Name=+E164[/uuid]`:

```sh
USERS=Keith=+16175550100,Alex=+16175550101/0b8a3c1e-1d2f-4a5b-9c6d-7e8f9a0b1c2d
```

The name is what the agent sees on each message's `From:` line. The uuid (the person's Signal
ACI) is optional; add it if someone's messages arrive without a phone number (for example
because they hide their number from people who don't have it). Anyone not listed gets no reply.

### Chats and groups

Each DM with Monti and each group Monti is in is its own chat, with its own agent, session and
memory. `/agent`, `/new` and `/which` apply to the chat you send them in; in a group, whoever
sends a command changes it for everyone there. Monti answers every message in an allowed group
(no mention needed). Chats run independently: a slow answer in one doesn't hold up another, and
messages in one chat are answered in order.

To use Monti in a group, add the bot's number to a Signal group like any other contact. A group
of just you and Monti works as a separate thread. See `docs/runbooks/signal-registration.md`.

**Group security rule.** Monti speaks in a group only when every member other than Monti is in
`USERS`, because its answers show the household's finances to everyone there. If anyone else is in
the group, or has been invited to it, Monti stays silent, even when someone on the list writes.
Membership is checked fail-closed: if signal-cli can't list the group's members (error, timeout,
unknown group, a member it can't identify), Monti stays silent and logs why. Membership is cached
for 60 seconds per group and refreshed when the group changes.

## Signal commands

- `/agent <name>` switches agent (`echo`, `pi`, `deep`).
- `/new` starts a fresh session with the current agent.
- `/which` shows the current agent, session and model.
- `/help` lists the commands.

Anything else is a question for the current agent. If Monarch login expires, Monti says so;
run `mise run monarch-login` on the Mac again.

## Development

- `mise run test` runs the unit tests for every Python package.
- `mise run contract` starts fake-monarch and each agent locally and runs the contract suite
  (`contract/tests`) against echo, Pi and deep. It loads `.env` if present and runs the LLM
  tests only when `ANTHROPIC_API_KEY` is set (otherwise it says so and runs the rest). Pi's
  own tests: `cd agents/pi && npm ci && npm test`.

## Where things live

- `data/gateway/gateway.db`: SQLite. The `turns` table has one row per question (chat, sender,
  agent, session, inbound, reply, status, tool calls, tokens). The schema has no migrations:
  clear `data/gateway/` when upgrading from the single-owner version.
- `data/logs/monarch-calls.jsonl`: one line per Monarch tool call (tool, args, ms, ok, size;
  never the results).
- `data/agents/pi/<session_id>/` and `data/agents/deep/checkpoints.db`: agent session state.
- `data/monarch/tokens.json` (mode 0600) and `data/signal/`: credentials. Back up
  `data/signal/`; losing it means re-registering.

`data/` and `.env` are gitignored.

## Security posture

- Monarch access is the read-only `mcp:read` OAuth scope, and the proxy exposes only a
  17-tool read allowlist and refuses everything else.
- The bot answers only people in `USERS`. In a group it also requires every other member to be
  in `USERS` (fail-closed, see above). Everyone else gets no reply.
- Pi is created with zero built-in tools (no bash, read, write or edit); its only tools are the
  Monarch ones.
- deepagents uses its in-memory backend only, never a host-filesystem backend.
- Only the proxy publishes a port, on `127.0.0.1:8100`, for debugging.
