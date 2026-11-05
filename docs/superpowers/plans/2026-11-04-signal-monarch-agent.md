# Signal ↔ Monarch Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Text a Signal number and get answers about my Monarch finances from a swappable agent (Pi or deepagents).

**Architecture:** signal-cli (Docker) → Python gateway (allowlist, commands, sessions, SQLite log) → agents behind an HTTP turn contract (`/health`, `/turn`) → a local read-only MCP proxy that owns Monarch OAuth → `https://api.monarch.com/mcp`. Agents own their memory, keyed by `session_id`.

**Tech Stack:** Python ≥3.12 with a uv workspace (FastAPI, httpx, websockets, pydantic, `mcp` SDK, deepagents, langchain-mcp-adapters, langgraph-checkpoint-sqlite, pytest, respx, ruff). TypeScript on Node ≥22 for the Pi agent (Pi SDK, `@modelcontextprotocol/sdk`, vitest). Docker Compose, with `bbernhard/signal-cli-rest-api` in `json-rpc` mode.

**Spec:** `docs/superpowers/specs/2026-11-04-signal-monarch-agent-design.md`

## Global Constraints

- Python packages are uv workspace members: `gateway`, `contract`, `agents/echo`, `agents/deep`, `monarch-proxy`. Only `agents/pi/` is TypeScript.
- The Monarch OAuth scope requested is exactly `mcp:read`. `mcp:write` is never requested.
- The proxy exposes exactly these read tools and refuses everything else: `GetAccounts, GetBudget, GetBusinesses, GetCashFlow, GetCategories, GetCreditScoreHistory, GetGoals, GetHouseholdMembers, GetInvestments, GetMerchants, GetNetWorthHistory, GetRealEstate, GetRecurring, GetSpendingByCategory, GetTags, GetTransactions, ListRules`.
- Ports: monarch-proxy `8100` (`/mcp`), agent-echo `8201`, agent-pi `8202`, agent-deep `8203`, signal-cli-rest-api `8080`, OAuth loopback `8765`. No compose service publishes a port except the proxy, at `127.0.0.1:8100` for debugging.
- Paths: `data/monarch/tokens.json` (mode 0600), `data/gateway.db`, `data/agents/pi/<session_id>/`, `data/agents/deep/checkpoints.db`, `data/logs/monarch-calls.jsonl`. `data/` and `.env` are gitignored (already done).
- Default model: `claude-sonnet-5-5`, overridable with env `MODEL`. Timezone: `America/New_York`.
- Gateway turn timeout: 120s. Agents give up internally at 110s and return `timeout`.
- Reply split limit: 1500 characters.
- Gateway error copy, verbatim:
  - `That took too long. Try again or /new.`
  - `The <name> agent is down.`
  - ``Monarch login expired. Run `make monarch-login` on the Mac.``
- The auth-failure marker string returned by the proxy in tool errors is `MONARCH_AUTH_REQUIRED`.
- Pi built-in tools (bash/read/write/edit/etc.) are never registered. deepagents is never given a host-filesystem backend.
- Contract error codes: `monarch_auth` → HTTP 502, `timeout` → 504, `internal` → 500 (also 400 for a malformed request).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A second text arrives while a turn is still running.** Turns for one owner run one at a time, in arrival order. Tested in Task 5 (`test_messages_processed_serially`).
2. **Non-message Signal envelopes** (read receipts, typing, reactions, sync messages, attachment-only, edits) are ignored without crashing and without a reply. Tested in Task 5 (`test_parse_envelope_ignores_non_text`).
3. **The sender arrives with a UUID only** (`sourceNumber` is null). It's allowed when it matches `OWNER_UUID` and dropped otherwise. Tested in Task 4 (`test_owner_match_by_uuid`).
4. **One huge paragraph, or an empty reply.** Splitting falls back from paragraph to line to hard cut, and no chunk is ever over 1500. An empty or whitespace-only reply sends `(no reply)` instead of an empty Signal message. Tested in Task 4 (`test_split_*`).
5. **Sloppy or unknown commands** (`/Agent PI `, `/agent foo`, `/new now`). Case and whitespace are tolerated. An unknown agent gets a reply listing the valid agents and leaves the active agent unchanged. Tested in Task 4 (`test_unknown_agent_lists_choices`).

---

### Task 1: Workspace scaffold + Monarch OAuth login

**Files:**
- Create: `pyproject.toml` (uv workspace root, dev deps pytest, pytest-asyncio, respx, ruff), `Makefile`, `.env.example`
- Create: `monarch-proxy/pyproject.toml`, `monarch-proxy/src/monarch_proxy/{__init__,tokens,auth,login}.py`
- Create: `contract/pyproject.toml`, `contract/src/turn_contract/__init__.py` (only `AUTH_ERROR_MARKER` for now)
- Test: `monarch-proxy/tests/test_tokens.py`, `monarch-proxy/tests/test_auth.py`

