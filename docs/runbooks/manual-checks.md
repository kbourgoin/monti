# Manual acceptance checks

Run once after `mise run up` (see `README.md` for setup). Use your own phone (Keith) unless a step
says otherwise; section 7 also needs a second person in `USERS` (Alex below) and someone who isn't
(the outsider). Run `mise run test` and `mise run contract` first.

## 1. Basics (echo)

1. Send `/agent echo`, then `hi`. Expect a typing indicator, then `echo (Keith): hi (turn 1)`.
2. Send `/which`. Expect agent `echo`, a session id, and a model line.
3. Send `/new`, then `hi`. Expect `Started a new session with echo.` then `echo (Keith): hi (turn 1)`.
4. Send `/help`. Expect the four commands. Send `/agent nope`. Expect an error listing valid agents.

## 2. Pi

1. Send `/agent pi`, then `What is my net worth?`. Expect a typing indicator and a reply that
   matches the Monarch app.
2. Ask `How much did I spend on groceries last month?`, then `And the month before?`. The follow-up
   should rely on the first answer (session memory).
3. Send `/new` and ask `And the month before?`. Expect it not to know what you mean.

## 3. Deep

1. Send `/agent deep` and repeat the questions from section 2. Expect similar answers.
2. Ask a multi-step question (for example `Compare my spending this month with last month by category`).

## 4. Ordering, reactions, other senders

1. Send two messages quickly (for example `/agent echo`, then `one` and `two` back to back). Expect
   both answers, in order.
2. React to one of the bot's messages with an emoji. Expect no reply.
3. Have someone not in `USERS` text the bot's number. Expect no reply.

## 5. Failure copy

1. `docker compose stop agent-pi`, switch to `/agent pi`, and send a message. Expect
   `The pi agent is down.` Then `docker compose start agent-pi`.
2. To check the expired-login copy: move `data/monarch/tokens.json` aside, `docker compose restart
   monarch-proxy`, and ask Pi for something. Expect ``Monarch login expired. Run `mise run monarch-login` on the Mac.``
   Restore the file and restart the proxy, or run `mise run monarch-login`.
3. To check that a re-login takes effect without a proxy restart: `docker compose stop monarch-proxy`,
   back up `data/monarch/tokens.json`, replace its `access_token` and `refresh_token` values with
   junk, and `docker compose start monarch-proxy`. Ask Pi for something and expect the expired-login
   copy. Run `mise run monarch-login`, then, WITHOUT restarting the proxy, ask again. Expect a real answer.

## 6. Records match the session

1. Turns: `sqlite3 data/gateway/gateway.db "select id, agent, inbound, status, input_tokens, output_tokens from turns"`.
   Expect one row per question you sent in sections 1 to 5 and 7 (commands, reactions and ignored
   senders write none). `select chat_id, sender_name, inbound from turns` shows `dm:<number>` or
   `group:<id>` and who asked.
2. Monarch calls: `wc -l data/logs/monarch-calls.jsonl` and `tail -n 5 data/logs/monarch-calls.jsonl`.
   Expect one line per Monarch tool call from the Pi and deep questions, with only allowlisted tool
   names and no result bodies.
3. Agent state: `ls data/agents/pi data/agents/deep`.

## 7. Users, chats and groups

Use `/agent echo` in each chat first, so the turn counter shows which conversation is which.

1. **Two DMs.** Keith DMs `hi` twice; Alex DMs `hi`. Keith sees turns 1 and 2, Alex sees turn 1:
   separate conversations.
2. **Two Keith-and-Monty groups.** Make two groups with only Keith and Monty (see
   `signal-registration.md`, "Adding Monty to a group"). Send `hi` in each. Each reply is in its own
   group and each says turn 1; your DM is unaffected.
3. **Both people in one group.** Make a group with Keith, Alex and Monty. Switch it to `/agent pi`
   and have each person ask something. Monty answers in the group and addresses each person by name.
4. **A group with an outsider.** Make a group with Keith, the outsider and Monty, and have Keith send
   `hi`. Expect silence (no typing indicator either) and a gateway log line saying the group has a
   member not in `USERS`. Then remove the outsider and, after up to a minute, send `hi` again: Monty
   answers. Add the outsider back (or just invite them): Monty goes silent again.
5. **Commands are per chat.** In one of the step 2 groups send `/agent deep`, then `/which` there
   (deep) and `/which` in the other group and in your DM (unchanged).
6. **Two chats at once.** With `/agent pi`, ask a slow question in one group (for example the
   multi-step one from section 3), and while Monty is typing there send `/which` in your DM. The DM
   answer arrives without waiting for the group.
7. **Order within a chat.** In one group send `one` then `two` quickly (echo). Answers come in order.
8. **Invites.** If Signal only invited Monty in any step above, confirm:
   - in an all-`USERS` group, Keith's invite is accepted automatically (log line
     `accepted group invite`);
   - in a group with the outsider in it, the invite stays pending even when Keith (not the
     outsider) invites Monty or renames the group (log line `left pending`); Monty never joins
     or speaks there;
   - after Monty is removed from a group, a later change there doesn't make it rejoin.

### signal-cli REST assumptions to confirm

The gateway's group support was written from the bbernhard/signal-cli-rest-api source and
swagger (`src/client/client.go`, `src/api/api.go`, `src/docs/swagger.json`) and the signal-cli
man page, not against a live server. While doing section 7, confirm:

- **Group ids.** Receive frames carry `dataMessage.groupInfo.groupId` (signal-cli's internal
  base64 id); the gateway's chat id is `group:<that id>`. The REST API addresses the same group as
  `group.` + base64(internal id), which is also the `id` field of the group list.
- **Group list.** `GET /v1/groups/<bot>?expand=true` returns entries with `internal_id`,
  `member` (true once the bot is a full member) and `members` / `pending_invites` /
  `pending_requests` as `{"number": ..., "uuid": ...}` objects (number empty when unknown). Without
  `expand` they're plain strings (a number, else a uuid); the gateway accepts both. Check with
  `curl "http://127.0.0.1:8080/v1/groups/$BOT_NUMBER?expand=true"` (loopback publish as in the
  registration runbook) that the bot itself appears under its number; if only by uuid, set
  `BOT_UUID`.
- **Group send.** `POST /v2/send` with `"recipients": ["group.<...>"]` posts to the group.
- **Group typing.** `PUT`/`DELETE /v1/typing-indicator/<bot>` with `{"recipient": "group.<...>"}`
  shows and hides the indicator in the group.
- **Invites.** signal-cli doesn't accept invites by itself (`updateGroup -g` accepts one, which is
  what `POST /v1/groups/<bot>/<group.id>/join` runs). The gateway assumes an invite reaches the bot
  as a `groupInfo.type: "UPDATE"` data message with no text, sent by the inviter. If no such frame
  arrives, step 8 fails: accept by hand as in the registration runbook and tell the builder.
- **Group listing `member` field.** If the group listing has no `member` field, every group logs
  "bot is not a full member" and Monty stays silent in all groups; report it to the builder.
- **Profile sharing.** DMing the bot before adding it lets Signal add it directly rather than
  invite it. If adding always produces an invite, that's fine as long as step 8 passes.
