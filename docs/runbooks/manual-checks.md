# Manual acceptance checks

Run once after `make up` (see `README.md` for setup). Use your owner phone unless a step says
otherwise. Run `make test` and `make contract` first.

## 1. Basics (echo)

1. Send `/agent echo`, then `hi`. Expect a typing indicator, then `echo: hi (turn 1)`.
2. Send `/which`. Expect agent `echo`, a session id, and a model line.
3. Send `/new`, then `hi`. Expect `Started a new session with echo.` then `echo: hi (turn 1)`.
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
3. Have someone else text the bot's number (or add the bot to a group and message it). Expect no reply.

## 5. Failure copy

1. `docker compose stop agent-pi`, switch to `/agent pi`, and send a message. Expect
   `The pi agent is down.` Then `docker compose start agent-pi`.
2. To check the expired-login copy: move `data/monarch/tokens.json` aside, `docker compose restart
   monarch-proxy`, and ask Pi for something. Expect ``Monarch login expired. Run `make monarch-login` on the Mac.``
   Restore the file and restart the proxy, or run `make monarch-login`.
3. To check that a re-login takes effect without a proxy restart: `docker compose stop monarch-proxy`,
   back up `data/monarch/tokens.json`, replace its `access_token` and `refresh_token` values with
   junk, and `docker compose start monarch-proxy`. Ask Pi for something and expect the expired-login
   copy. Run `make monarch-login`, then, WITHOUT restarting the proxy, ask again. Expect a real answer.

## 6. Records match the session

1. Turns: `sqlite3 data/gateway.db "select id, agent, inbound, status, input_tokens, output_tokens from turns"`.
   Expect one row per question you sent in sections 1 to 5 (commands, reactions and ignored senders
   write none).
2. Monarch calls: `wc -l data/logs/monarch-calls.jsonl` and `tail -n 5 data/logs/monarch-calls.jsonl`.
   Expect one line per Monarch tool call from the Pi and deep questions, with only allowlisted tool
   names and no result bodies.
3. Agent state: `ls data/agents/pi data/agents/deep`.
