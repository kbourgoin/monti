# Monti Chats & mise Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several people use Monti in DMs and groups, each chat its own conversation, and the repo uses mise instead of make.

**Architecture:** The gateway identifies a *chat* (`dm:<E164>` or `group:<id>`) for every inbound message, admits it per the `USERS` list and group membership (fail-closed), and keys active-agent and session state by chat. Per-chat queues and workers give in-order handling within a chat and concurrency across chats. The turn contract gains `sender_name`, rendered as a `From:` line by every agent.

**Tech Stack:** As on `feat/signal-monarch-agent`: Python uv workspace, TypeScript Pi agent, Docker Compose, signal-cli-rest-api. New: mise.

**Spec:** `docs/superpowers/specs/2026-11-05-monty-chats-and-mise-design.md` (amends `docs/superpowers/specs/2026-11-04-signal-monarch-agent-design.md`)

## Global Constraints

- Everything in the v1 plan's Global Constraints still holds, except as changed below (`docs/superpowers/plans/2026-11-04-signal-monarch-agent.md`).
- Expired-login copy, verbatim: ``Monarch login expired. Run `mise run monarch-login` on the Mac.``
- Proxy auth marker text: `MONARCH_AUTH_REQUIRED: run mise run monarch-login` (the marker itself is still `MONARCH_AUTH_REQUIRED`).
- `USERS` format: comma-separated `Name=+E164[/uuid]`. `OWNER_NUMBER` and `OWNER_UUID` are removed.
- Chat IDs: `dm:<E164>` and `group:<signal group id>`.
- Group membership cache TTL: 60s. A lookup failure means the message is dropped (fail-closed).
- `format_user_message` output: `"Current time: <iso>\nFrom: <sender_name>\n\n<text>"`.
- `mise.toml` tools: `node = "22"`, `python = "3.13"`, `uv = "latest"`. Tasks: `monarch-login`, `test`, `contract`, `up`, `down`, `logs`. `Makefile` is deleted.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A group where a non-user is present: Monti is silent even when a user writes. Tested in Task 4 (`test_group_with_outsider_silent`).
2. The group member lookup fails or times out: Monti is silent and the failure is logged. Tested in Task 4 (`test_group_lookup_failure_silent`).
3. A slow turn in chat A doesn't delay chat B, and two quick messages in chat A are answered in order. Tested in Task 4 (`test_chats_concurrent_messages_ordered`).
4. `/agent deep` in group G1 doesn't change the agent in group G2 or in the DM. Tested in Task 3 (`test_commands_are_per_chat`).
5. The sender has a UUID but no number, in a group. Matched by UUID and admitted only if every member is a user. Tested in Task 4 (`test_group_uuid_only_sender`).

---

### Task 1: mise replaces make

**Files:** Create `mise.toml`. Delete `Makefile`. Modify `scripts/contract.sh` (drop the hand-rolled Node version check if mise guarantees Node; keep a check that prints a mise hint), `gateway/src/gateway/router.py` (login copy), `monarch-proxy/src/monarch_proxy/server.py` (marker text), the tests that assert those strings, `README.md`, `docs/runbooks/*.md`, `agents/pi/README.md`, and any other user-facing `make` mention (`git grep -n "make "` to find them; ignore `docs/superpowers/`).

- [ ] **Step 1:** Update the copy assertions in the tests to the new strings and run them: `uv run pytest gateway monarch-proxy -q` should FAIL on the copy.
- [ ] **Step 2:** Change the copy in `router.py` and `server.py`, then run the tests again. They should PASS.
- [ ] **Step 3:** Write `mise.toml` per Global Constraints. Its tasks run the same commands as the old Makefile targets, and `up` also creates every `data/` subdirectory compose mounts. Delete `Makefile`.
- [ ] **Step 4:** Verify with `mise install`, then `mise run test` (green), then `mise run contract` (echo 4+4 skipped, pi 8, deep 8; pi now on the mise Node with no scratch Node). `git grep -nE "\bmake (monarch|up|down|test|contract|logs)"` outside `docs/superpowers/` should print nothing.
- [ ] **Step 5:** Commit `build: switch from make to mise`.

---

### Task 2: Contract `sender_name` + agents + prompt

**Files:** `contract/src/turn_contract/{models,messages}.py`, `contract/tests/test_contract.py`, `agents/echo/src/agent_echo/app.py` + tests, `agents/pi/src/{contract,sessions,server}.ts` + tests, `agents/deep/src/agent_deep/app.py` + tests, `prompts/system.md`.

**Interfaces produced:** `TurnRequest.sender_name: str` (required). `format_user_message(text: str, now: datetime, sender_name: str) -> str`. TS `formatUserMessage(text: string, now: string, senderName: string): string` produces identical output. Echo replies `echo (<sender_name>): <text> (turn N)`.

- [ ] **Step 1: Failing tests.**
  - `test_format_user_message_includes_from`: the exact string per Global Constraints.
  - Contract `test_agent_sees_sender`, unmarked like `test_basic_turn`: post "What is my name? Reply with just the name." with `sender_name="Zed"` and assert `"Zed" in reply`.
  - Contract `test_malformed_request` also covers a request missing `sender_name` (400).
  - Pi vitest: `formatUserMessage` parity, including the From line.
  - Echo unit test: the new reply format.