**Interfaces:**
- Produces:
  - `turn_contract.AUTH_ERROR_MARKER: str = "MONARCH_AUTH_REQUIRED"`
  - `monarch_proxy.tokens.FileTokenStorage(path: Path)` implements the `mcp.client.auth.TokenStorage` protocol: `async get_tokens() -> OAuthToken | None`, `async set_tokens(OAuthToken)`, `async get_client_info() -> OAuthClientInformationFull | None`, `async set_client_info(OAuthClientInformationFull)`. A single JSON file `{"tokens": …, "client_info": …}`, written atomically (temp file + `os.replace`) with mode 0600.
  - `monarch_proxy.auth.MONARCH_MCP_URL = "https://api.monarch.com/mcp"`, `REDIRECT_PORT = 8765`
  - `monarch_proxy.auth.build_client_metadata() -> OAuthClientMetadata`
  - `monarch_proxy.auth.MonarchAuthRequired(Exception)`
  - `monarch_proxy.auth.make_provider(storage: FileTokenStorage, *, interactive: bool) -> OAuthClientProvider`. When non-interactive, the redirect and callback handlers raise `MonarchAuthRequired`.
  - `monarch_proxy.auth.wait_for_callback(port: int) -> Awaitable[tuple[str, str | None]]` runs a one-shot loopback HTTP server and returns `(code, state)`.
  - CLI: `uv run monarch-login` (script entry `monarch_proxy.login:main`). Makefile target `monarch-login` runs it.

- [ ] **Step 1: Scaffold the workspace.** Root `pyproject.toml` with `[tool.uv.workspace] members = ["contract", "monarch-proxy"]`. Each later task appends its own member (`agents/echo` in Task 3, `gateway` in Task 4, `agents/deep` in Task 7) so `uv sync` keeps working. `monarch-proxy` depends on `mcp`, `httpx`, `turn-contract` (workspace source). `.env.example` lists `ANTHROPIC_API_KEY, MODEL, OWNER_NUMBER, OWNER_UUID, BOT_NUMBER, DEFAULT_AGENT`. Run `uv sync` and expect success.

- [ ] **Step 2: Write failing tests**

```python
# test_tokens.py
async def test_roundtrip_and_mode(tmp_path):
    s = FileTokenStorage(tmp_path / "tokens.json")
    assert await s.get_tokens() is None
    tok = OAuthToken(access_token="a", token_type="Bearer", refresh_token="r", expires_in=3600)
    await s.set_tokens(tok)
    assert (await FileTokenStorage(tmp_path / "tokens.json").get_tokens()).refresh_token == "r"
    assert stat.S_IMODE((tmp_path / "tokens.json").stat().st_mode) == 0o600

async def test_client_info_preserved_when_tokens_written(tmp_path): ...
    # set_client_info then set_tokens; both readable afterwards

# test_auth.py
def test_metadata_is_read_only():
    m = build_client_metadata()
    assert m.scope == "mcp:read"
    assert [str(u) for u in m.redirect_uris] == ["http://127.0.0.1:8765/callback"]
    assert m.token_endpoint_auth_method == "none"
    assert set(m.grant_types) == {"authorization_code", "refresh_token"}

async def test_noninteractive_provider_raises_on_redirect(tmp_path):
    p = make_provider(FileTokenStorage(tmp_path / "t.json"), interactive=False)
    with pytest.raises(MonarchAuthRequired):
        await p.context.redirect_handler("https://example/authorize")  # adjust attr to SDK

async def test_callback_server_captures_code():
    task = asyncio.create_task(wait_for_callback(18765))
    r = await httpx.AsyncClient().get("http://127.0.0.1:18765/callback?code=abc&state=xyz")
    assert r.status_code == 200
    assert await task == ("abc", "xyz")
```

- [ ] **Step 3: Run tests and expect failure.** `uv run pytest monarch-proxy -v` should fail with ImportError.

- [ ] **Step 4: Implement `tokens.py` and `auth.py`.** Use the `mcp` SDK's `OAuthClientProvider(server_url=MONARCH_MCP_URL, client_metadata=…, storage=…, redirect_handler=…, callback_handler=…)`. Check the constructor and handler signatures against the installed SDK version and adapt the test's handler access. The interactive redirect handler calls `webbrowser.open(url)` and prints the URL. The interactive callback handler awaits `wait_for_callback(REDIRECT_PORT)`.

- [ ] **Step 5: Run tests and expect them to pass.** `uv run pytest monarch-proxy -v`

- [ ] **Step 6: Implement `login.py:main()`.** It builds an interactive provider, opens `streamablehttp_client(MONARCH_MCP_URL, auth=provider)`, initializes a `ClientSession`, calls `list_tools()`, and prints `Logged in. <n> tools upstream, <m> allowed read tools.` (the allowlist arrives in Task 2; until then just print the upstream count).

- [ ] **Step 7: Manual check (needs Keith).** Run `make monarch-login`, approve in the browser, and expect the success line plus `data/monarch/tokens.json` with mode 0600. **If dynamic registration rejects the redirect URI:** retry with `http://localhost:8765/callback`. If that's rejected too, stop and report to Keith, because that's a design change.

- [ ] **Step 8: Manual refresh check.** Replace `access_token` in `tokens.json` with `"bogus"`, then run `uv run monarch-login --check` (a non-interactive provider plus `list_tools`). Expect success, and `tokens.json` should now hold a new access token, proving the 401 → refresh path works. If the SDK doesn't refresh on 401, add one explicit refresh-and-retry in `--check`, and reuse it in Task 2's `Upstream`.

