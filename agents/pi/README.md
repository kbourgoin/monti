# agent-pi

The Pi agent (TypeScript). It implements the gateway turn contract on port 8202, and its
only tools are the Monarch read tools from the proxy.

Pi SDK: `@earendil-works/pi-coding-agent` 1.5.0, plus `@earendil-works/pi-ai` 1.5.0. The repo
moved from badlogic/pi-mono, and `@mariozechner/pi-coding-agent` stopped at 0.73.1. Versions are
pinned exactly because the SDK changes quickly.

## Running

- Node >= 22.19 (Pi's `engines` field). Node 23.0-23.7 do not work: Pi's undici needs
  `zlib.createZstdDecompress`. The source runs directly with Node's type stripping
  (`node src/server.ts`), so there is no build step.
- `npm ci`, then `npm test` (vitest). Tests start the repo's fake Monarch (`uv run fake-monarch`)
  on port 8191 and use Pi's faux model provider, so they make no LLM calls.
- `npm run typecheck` runs tsc.
- `MONARCH_MCP_URL=http://127.0.0.1:8190/mcp npm start` runs against a local fake Monarch, with
  `ANTHROPIC_API_KEY` set.

Environment: `AGENT_HOST` (default 127.0.0.1; compose sets 0.0.0.0), `MONARCH_MCP_URL`
(default `http://monarch-proxy:8100/mcp`), `MODEL` (default `claude-sonnet-5-5`),
`ANTHROPIC_API_KEY`, `PI_DATA_DIR` (default `<repo>/data/agents/pi`) and `SYSTEM_PROMPT_PATH`
(default `<repo>/prompts/system.md`).

## Pi SDK surface used

The code is in `src/sessions.ts`.

(a) **Zero built-in tools.** `createAgentSession({ noTools: "all", tools: monarchNames, customTools })`.
`tools` is an allowlist of exact names. Pi drops every tool that doesn't match it, built-ins
included (read, bash, powershell, edit, write, grep, find, ls), from the registry entirely, not
just from the active set, so `setActiveToolsByName(["bash"])` afterwards does nothing.
`noTools: "all"` also empties the default selection. A custom `ResourceLoader` loads no
extensions, skills, prompt templates or context files, so nothing can add tools. Sessions also
get in-memory settings, so nothing is read from `~/.pi`. Belt and braces: after creating a
session we check that `getAllTools()` is exactly the Monarch names, each with
`sourceInfo.source === "sdk"`, and refuse the session otherwise.

(b) **Custom tools with a JSON schema.** `customTools: ToolDefinition[]`, with `{ name, label,
description, parameters, execute(toolCallId, params, signal) }`. `parameters` can be any JSON
Schema (Pi validates arguments with pi-schema, not TypeBox-only), so the MCP `inputSchema` is
passed through unchanged. `execute` returns `{ content: [{type: "text", text}], details, isError }`.

(c) **Persist and resume from a directory.** `sessionManager: SessionManager.continueRecent(dir, dir)`,
where `dir` is `PI_DATA_DIR/<session_id>`. It opens the newest `.jsonl` session in `dir`, or starts
a new one. Pi writes the transcript as JSONL as the turn runs.

(d) **Choosing the Anthropic model.** `ModelRuntime.create({ credentials: new InMemoryCredentialStore(),
modelsPath: null, refreshOnCreate: false })`, then `setRuntimeApiKey("anthropic", key)` and
`modelRuntime.getModel("anthropic", MODEL)`. Pass both to `createAgentSession({ model, modelRuntime })`.
`thinkingLevel: "off"`.

(e) **Token usage after a prompt.** `session.subscribe()`. On each `message_end` event whose
message has role `assistant`, read `message.usage` (`input`, `output`, `cacheRead`, `cacheWrite`).
A turn reports `input_tokens = input + cacheRead + cacheWrite` and `output_tokens = output`,
summed over the turn's model calls.

Also: `session.prompt(text, { expandPromptTemplates: false })` so text starting with `/` is never
treated as a command. Pi rejects a prompt while the session is busy, so turns go through a
per-session mutex. `session.abort()` stops a run when the 110s turn timeout fires.