- [ ] **Step 2:** Run them and expect FAIL: `uv run pytest contract agents/echo -q` and `cd agents/pi && npx vitest run` (through mise's Node).
- [ ] **Step 3:** Implement across the model, the formatter, echo, pi and deep. Every agent must call the formatter with `sender_name`.
- [ ] **Step 4:** Rewrite `prompts/system.md` per the spec's System prompt section. "Monti" appears once, on line 1, and no person's name is hard-coded.
- [ ] **Step 5:** Run them again and expect PASS, then `mise run contract` (now echo 5 + 4 skipped, pi 9, deep 9).
- [ ] **Step 6:** Commit `feat(contract): sender_name / From line across agents; household prompt`.

---

### Task 3: Gateway users, chat identity and per-chat state (DMs)

**Files:** `gateway/src/gateway/{config,router,store}.py`, and `signal_client.py` (`Inbound` gains `group_id`) + tests. Compose (`USERS` replaces the owner vars in the gateway environment), `.env.example`.

**Interfaces produced:**
- `config.User(name: str, number: str, uuid: str | None)`. `Settings.users: tuple[User, ...]` replaces `owner_number`/`owner_uuid`. `load_settings` parses and validates `USERS` (`ValueError` on empty, duplicate names, duplicate numbers, or a malformed entry).
- `Inbound` gains `group_id: str | None`. `is_group` becomes `group_id is not None`.
- `router.match_user(inb, settings) -> User | None`. `router.chat_id(inb, user) -> str` gives `dm:<user.number>` or `group:<group_id>`.
- `Store`: every `owner` parameter becomes `chat_id`. `TurnRecord` gains `chat_id: str` and `sender_name: str`.
- `Router.handle(inb) -> Reply | None`, where `Reply(chat_id: str, recipient: Recipient, messages: list[str])` and `Recipient = DirectRecipient(number) | GroupRecipient(group_id)`. Group admission arrives in Task 4: in this task the router admits a group only if a `GroupGate` port it receives says yes. The port is `async allowed(group_id: str) -> bool`, and Task 3 tests use a fake.

- [ ] **Step 1: Failing tests.**
  - `test_users_parse_and_validate`: valid entry with and without uuid; empty value; duplicate name; duplicate number; malformed entry.
  - `test_dm_from_each_user_is_separate_chat`: Keith and Alex DMs get different session ids and both are logged with sender_name.
  - `test_dm_from_stranger_dropped`.
  - `test_match_by_uuid_when_number_missing`.
  - `test_commands_are_per_chat`: `/agent deep` in group G1 (the fake gate allows it) leaves G2 and the DM on pi, and `/new` in G1 rotates only G1.
  - `test_turn_request_has_sender_name`.
- [ ] **Step 2:** Run and expect FAIL: `uv run pytest gateway -q`.
- [ ] **Step 3:** Implement. Remove the owner concept entirely.
- [ ] **Step 4:** Run and expect PASS, with the full suite green.
- [ ] **Step 5:** Commit `feat(gateway): USERS and per-chat state`.

---

### Task 4: Groups, membership gate and per-chat concurrency

**Files:** `gateway/src/gateway/{signal_client,groups,app}.py` (new `groups.py`), tests and fixtures, `docs/runbooks/signal-registration.md` (adding Monti to a group, invites), `docs/runbooks/manual-checks.md` (the spec's new checks), `README.md` (USERS, groups, security rule).

**Interfaces produced:**
- `SignalClient.list_groups() -> list[GroupInfo]`, where `GroupInfo(id: str, members: list[MemberRef])` and `MemberRef(number: str | None, uuid: str | None)`.
- `SignalClient.send` and `typing` accept `Recipient`. Group sends address the group in whatever form the REST API needs; record the assumption.
- `groups.MembershipGate(signal, settings, ttl_s=60)` implements `GroupGate`: true only when every member other than the bot matches a user. Exceptions or timeouts give false and a log line.
- `app.Gateway`: the receive loop hands each admitted message to a per-chat queue whose worker runs typing, the router and sends. Workers are created on demand.
- Invite handling per the spec, if signal-cli needs an explicit accept.

- [ ] **Step 1: Failing tests.**
  - `test_parse_envelope_group_id`, from the group fixture.
  - `test_group_all_users_admitted`.
  - `test_group_with_outsider_silent`.
  - `test_group_lookup_failure_silent`, where the lookup raises or times out.
  - `test_group_membership_cached_60s`, checking the fetch count with a fake clock.
  - `test_group_uuid_only_sender`.
  - `test_group_reply_and_typing_go_to_group`.
  - `test_chats_concurrent_messages_ordered`: a slow fake agent; chat B finishes before chat A's slow turn, and A1 and A2 replies come in order.
  - `test_receive_loop_not_blocked_by_turn`.
- [ ] **Step 2:** Run and expect FAIL: `uv run pytest gateway -q`.
- [ ] **Step 3:** Implement. Use the signal-cli-rest-api docs for the group list, group send and typing shapes, and write the fixtures from them, noting the assumptions in the report and in `manual-checks.md`.
- [ ] **Step 4:** Run and expect PASS. Then the full suite, `mise run contract`, `docker compose config` and `docker compose build gateway`.
- [ ] **Step 5:** Update the docs (README, both runbooks).
- [ ] **Step 6:** Commit `feat(gateway): group chats with fail-closed membership gate; per-chat workers`.