- [ ] **Step 9: Commit.** `git add -A && git commit -m "feat(proxy): Monarch OAuth login with read-only scope"`

---

### Task 2: monarch-proxy server (allowlist, forwarding, call log)

**Files:**
- Create: `monarch-proxy/src/monarch_proxy/{allowlist,calllog,upstream,server}.py`
- Modify: `monarch-proxy/src/monarch_proxy/login.py` (print the allowed count)
- Test: `monarch-proxy/tests/test_allowlist.py`, `monarch-proxy/tests/test_server.py`

**Interfaces:**
- Consumes: `make_provider`, `FileTokenStorage`, `MonarchAuthRequired`, `AUTH_ERROR_MARKER` (Task 1)
- Produces:
  - `allowlist.READ_TOOLS: frozenset[str]` (the 17 names in Global Constraints), `allowlist.is_allowed(name: str) -> bool`
  - `calllog.CallLog(path: Path).record(*, tool: str, args: dict, ms: int, ok: bool, result_bytes: int) -> None` appends one JSON line with an added `ts` (ISO-8601 UTC). It never records result bodies.
  - `upstream.Upstream` protocol with `async list_tools() -> list[mcp.types.Tool]` and `async call_tool(name: str, args: dict) -> mcp.types.CallToolResult`. These raise `MonarchAuthRequired` when the login is unusable. `upstream.MonarchUpstream(storage)` is the real implementation: it opens one session at startup and, on a transport error, reconnects once and retries.
  - `server.build_server(upstream: Upstream, log: CallLog) -> mcp.server.lowlevel.Server`
  - CLI `uv run monarch-proxy` serves streamable HTTP at `$PROXY_HOST:8100/mcp` (`PROXY_HOST` defaults to `127.0.0.1`; compose sets `0.0.0.0`).

- [ ] **Step 1: Write failing tests.** Use a `FakeUpstream` whose `list_tools` returns `GetAccounts`, `GetTransactions` and `UpdateTransaction`, and whose `call_tool` records calls and returns a text result. Connect with `mcp.shared.memory.create_connected_server_and_client_session(build_server(fake, CallLog(tmp)))`.

```python
def test_allowlist_exact():
    assert is_allowed("GetTransactions") and is_allowed("ListRules")
    assert not is_allowed("UpdateTransaction") and not is_allowed("ReportIssue")
    assert not is_allowed("GetSomethingNew")   # unknown tools denied, even with Get prefix

async def test_list_hides_write_tools(): names == {"GetAccounts", "GetTransactions"}

async def test_write_call_refused_and_not_forwarded():
    r = await client.call_tool("UpdateTransaction", {})
    assert r.isError and fake.calls == []

async def test_read_call_forwarded_and_logged(tmp_path):
    r = await client.call_tool("GetAccounts", {"intent": "budgeting"})
    assert not r.isError and fake.calls == [("GetAccounts", {"intent": "budgeting"})]
    line = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert line["tool"] == "GetAccounts" and line["ok"] is True and "result" not in line
    assert set(line) == {"ts", "tool", "args", "ms", "ok", "result_bytes"}

async def test_auth_failure_returns_marker():
    fake.raise_auth = True
    r = await client.call_tool("GetAccounts", {})
    assert r.isError and AUTH_ERROR_MARKER in r.content[0].text
```

- [ ] **Step 2: Run and expect failure.** `uv run pytest monarch-proxy -v`
- [ ] **Step 3: Implement `allowlist.py`, `calllog.py`, `server.py`, `upstream.py`.** For `tools/list`, return the upstream tools filtered by `is_allowed`. A refused `tools/call` returns `isError=True` with the text `Tool <name> is not available (read-only proxy).`. `MonarchAuthRequired` maps to `isError=True` with the text `MONARCH_AUTH_REQUIRED: run make monarch-login`.
- [ ] **Step 4: Run and expect a pass.** `uv run pytest monarch-proxy -v`
- [ ] **Step 5: Manual check.** Run `uv run monarch-proxy`, then `npx @modelcontextprotocol/inspector` against `http://127.0.0.1:8100/mcp`. Expect exactly the 17 tools (or the subset upstream currently offers), and calling `GetAccounts` should return data. `data/logs/monarch-calls.jsonl` should get a line.
- [ ] **Step 6: Commit.** `feat(proxy): read-only MCP proxy with call log`

---

### Task 3: Turn contract package, fake Monarch, contract suite, echo agent

**Files:**
- Modify: `contract/src/turn_contract/__init__.py`
- Create: `contract/src/turn_contract/{models,messages,fake_monarch}.py`, `contract/tests/test_contract.py`, `contract/tests/conftest.py`
- Create: `agents/echo/pyproject.toml`, `agents/echo/src/agent_echo/app.py`
- Test: `agents/echo/tests/test_echo.py` (plus the contract suite run against echo)

