# Signal registration (Monty bot number)

One-time setup for the bot's Signal number (`BOT_NUMBER`, the Tello SIM). Do this before `mise run up`.

1. Fill in `.env` (copy `.env.example`): `OWNER_NUMBER` (your personal number) and `BOT_NUMBER`,
   both E.164 like `+16175550100`.
2. Start only signal-cli with a temporary loopback publish. Create `docker-compose.override.yml`
   (gitignore it or delete it afterwards):

   ```yaml
   services:
     signal-cli:
       ports:
         - "127.0.0.1:8080:8080"
   ```

   Then: `mkdir -p data/signal && docker compose up -d signal-cli`.
3. Get a captcha token at <https://signalcaptchas.org/registration/generate.html>. Solve it,
   copy the `signalcaptcha://...` link target. It expires fast, so use it immediately:

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
     -H 'Content-Type: application/json' -d '{"name": "Monty"}'
   ```
6. Remove the override file, then `docker compose down && mise run up`.
7. From your personal phone text the bot `hi`. Expect a typing indicator, then
   `echo: hi (turn 1)`. `/which` shows agent, session and model. Messages from other numbers
   or groups get no reply.

Registration state lives in `data/signal/` (gitignored). Back it up; losing it means
re-registering.
