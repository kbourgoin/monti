# Signal registration (Monti bot number)

One-time setup for the bot's Signal number (`BOT_NUMBER`, the Tello SIM). Do this before `mise run up`.

1. Fill in `.env` (copy `.env.example`): `USERS` (everyone Monti answers, as
   `Name=+E164[/uuid]`, comma-separated; see `README.md`) and `BOT_NUMBER`, numbers in E.164
   like `+16175550100`.
2. Start only signal-cli with a temporary loopback publish. Create `docker-compose.override.yml`
   (gitignore it or delete it afterwards):

   ```yaml
   services:
     signal-cli:
       ports:
         - "127.0.0.1:8080:8080"
   ```

   Then: `mkdir -p data/signal && docker compose up -d signal-cli`.

   The commands below use `$BOT_NUMBER` from your shell, which `.env` doesn't set. Set it first,
   or every request returns `404 page not found`:

   ```sh
   export BOT_NUMBER=+16175550100   # the Tello number
   curl http://127.0.0.1:8080/v1/about   # should return JSON, not a 404
   ```
3. Get a captcha token at <https://signalcaptchas.org/registration/generate.html>. Solve it,
   copy the `signalcaptcha://...` link target. It expires fast, so use it immediately.
   The page tries to open Signal instead of showing the token: right-click the "Open Signal"
   link and choose Copy Link Address. If it redirects before you can, open the browser's
   DevTools console (Cmd+Opt+J in Chrome) *before* solving. Chrome logs
   `Launched external handler for 'signalcaptcha://…'`; copy the token from that line.

   ```sh
   curl -X POST http://127.0.0.1:8080/v1/register/$BOT_NUMBER \
     -H 'Content-Type: application/json' \
     -d '{"captcha": "<token without the signalcaptcha:// prefix>"}'
   ```
4. Signal sends an SMS code to the Tello phone. Verify:

   ```sh
   curl -X POST http://127.0.0.1:8080/v1/register/$BOT_NUMBER/verify/<code>
   ```
5. Set the profile name:

   ```sh
   curl -X PUT http://127.0.0.1:8080/v1/profiles/$BOT_NUMBER \
     -H 'Content-Type: application/json' -d '{"name": "Monti"}'
   ```
6. Remove the override file, then `docker compose down && mise run up`.
7. Set the profile picture: `mise run set-avatar` (or `mise run set-avatar path/to/image.png`).
7. From your personal phone text the bot `hi`. Expect a typing indicator, then
   `echo (Keith): hi (turn 1)` (with `/agent echo`). `/which` shows agent, session and model. Messages
   from numbers not in `USERS` get no reply.

Registration state lives in `data/signal/` (gitignored). Back it up; losing it means
re-registering.

## Adding Monti to a group

Monti answers in a group only when every member other than Monti is in `USERS` (see the group
security rule in `README.md`). To add it:

1. DM the bot first from the phone that will add it (any message, for example `/which`). Monti's
   reply should share its profile with you, which usually lets Signal add it to a group
   directly instead of only inviting it.
2. In Signal, create a group (or open an existing one) and add the bot's number as a member.
   A group of just you and Monti is fine: it's a separate conversation from your DM.
3. Send a message in the group. Expect a typing indicator and a reply.

If Signal could only *invite* the bot (it shows as "invited"/pending), the gateway accepts the
invite on its own, but only when all of these hold: the group change was sent by someone in
`USERS`, the bot is listed as invited, and everyone else in the group (members and anyone else
invited or asking to join) is in `USERS`. Otherwise the invite stays pending. signal-cli doesn't
record who sent an invite, so this stands in for "invited by a user": Monti never joins a group
that has an outsider in it, whoever invited it. Group changes reach the bot as signal-cli
"UPDATE" messages; the gateway then re-reads the group list and calls the join endpoint.

If the bot still shows as pending after a minute, accept by hand (temporary loopback publish as in
step 2 above):

```sh
# Find the group: look for "member": false and the bot under "pending_invites".
curl "http://127.0.0.1:8080/v1/groups/$BOT_NUMBER?expand=true"
# Accept, using that group's "id" (the one that starts with "group."):
curl -X POST "http://127.0.0.1:8080/v1/groups/$BOT_NUMBER/<group.id>/join"
```

If Monti stays silent in a group, check `mise run logs` for the gateway's reason: a member not in
`USERS`, an outsider invited, the bot not yet a full member, or a failed member lookup.

Optional `BOT_UUID`: set it if signal-cli ever lists the bot in a group by uuid only (the gateway
would otherwise count that entry as an outsider and stay silent). The bot's uuid is the `uuid`
of its entry in `data/signal/data/accounts.json`.