**Interfaces:**
- Produces (`turn_contract`):
  - pydantic models `TurnRequest(session_id: str, message_id: str, text: str, now: datetime)`, `ToolCall(name: str, args: dict, ms: int, ok: bool)`, `Usage(input_tokens: int = 0, output_tokens: int = 0)`, `TurnResponse(reply: str, tool_calls: list[ToolCall] = [], usage: Usage = Usage())`, `ErrorBody(code: Literal["monarch_auth","timeout","internal"], message: str)`, `ErrorResponse(error: ErrorBody)`, `Health(name: str, framework: str, model: str)`
  - `ERROR_STATUS = {"monarch_auth": 502, "timeout": 504, "internal": 500}`
  - `messages.format_user_message(text: str, now: datetime) -> str` returns `"Current time: <now.isoformat()>\n\n<text>"`. Every agent uses it, since the system prompt is static.
  - `fake_monarch`: run with `uv run fake-monarch --port 8190`. It's an MCP streamable-HTTP server with `GetAccounts` (returns `{"accounts":[{"name":"Webster Checking","balance":1234.56}]}`) and `GetTransactions` (two fixed transactions), plus `POST /_mode {"mode":"ok"|"auth_error"}`. In `auth_error` mode every tool returns `isError` with `AUTH_ERROR_MARKER`, the same way the proxy does.
  - Contract suite: `AGENT_URL=http://127.0.0.1:8201 uv run pytest contract/tests` runs the non-LLM tests. Add `-m llm` (with `FAKE_MONARCH_URL` set and the agent pointed at the fake) for the LLM tests.
- Agent-echo: FastAPI on 8201. `/health` returns `{"name":"echo","framework":"echo","model":"none"}`. `/turn` replies `echo: <text>`. Its in-memory per-session turn counter shows up as `(turn N)`.

- [ ] **Step 1: Write the contract suite.**

```python
def test_health(): r = get("/health"); assert r.status_code == 200; Health.model_validate(r.json())
def test_basic_turn(): r = post_turn("hello"); assert r.status_code == 200; assert TurnResponse.model_validate(r.json()).reply.strip()
def test_malformed_request(): r = post("/turn", json={}); assert r.status_code == 400; assert ErrorResponse.model_validate(r.json())

@pytest.mark.llm
def test_memory_within_session():
    sid = uuid4(); post_turn("Remember the code word teal-42.", sid)
    assert "teal-42" in post_turn("What was the code word?", sid).json()["reply"]
@pytest.mark.llm
def test_sessions_isolated():  # new sid asked the same → reply lacks "teal-42"
@pytest.mark.llm
def test_uses_monarch_tool():
    r = post_turn("What accounts do I have?").json()
    assert "Webster" in r["reply"] and any(c["name"] == "GetAccounts" for c in r["tool_calls"])
@pytest.mark.llm
def test_monarch_auth_error():
    set_fake_mode("auth_error")
    try: r = post_turn("What accounts do I have?"); assert r.status_code == 502; assert r.json()["error"]["code"] == "monarch_auth"
    finally: set_fake_mode("ok")
```

Register the `llm` marker and skip LLM tests unless `-m llm` is passed.

- [ ] **Step 2: Echo unit test.** Two turns in one session give `echo: a (turn 1)` and `echo: b (turn 2)`. A new session starts again at turn 1.
- [ ] **Step 3: Run and expect failure.** `uv run pytest agents/echo -v`
- [ ] **Step 4: Implement the models, `format_user_message`, `fake_monarch`, and the echo app.** Echo maps FastAPI's `RequestValidationError` to 400 `ErrorResponse(code="internal")`. Put that handler in `turn_contract` as `install_error_handlers(app: FastAPI) -> None` so the deep agent reuses it, which makes `fastapi` a dependency of `turn_contract`.
- [ ] **Step 5: Run and expect a pass.** Run `uv run pytest agents/echo -v`. Then start echo (`uv run agent-echo`) and run `AGENT_URL=http://127.0.0.1:8201 uv run pytest contract/tests -v`: 3 pass, 4 skipped.
- [ ] **Step 6: Commit.** `feat: turn contract, fake Monarch, echo agent`

---

### Task 4: Gateway core (config, commands, store, splitting, router)

**Files:**
- Create: `gateway/pyproject.toml`, `gateway/src/gateway/{config,commands,store,formatting,router}.py`
- Test: `gateway/tests/test_commands.py`, `test_store.py`, `test_formatting.py`, `test_router.py`

