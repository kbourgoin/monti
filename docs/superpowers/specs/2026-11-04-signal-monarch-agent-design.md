# Monti — Design

Date: 2026-11-04
Status: Draft, awaiting approval

## Intent

A personal assistant I can text over Signal to ask about my Monarch Money
finances ("what did we spend on restaurants this month?", "anything weird
this week?"). It's built from scratch, not on OpenClaw, for two reasons:

1. I want the assistant itself.
2. I want to go deeper on building agents. We use deepagents (Python) at work,
   and I want to compare it directly with Pi (badlogic/pi-mono) on the same job.

So the core idea is **one gateway with agents that can be swapped**. The
gateway handles Signal, sessions and safety. Agents are separate services
behind a small HTTP contract, so Pi, deepagents and future agents (Vercel Eve,
something hand-rolled) run side by side and can be switched from the chat.

## Goals (v1)

- Text the Tello burner number on Signal and get answers grounded in my Monarch data.
- Two working agents, **Pi** (TypeScript) and **deepagents** (Python), switchable from chat.
- Each agent keeps its own conversation memory with the framework's native
  tools, because learning those is part of the point.
- Every turn and tool call logged locally, so I can see what each agent did.

## Non-goals (v1)

- Scheduled or proactive messages.
- Writing to Monarch (recategorizing, rules, etc.).
- Other channels (WhatsApp, SMS, Telegram).
- Multi-user support, group chats, voice and attachments.
- Seeing accounts that Monarch's MCP withholds (USAA, Fidelity and Amazon go
  through Finicity and are hidden). Accepted as a limitation.
- Hosting anywhere but my Mac. An always-on host comes later.
- Streaming replies.

## Anti-goals (failure even if it "works")

- **Anyone other than me can get a response.** Messages from any other number,
  and from any group, are dropped silently.
- **An agent can run commands or touch files on the host.** Pi includes
  bash/read/write/edit tools by default. They must not be registered. The agents'
  only real-world capability is the Monarch read tools.
- **An agent can change Monarch data.** Blocked twice: the OAuth token is
  requested with scope `mcp:read` only, *and* the proxy allows only read tools.
- Monarch credentials or financial transcripts end up anywhere but local disk
  (not in git, logs shipped elsewhere, etc.).

## Architecture

```
My phone (Signal)
      │
signal-cli  (Docker; registered to the Tello number)
      │  REST + websocket (or JSON-RPC)
Gateway  (Python, asyncio)
  - allowlist, chat commands, session routing, transcript log (SQLite)
      │  HTTP turn contract
 ┌────┴──────────────┐
agent-pi (TS/Node)   agent-deep (Python)        [+ agent-echo for tests]
 └────┬──────────────┘
      │  MCP (streamable HTTP, localhost, unauthenticated)
monarch-proxy  (Python)
  - owns Monarch OAuth tokens, read-only tool allowlist, call log
      │  MCP + Bearer token
https://api.monarch.com/mcp
```

All services bind to localhost or the Compose-internal network. Nothing is
exposed publicly; signal-cli only makes outbound connections.

### Repo layout

Python everywhere except `agents/pi/`, which has to be TypeScript because Pi only
ships a TypeScript SDK. The Python packages form a single uv workspace at the
repo root (shared dev tooling: pytest, ruff); the turn contract is the only
thing `agents/pi/` shares with the rest.

```
mm-text/
  gateway/          Python (uv)
  agents/pi/        TypeScript (Node), Pi SDK
  agents/deep/      Python (uv), deepagents + langchain-mcp-adapters, FastAPI
  agents/echo/      Python (uv), trivial reference agent (tests, signal bring-up)
  monarch-proxy/    Python (uv)
  pyproject.toml    uv workspace root (members: gateway, agents/deep, agents/echo, monarch-proxy)
  prompts/system.md shared system prompt used by every agent
  data/             gitignored: tokens, SQLite, agent memory, logs
  docker-compose.yml
  .env.example      ANTHROPIC_API_KEY, OWNER_NUMBER, BOT_NUMBER, MODEL, ...
```

