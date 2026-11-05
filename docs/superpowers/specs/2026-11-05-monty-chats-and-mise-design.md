# Monty — Shared Use, Chats and mise (design amendment)

Date: 2026-11-05
Status: Draft, awaiting approval
Amends: `2026-11-04-signal-monarch-agent-design.md` (the "v1 design"). Anything
not mentioned here stays as the v1 design and the code on
`feat/signal-monarch-agent` have it.

## Intent

Keith and his partner both want to use Monty, in direct messages and in Signal
groups. The unit of conversation becomes **the chat**, not the person: each DM
with Monty and each group Monty is in is its own conversation with its own
agent and memory. Keith often starts several groups with just himself and Monty,
using them like separate threads.

Separately, the repo's task runner moves from make to **mise**, which also pins
tool versions (fixing the Node-too-old problem for Pi).

## Goals

- Several named people (from config) can use Monty in DMs.
- Monty works in Signal groups and replies to **every** message in an allowed
  group, with no mention or prefix needed.
- Each chat has its own active agent, session and `/new`/`/which`.
- The agent always knows who sent each message.
- Messages in different chats are handled concurrently. Messages within one chat
  are handled one at a time, in arrival order.
- `mise run <task>` replaces every `make <target>`, and `mise.toml` pins Node, Python and uv.

## Non-goals

- Replying only when mentioned. Every allowed message goes to the agent.
- Separate Monarch logins or per-person data. There's one household Monarch login,
  and everyone on the list sees all of it.
- Per-person permissions. Everyone on the list can use every command, including `/agent`.
- Everything else that was a non-goal in v1 still is, except multi-user and group chats.

## Anti-goals (replace the v1 "anyone other than me" anti-goal)

- **Someone not on the list gets a response.** A DM from an unknown number or UUID is dropped silently.
- **Monty speaks in a group that includes anyone not on the list**, because its
  replies would show household finances to that person. Monty stays silent in such
  a group, even when someone on the list writes there. Membership is checked
  **fail-closed**: if the member list can't be fetched, Monty stays silent.
- The other v1 anti-goals stay unchanged: no host access for agents, no Monarch
  writes, and secrets and financial data stay on local disk.

## Users

- New env var `USERS`, a comma-separated list of `Name=+E164` entries, each with
  an optional `/uuid`. Example: `USERS="Keith=+15551110000,Alex=+15552220000/1b2c…"`.
- It replaces `OWNER_NUMBER` and `OWNER_UUID`, which are removed rather than kept as aliases.
- Names and numbers must be unique, and there must be at least one entry. The gateway
  refuses to start with a clear error if either rule is broken.
- A sender matches a user by number, or by UUID when the envelope has no number.

## Chats

- **Chat ID:** `dm:<E164 of the user>` for a direct message, and `group:<signal group id>` for a group.
- **Admission rule:**
  - DM: the sender must match a user.
  - Group: the sender must match a user, *and* every member of the group other
    than the bot must match a user.
- **Group members** come from signal-cli (the REST API's group listing for the bot
  number), cached per group for 60s. Any lookup failure means drop and log.
- **Joining groups:** the builder confirms how signal-cli handles group invites. If
  a pending invite needs an explicit accept, Monty accepts only invites from someone
  on the list. If signal-cli joins automatically, the admission rule above still
  keeps Monty silent wherever it shouldn't speak. The runbook documents how to add
  Monty to a group.
- **Replies and typing indicators** go to the chat: the person for a DM, the group for a group.
- **Commands** (`/agent`, `/new`, `/which`, `/help`) work in any allowed chat and
  apply to that chat. In a group, whoever sends the command changes it for everyone in that group.

## Gateway state (SQLite)

- `active_agent`: chat_id → agent.
- `sessions`: (chat_id, agent) → session_id.
- `turns` gains `chat_id` and `sender_name` columns.
- No migration is needed: nothing real has run yet (Keith clears `data/` before the
  first run). The schema is created fresh.

## Concurrency

- The receive loop must never block on a turn. Each message goes to its chat's
  queue, and one worker per chat processes the queue in order. Workers are created
  when needed and may exit when idle.
- Two chats never wait on each other. Two messages in one chat never run at the same time.

## Turn contract change

- `TurnRequest` gains a required `sender_name: str`.
- `format_user_message(text, now, sender_name)` now returns
  `"Current time: <iso>\nFrom: <sender_name>\n\n<text>"`.
- The Pi agent's TypeScript mirror and the deepagents agent are both updated, and
  the contract suite checks the agent can see the sender. The test asks "What is my
  name?" with sender "Zed" and checks for "Zed" in the reply.
- Agents still own memory per `session_id`. Since each group chat is one session,
  the agent sees everyone's messages in that group, each with its own `From:` line.

## System prompt

`prompts/system.md` is rewritten for a household. "Monty" still appears exactly
once, on the first line. The prompt also says:
- Several household members may write, sometimes together in a group. Each
  message's `From:` line says who is asking, and Monty answers that person,
  using their name when it's natural.
- The Monarch data is the household's shared data.
- Everything else in the v1 prompt stays: plain text, $ amounts, the time line,
  generic `intent`, and honesty about gaps.

## mise

- `mise.toml` has `[tools]` with `node = "22"` (≥22.19), `python = "3.13"` and
  `uv = "latest"`, plus `[tasks]` replacing every Makefile target: `monarch-login`,
  `test`, `contract`, `up`, `down` and `logs`.
- `Makefile` is deleted.
- Every user-facing mention of make changes, including the expired-login copy:
  ``Monarch login expired. Run `mise run monarch-login` on the Mac.`` and the
  proxy's marker text `MONARCH_AUTH_REQUIRED: run mise run monarch-login`.

## Docs

- README: `USERS`, mise setup (`mise install`), groups, and the security rule for groups.
- Registration runbook: how to add Monty to a group.
- manual-checks.md: add these checks:
  - a DM from Keith and one from the partner (separate conversations)
  - two Keith-and-Monty groups (separate conversations)
  - a group with both people (Monty answers each by name)
  - a group with an outsider (silence)
  - commands in a group apply to that group only
  - two chats at once (no waiting)

## Testing

- Gateway unit tests cover:
  - `USERS` parsing and validation
  - matching by number or UUID
  - DM and group admission, including the outsider and lookup-failure cases
  - per-chat state and commands
  - group fan-out of replies and typing
  - ordering within a chat and concurrency across chats, with fakes
- Contract suite: the `sender_name` test runs against echo, pi and deep. Echo
  passes it by including `From: <name>` in its echo.

## Left to the builder

- The exact signal-cli REST calls for group listing, sending to a group, group
  typing indicators and accepting invites. Record any assumptions in fixtures and
  in the manual checklist.
- How workers and queues are structured, within the concurrency rules above.