**Interfaces:**
- Consumes: `turn_contract` models (Task 3)
- Produces:
  - `config.Settings` (frozen dataclass): `owner_number: str, owner_uuid: str | None, bot_number: str, signal_url: str, agents: dict[str, str], default_agent: str, db_path: Path, turn_timeout_s: float = 120, split_limit: int = 1500, tz: str = "America/New_York"`. `config.load_settings(env: Mapping[str, str]) -> Settings` reads `OWNER_NUMBER, OWNER_UUID, BOT_NUMBER, SIGNAL_URL, AGENTS` (format `name=url,name=url`), `DEFAULT_AGENT` and `DB_PATH`. It raises `ValueError` if `DEFAULT_AGENT` isn't in `AGENTS`.
  - `commands.Command(name: Literal["agent","new","which","help"], arg: str | None)`, `commands.parse_command(text: str) -> Command | None`. The text is stripped, and the match is case-insensitive on `/name` plus an optional single argument, lowercased. Any other `/…` text returns `None` and goes to the agent.
  - `store.Store(path: Path)`: `active_agent(owner: str) -> str | None`, `set_active_agent(owner, agent) -> None`, `current_session(owner, agent) -> str` (creates a uuid4 if missing), `rotate_session(owner, agent) -> str`, `log_turn(rec: TurnRecord) -> None`. `store.TurnRecord` dataclass fields: `session_id, agent, inbound, reply, started_at, finished_at, status: Literal["ok","timeout","error"], tool_calls: list[dict], input_tokens: int, output_tokens: int`.
  - `formatting.split_reply(text: str, limit: int = 1500) -> list[str]`
  - `agent_client` interface used by the router (implemented in Task 5): `AgentPort` protocol with `async turn(agent: str, req: TurnRequest) -> TurnResponse`, raising `AgentTimeout`, `AgentDown`, or `AgentError(code: str, message: str)`. Define the protocol and the exceptions in `gateway/agent_client.py` now.
  - `router.Inbound` dataclass `(sender_number: str | None, sender_uuid: str | None, text: str, timestamp_ms: int, is_group: bool)`, `router.is_owner(inb: Inbound, s: Settings) -> bool`, and `router.Router(settings, store, agents: AgentPort)` with `async handle(inb: Inbound) -> list[str]`. The router returns the messages to send, and an empty list means drop silently. It logs every agent turn with `store.log_turn`.

- [ ] **Step 1: Write failing tests.**

```python
# test_commands.py
@pytest.mark.parametrize("t,exp", [("/agent pi", Command("agent","pi")), ("  /Agent PI ", Command("agent","pi")),
    ("/new", Command("new",None)), ("/WHICH", Command("which",None)), ("/help", Command("help",None)),
    ("/new now", Command("new","now")), ("/spending", None), ("hello", None)])
def test_parse(t, exp): assert parse_command(t) == exp

# test_store.py
def test_session_stable_then_rotates(tmp_path): a = s.current_session("o","pi"); assert s.current_session("o","pi") == a; b = s.rotate_session("o","pi"); assert b != a and s.current_session("o","pi") == b
def test_sessions_per_agent(tmp_path): current_session("o","pi") != current_session("o","deep")
def test_persists_across_instances(tmp_path): set_active_agent then new Store(same path).active_agent == "deep"

# test_formatting.py
def test_split_short(): assert split_reply("hi") == ["hi"]
def test_split_on_paragraphs(): parts = split_reply("a"*1000 + "\n\n" + "b"*1000); assert parts == ["a"*1000, "b"*1000]
def test_split_falls_back_to_lines_then_hard(): parts = split_reply("x"*4000); assert all(len(p) <= 1500 for p in parts) and "".join(parts) == "x"*4000
def test_split_lines(): text = "\n".join(["line"*50]*20); assert all(len(p) <= 1500 for p in split_reply(text))
def test_split_empty(): assert split_reply("   ") == ["(no reply)"]

# test_router.py  (FakeAgents implementing AgentPort; Settings with agents {"pi","deep","echo"}, default "pi")
async def test_drops_non_owner(): assert await r.handle(inb(number="+15550000000")) == [] and fake.calls == []
async def test_drops_group(): ...is_group=True → []
async def test_owner_match_by_uuid(): number None, uuid == settings.owner_uuid → handled; uuid other → []
async def test_routes_to_default_agent_with_session_and_now(): fake.calls[0] == ("pi", req) where req.session_id == store.current_session(owner,"pi") and req.now.tzinfo key "America/New_York" and req.now.microsecond == 0
async def test_agent_command_switches(): handle("/agent deep") → one reply containing "deep"; next message goes to "deep"
async def test_unknown_agent_lists_choices(): reply contains "foo" not valid and lists "deep", "echo", "pi"; active agent unchanged
async def test_new_rotates_only_current_agent(): ...
async def test_which_shows_agent_session_model(): reply contains "pi", session id, and model from fake health  # AgentPort also needs async health(agent) -> Health
async def test_timeout_copy(): fake raises AgentTimeout → ["That took too long. Try again or /new."]; turn logged status "timeout"
async def test_down_copy(): AgentDown → ["The pi agent is down."]
async def test_monarch_auth_copy(): AgentError("monarch_auth", …) → ["Monarch login expired. Run `make monarch-login` on the Mac."]
async def test_long_reply_split(): fake reply 3000 chars → 2+ messages
async def test_turn_logged(): after ok turn, turns table has 1 row with status "ok", tool_calls and tokens copied
```

Add `async health(agent: str) -> Health` to the `AgentPort` protocol, for `/which`.

- [ ] **Step 2: Run and expect failure.** `uv run pytest gateway -v`
- [ ] **Step 3: Implement the modules.** Use stdlib `sqlite3` with three tables (`active_agent`, `sessions`, `turns`). `split_reply` greedily packs paragraphs (split on `\n\n`) up to `limit`. An oversize paragraph is packed by lines, and an oversize line is hard-cut. Strip each chunk and drop empty ones. `/help` copy is up to the builder, but it must list the 4 commands.
- [ ] **Step 4: Run and expect a pass.** `uv run pytest gateway -v`
- [ ] **Step 5: Commit.** `feat(gateway): commands, sessions, splitting, router`