## Components

### 1. signal-cli

- Registered to the Tello number as its own Signal account (not linked to my
  personal account).
- **Manual steps for me:** solve the Signal captcha, then receive the SMS
  verification code on the Tello phone. The builder writes a short runbook for this.
- Image: `bbernhard/signal-cli-rest-api` in `json-rpc` mode is the default
  choice. Using signal-cli's native JSON-RPC/SSE daemon instead is the builder's call.
- Keep the image current, since old signal-cli versions break when Signal's API changes.

### 2. Gateway (Python)

Responsibilities:
- Receive incoming messages and drop anything not from `OWNER_NUMBER`, or from a group.
- Handle chat commands (matched exactly, case-insensitive, before any agent sees the message):
  - `/agent <name>`: switch my active agent (`pi`, `deep`, `echo`, from config). Replies with a confirmation.
  - `/new`: start a new session with the current agent.
  - `/which`: show the active agent, session ID and model.
  - `/help`: list the commands.
- Send a typing indicator while the agent works.
- Call the active agent with the turn contract. Timeout is 120s.
- Format replies for Signal: plain text, split into several messages at
  paragraph boundaries when long (about 1,500 characters per message; exact threshold is the builder's call).
- Log everything to SQLite.

State (SQLite, `data/gateway.db`):
- `active_agent`: owner → agent name (defaults to `pi`).
- `sessions`: (owner, agent) → current `session_id`. `/new` replaces it with a new UUID.
  Switching agents resumes that agent's current session for me.
- `turns`: id, session_id, agent, inbound text, reply text, started/finished
  timestamps, status (ok / timeout / error), tool_calls JSON, token usage.

Errors that reach me as plain text:
- Agent timeout: "That took too long. Try again or /new."
- Agent unreachable or erroring: "The <name> agent is down."
- Monarch auth failure (agent reports `error.code = "monarch_auth"`):
  "Monarch login expired. Run `make monarch-login` on the Mac."

### 3. Agent turn contract (HTTP)

All agents implement:

```
GET  /health
  → 200 {"name": "pi", "framework": "pi-mono", "model": "claude-sonnet-5-5"}

POST /turn
  {"session_id": "uuid", "message_id": "uuid", "text": "…", "now": "ISO-8601 with tz"}
  → 200 {"reply": "…",
         "tool_calls": [{"name": "GetTransactions", "args": {...}, "ms": 812, "ok": true}],
         "usage": {"input_tokens": 0, "output_tokens": 0}}
  → 4xx/5xx {"error": {"code": "monarch_auth" | "timeout" | "internal", "message": "…"}}
```

- Synchronous request/response. Agents handle one turn at a time per session.
- **Agents own their memory**, keyed by `session_id`. The gateway never sends history.
- A shared contract test suite (run against each agent with Monarch faked or
  stubbed) proves an agent conforms: health, a basic turn, memory within a
  session, isolation across sessions, and the error shape.

### 4. agent-pi (TypeScript / Node)

- Built on the Pi SDK (`createAgentSession`). Check the current package name:
  the repo may have moved to `earendil-works/pi`.
- **No built-in tools registered.** Monarch tools come from the proxy, through
  `pi-mcp-adapter` or a thin custom tool wrapper around an MCP client. The
  builder picks whichever keeps Pi's tool set explicit and auditable.
- Memory: Pi's session persistence under `data/agents/pi/<session_id>`.

### 5. agent-deep (Python)

- `create_deep_agent(model=…, tools=<MCP tools>, system_prompt=…)`, with tools
  from `langchain-mcp-adapters` `MultiServerMCPClient` pointed at the proxy.
- Memory: LangGraph `SqliteSaver` checkpointer at `data/agents/deep/checkpoints.db`,
  with `thread_id = session_id`.
- deepagents' built-in planning, virtual-filesystem and subagent tools are
  allowed: the default state backend is in memory and doesn't touch host files.
  It **must not** be configured with a host-filesystem backend.
- Served by FastAPI and uvicorn.

### 6. monarch-proxy (Python)

- **Login CLI** (`make monarch-login`): registers a client dynamically at
  `https://api.monarch.com/oauth/register` (public client, auth method `none`),
  runs the authorization-code flow with PKCE S256 and a loopback redirect
  (`http://127.0.0.1:<port>/callback`), requests **scope `mcp:read` only**, and
  saves the client info and tokens to `data/monarch/tokens.json` (mode 0600).
  Runs on the host because it needs a browser.
- **Proxy server:** an MCP streamable-HTTP server at `127.0.0.1:8100/mcp`, no auth.
  It forwards `tools/list` and `tools/call` upstream with the Bearer token,
  refreshing it with the refresh-token grant when it expires or on a 401.
  If the refresh fails, tool calls return an error the agents can map to `monarch_auth`.
- **Tool allowlist:** only read tools are exposed (`Get*`, plus any other
  read-only tools the builder confirms). Everything else is left out of
  `tools/list` and refused on `tools/call`.
- **Call log:** JSONL at `data/logs/monarch-calls.jsonl` with timestamp, tool,
  args, latency, ok/error and result size. No result bodies.
- FastMCP's proxy and OAuth-client support may cover most of this. Whether to
  use it or hand-roll with the `mcp` SDK is the builder's call.

Verified 2026-11-04: Monarch publishes OAuth metadata with
`registration_endpoint`, `code_challenge_methods_supported: ["S256"]`,
`grant_types_supported: ["authorization_code","refresh_token"]`,
`token_endpoint_auth_methods_supported` including `none`, and scopes
`mcp:read` and `mcp:write`. **Not yet verified:** that registration accepts a
loopback redirect URI, and how long refresh tokens last.

### Shared system prompt (`prompts/system.md`)

Every agent uses it, so the comparison is fair. It covers:
- who I am to the assistant: a household finance helper for one person
- answer in **plain text**: no markdown tables or headers, short paragraphs, $ amounts
- current date and time are injected per turn from `now`; timezone America/New_York
- the Monarch tools' `intent` field must be a generic task category, never personal details
- be honest when data is missing or truncated

### Model

Both agents default to `claude-sonnet-5-5` through the Anthropic API
(`ANTHROPIC_API_KEY`), configurable per agent with an environment variable. The
same default model keeps the framework comparison apples-to-apples.

## Running it (v1)

- `docker compose up` starts signal-cli, the gateway, the three agents and monarch-proxy.
- `data/` is mounted into the containers that need it. The login CLI runs on
  the host and writes into `data/monarch/`.
- `.env` holds secrets and numbers. `.env` and `data/` are gitignored.

## Build order

1. **Monarch proxy and login.** Prove registration, login and refresh work, and
   that tool calls go through the proxy (check with MCP Inspector). If the
   loopback redirect is rejected, fall back to a paste-the-code flow before moving on.
2. **Signal bring-up.** Register the number (runbook plus my manual steps).
   Gateway and the echo agent working end to end from my phone, with the
   allowlist and commands working.
3. **agent-pi** end to end from Signal.
4. **agent-deep** end to end, with `/agent` switching between them.
5. **Polish:** typing indicators, error messages, splitting long replies,
   contract tests passing for all three agents.

## Testing

- Gateway: unit tests for allowlist, command parsing, session rotation and
  reply splitting, using a fake signal client and fake agents.
- Agents: shared contract suite (above).
- Proxy: unit tests for allowlist filtering and token refresh, using a fake upstream.
- Manual: a short script of real questions sent over Signal to each agent.

## Later (explicitly out of v1)

- **Comparison harness:** the same set of Monarch questions run against each agent
  through `/turn`, recording answers, tool calls, latency and tokens side by side.
- Moving to an always-on host.
- Write actions behind a confirm-by-reply flow.
- Scheduled digests.
- More agents (Vercel Eve, hand-rolled).

## Left to the builder

- Exact libraries for the Signal client, MCP proxy and Pi MCP wiring
  (preferences are stated above).
- Reply-splitting threshold, port numbers, Makefile and script ergonomics.
- Test framework choices per language.