---

### Task 5: Gateway I/O + Signal bring-up (echo end to end)

**Files:**
- Create: `gateway/src/gateway/{signal_client,app}.py`, and the implementation of `HttpAgentClient` in `gateway/src/gateway/agent_client.py`
- Create: `docker/python.Dockerfile` (build arg `PACKAGE`, `uv sync --package $PACKAGE --frozen`), `docker-compose.yml` (signal-cli, gateway, agent-echo, monarch-proxy), `docs/runbooks/signal-registration.md`
- Modify: `Makefile` (`up`, `down`, `logs`, `test`)
- Test: `gateway/tests/test_signal_client.py`, `gateway/tests/test_agent_client.py`, `gateway/tests/test_app.py`

**Interfaces:**
- Consumes: `Router`, `Inbound`, `Settings`, `AgentPort`/exceptions (Task 4), `TurnResponse`/`ErrorResponse`/`Health` (Task 3)
- Produces:
  - `signal_client.parse_envelope(msg: dict) -> Inbound | None` parses signal-cli-rest-api receive JSON (`{"envelope": {...}, "account": ...}`). It returns `Inbound` only for `envelope.dataMessage` with non-empty `message` text. `is_group` is true when `dataMessage.groupInfo` (or `groupV2`) is present.
  - `signal_client.SignalClient(base_url: str, bot_number: str)`: `receive() -> AsyncIterator[dict]` (websocket `ws://…/v1/receive/<bot_number>`, reconnecting with backoff of 1s doubling to 30s), `async send(recipient: str, text: str) -> None` (`POST /v2/send {"message","number","recipients":[recipient]}`), `async typing(recipient: str, on: bool) -> None` (`PUT` or `DELETE /v1/typing-indicator/<bot_number> {"recipient"}`). Typing failures are logged and swallowed.
  - `agent_client.HttpAgentClient(agents: dict[str,str], timeout_s: float)` implements `AgentPort`. `httpx.TimeoutException` raises `AgentTimeout`, a connect error raises `AgentDown`, an `ErrorResponse` body raises `AgentError(code, message)` (code `timeout` → `AgentTimeout`), and any other non-2xx raises `AgentDown`.
  - `app.Gateway(settings, signal: SignalClient-like, router: Router)` with `async run() -> None` and `async on_message(raw: dict) -> None`. Each owner gets its own `asyncio.Lock`. While a turn runs it turns typing on, then off in `finally`, and sends each reply in order. Messages from non-owners never trigger typing. `gateway.app:main` loads settings from env and runs.

- [ ] **Step 1: Write failing tests.**

```python
def test_parse_envelope_text(): parse_envelope(fixture("data_message.json")) == Inbound("+1617…", "uuid…", "hi", 1730…, False)
@pytest.mark.parametrize("name", ["receipt", "typing", "reaction", "sync_sent", "attachment_only", "edit"])
def test_parse_envelope_ignores_non_text(name): assert parse_envelope(fixture(f"{name}.json")) is None
def test_parse_envelope_group(): ...is_group is True

# test_agent_client.py (respx)
async def test_ok(): 200 TurnResponse → parsed
async def test_timeout(): respx side_effect httpx.ReadTimeout → AgentTimeout
async def test_down(): httpx.ConnectError → AgentDown
async def test_monarch_auth(): 502 {"error":{"code":"monarch_auth",…}} → AgentError with code "monarch_auth"
async def test_agent_timeout_code(): 504 {"error":{"code":"timeout"}} → AgentTimeout

# test_app.py (FakeSignal records send/typing calls; router with a slow fake agent)
async def test_messages_processed_serially(): two on_message concurrently; fake agent records start/end; second starts after first ends; replies sent in order
async def test_typing_on_then_off(): calls == [("typing", owner, True), ("send", owner, ...), ("typing", owner, False)]
async def test_typing_off_even_on_error(): router raises → typing False still sent
async def test_non_owner_no_typing(): no calls at all
```

Fixtures are captured JSON payloads under `gateway/tests/fixtures/`. Hand-write them from the signal-cli-rest-api docs, then replace them with real captures during Step 6.

- [ ] **Step 2: Run and expect failure.** `uv run pytest gateway -v`
- [ ] **Step 3: Implement `signal_client.py`, `HttpAgentClient`, and `app.py`.**
- [ ] **Step 4: Run and expect a pass.** `uv run pytest gateway -v`
- [ ] **Step 5: Write the compose file and runbook.** signal-cli runs `bbernhard/signal-cli-rest-api` with `MODE=json-rpc` and volume `./data/signal:/home/.local/share/signal-cli`, with no published ports. Registration happens through `docker compose run`, or a temporary `127.0.0.1:8080` publish documented in the runbook. The runbook covers: get a captcha token at `https://signalcaptchas.org/registration/generate.html`, `POST /v1/register/<BOT_NUMBER> {"captcha": "<token>"}` (use the token immediately, since it expires fast), receive the SMS on the Tello phone, `POST /v1/register/<BOT_NUMBER>/verify/<code>`, and set a profile name. Compose sets `DEFAULT_AGENT=echo` and `AGENTS=echo=http://agent-echo:8201` for now.
- [ ] **Step 6: Manual check (needs Keith).** Register the number using the runbook, then run `make up`. From the personal phone, text the bot `hi` and expect `echo: hi (turn 1)` with a typing indicator first. Text `/which` and get echo/session info back. Text from a different number (or a group) and get nothing back, with no log line beyond a debug drop. Capture the real envelopes to replace the test fixtures, then re-run the tests.
- [ ] **Step 7: Commit.** `feat(gateway): Signal I/O, compose, registration runbook`

---

### Task 6: agent-pi (TypeScript)

**Files:**
- Create: `prompts/system.md`
- Create: `agents/pi/{package.json,tsconfig.json,Dockerfile}`, `agents/pi/src/{contract.ts,monarchTools.ts,sessions.ts,server.ts}`
- Test: `agents/pi/test/{monarchTools.test.ts,server.test.ts}`
- Modify: `docker-compose.yml` (add `agent-pi`; `AGENTS` gains `pi=http://agent-pi:8202`; `DEFAULT_AGENT=pi`)

**Interfaces:**
- Consumes: the HTTP contract and `AUTH_ERROR_MARKER` text (Task 3, mirrored in TS), and the proxy at `MONARCH_MCP_URL` (default `http://monarch-proxy:8100/mcp`)
- Produces:
  - `contract.ts`: TS types mirroring `TurnRequest/TurnResponse/ToolCall/Usage/ErrorResponse/Health`, `AUTH_ERROR_MARKER`, and `formatUserMessage(text, now)` with output identical to the Python version.
  - `monarchTools.ts`: `loadMonarchTools(url: string, recorder: ToolRecorder) -> Promise<PiTool[]>`. It lists the proxy's tools over `@modelcontextprotocol/sdk` (`StreamableHTTPClientTransport`) and wraps each one as a Pi custom tool, passing the MCP JSON schema through. Each call records `{name,args,ms,ok}` and, if the result text contains `AUTH_ERROR_MARKER`, sets `recorder.authFailed = true`.
  - `sessions.ts`: `getSession(sessionId: string) -> Promise<PiAgentSession>`. It caches one Pi session per ID, persisted under `data/agents/pi/<sessionId>/` (`PI_DATA_DIR`), created with **no built-in tools**, only the Monarch tools, with the system prompt from `prompts/system.md` (`SYSTEM_PROMPT_PATH`) and the model `anthropic/$MODEL`. A per-session mutex serializes turns.
  - `server.ts`: an HTTP server on 8202 with `/health` → `{"name":"pi","framework":"pi-mono","model":MODEL}` and `/turn`, using the error mapping from Global Constraints and an internal 110s timeout.

- [ ] **Step 1: Write `prompts/system.md`** covering the spec's "Shared system prompt" bullets: a household finance helper for Keith; plain text only, with no markdown tables, headers or bold; short paragraphs; $ amounts; the current time comes from the `Current time:` line; the tools' `intent` must be a generic task category and never personal details; say so plainly when data is missing or truncated.
- [ ] **Step 2: Confirm the Pi SDK surface.** Run `npm view @earendil-works/pi-coding-agent version` and `npm view @mariozechner/pi-coding-agent version`, and use whichever is current. Read its SDK docs and note in `agents/pi/README.md` the exact options for: (a) creating a session with zero built-in tools, (b) registering custom tools with a JSON schema, (c) persisting and resuming a session from a directory, (d) choosing the Anthropic model, (e) reading token usage after a prompt. If (a) isn't possible, stop and report, because that's an anti-goal.
- [ ] **Step 3: Write failing tests (vitest).**

```ts
test("session has only Monarch tools", async () => {
  const s = await getSession(randomUUID()); expect(toolNames(s).sort()).toEqual(["GetAccounts","GetTransactions"]) }) // against fake-monarch
test("auth marker sets authFailed", ...)       // fake tool result containing MONARCH_AUTH_REQUIRED
test("records ms/ok per call", ...)
test("POST /turn {} → 400 internal", ...)
test("formatUserMessage matches python", () => expect(formatUserMessage("hi", new Date("2026-11-04T22:00:00-05:00"))).toBe("Current time: 2026-11-04T22:00:00-05:00\n\nhi"))
```

Note: the TS output has to preserve the offset exactly as Python's `isoformat()` prints it, so pass `now` through as the original string from the request rather than re-serializing it.
- [ ] **Step 4: Run and expect failure.** `cd agents/pi && npx vitest run`
- [ ] **Step 5: Implement.** If `recorder.authFailed` is set after a turn, return 502 `monarch_auth`.
- [ ] **Step 6: Run and expect a pass.** Run `npx vitest run`. Then run the contract suite with LLM tests against pi pointed at fake-monarch: `uv run fake-monarch --port 8190 &`, `MONARCH_MCP_URL=http://127.0.0.1:8190/mcp npm start &`, then `AGENT_URL=http://127.0.0.1:8202 FAKE_MONARCH_URL=http://127.0.0.1:8190 uv run pytest contract/tests -m "llm or not llm" -v`. All 7 should pass.
- [ ] **Step 7: Manual check (needs Keith).** Run `make up`, then text "What accounts do I have?" and get a plain-text answer with real data. Text "and which has the most?" and the agent should remember the context. `/new` then "what did I just ask?" should show the context is gone.
- [ ] **Step 8: Commit.** `feat(agent-pi): Pi agent with Monarch-only tools`

---

### Task 7: agent-deep (Python deepagents)

**Files:**
- Create: `agents/deep/pyproject.toml`, `agents/deep/src/agent_deep/{agent,extract,app}.py`
- Test: `agents/deep/tests/test_extract.py`, `agents/deep/tests/test_app.py`
- Modify: `docker-compose.yml` (add `agent-deep`; `AGENTS` gains `deep=http://agent-deep:8203`)

**Interfaces:**
- Consumes: `turn_contract` (models, `format_user_message`, `install_error_handlers`, `AUTH_ERROR_MARKER`), the proxy URL, and `prompts/system.md`
- Produces:
  - `agent.build_agent(*, mcp_url: str, model: str, checkpointer) -> tuple[CompiledGraph, ToolTimer]`. It loads tools with `MultiServerMCPClient({"monarch": {"transport": "streamable_http", "url": mcp_url}}).get_tools()` and wraps each tool so that a call appends `ToolCall(name, args, ms, ok)` to the `ToolTimer` list for the current turn (held in a contextvar). It then calls `create_deep_agent(model=init_chat_model(f"anthropic:{model}"), tools=wrapped, system_prompt=<prompts/system.md>, checkpointer=checkpointer)` and passes no filesystem backend.
  - `extract.usage_from(messages: list[BaseMessage]) -> Usage`, which sums `usage_metadata` over the AI messages produced in this turn. `extract.auth_failed(messages) -> bool` is true if any ToolMessage from this turn contains `AUTH_ERROR_MARKER`.
  - `app`: FastAPI on 8203. Its lifespan opens an `AsyncSqliteSaver` at `data/agents/deep/checkpoints.db`, and turns are serialized per session with a lock. `/turn` calls `graph.ainvoke({"messages":[HumanMessage(format_user_message(...))]}, config={"configurable":{"thread_id": session_id}})` under `asyncio.wait_for(…, 110)`. `/health` returns `{"name":"deep","framework":"deepagents","model":MODEL}`.

- [ ] **Step 1: Write failing tests.**

```python
def test_usage_sums_only_new_messages(): ...
def test_auth_failed_detects_marker(): [ToolMessage(content="MONARCH_AUTH_REQUIRED: …", tool_call_id="1")] → True
async def test_tool_timer_records_calls(): wrapped fake tool invoked → timer list has ToolCall(name, args, ms>=0, ok=True); raising tool → ok=False
async def test_turn_timeout_maps_504(monkeypatch): graph.ainvoke sleeps; patch timeout to 0.01 → 504 code "timeout"
def test_malformed_400(): ...
```

To find "messages produced in this turn", record `len(state["messages"])` before the call and slice afterwards (read the state with `graph.aget_state(config)`).
- [ ] **Step 2: Run and expect failure.** `uv run pytest agents/deep -v`
- [ ] **Step 3: Implement.** Pin `deepagents`, `langchain-mcp-adapters>=0.3,<0.4`, `langchain-anthropic` and `langgraph-checkpoint-sqlite` to the versions current at implementation time, and record them in `uv.lock`.
- [ ] **Step 4: Run and expect a pass.** Run `uv run pytest agents/deep -v`, then the full contract suite against deep on 8203 pointed at fake-monarch. All 7 should pass.
- [ ] **Step 5: Manual check (needs Keith).** Run `make up`, text `/agent deep`, then the same three-message script as Task 6, Step 7. Then `/agent pi` should resume the earlier Pi conversation.
- [ ] **Step 6: Commit.** `feat(agent-deep): deepagents agent with SQLite checkpointer`

---

### Task 8: Acceptance + README

**Files:**
- Create: `README.md` (what it is, setup order: `.env` → `make monarch-login` → registration runbook → `make up`; commands; where the logs are), `docs/runbooks/manual-checks.md`
- Modify: `Makefile` (`contract` target that runs the suite against echo, pi and deep with fake-monarch)

- [ ] **Step 1: Write `make contract`.** It starts fake-monarch and the three agents locally against it, runs the suite (including `llm`) for each, and tears them down. Expect 3 + 7 + 7 passes, with echo's 4 LLM tests skipped.
- [ ] **Step 2: Write `manual-checks.md`.** This is the scripted Signal session from Tasks 5–7 plus: send two messages quickly (expect the answers in order); send a reaction (no reply); have someone else text the bot (no reply); confirm `data/gateway.db` `turns` rows and `monarch-calls.jsonl` lines match the session.
- [ ] **Step 3: Run `make test` and `make contract`.** Expect everything green.
- [ ] **Step 4: Manual (needs Keith).** Run through `manual-checks.md` once.
- [ ] **Step 5: Commit.** `docs: README and acceptance checks`
