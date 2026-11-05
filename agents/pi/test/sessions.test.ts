import { readdir } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { afterEach, describe, expect, test } from "vitest";
import { MonarchAuthError, type TurnRequest } from "../src/contract.ts";
import { toolNames } from "../src/sessions.ts";
import { fauxStore, setMode, SYSTEM_PROMPT, tempDir } from "./helpers.ts";

afterEach(() => setMode("ok"));

const NOW = "2026-11-04T22:00:00-05:00";
const turn = (session_id: string, text: string): TurnRequest => ({
  session_id,
  message_id: randomUUID(),
  text,
  sender_name: "Zed",
  now: NOW,
});
const callAccounts = () =>
  fauxAssistantMessage(fauxToolCall("GetAccounts", { intent: "account overview" }), { stopReason: "toolUse" });
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("getSession", () => {
  test("session has only Monarch tools", async () => {
    const { store } = await fauxStore(await tempDir());
    const s = await store.getSession(randomUUID());
    expect(toolNames(s).sort()).toEqual(["GetAccounts", "GetTransactions"]);
    expect(s.session.getActiveToolNames().sort()).toEqual(["GetAccounts", "GetTransactions"]);
    expect(s.session.getAllTools().every((t) => t.sourceInfo.source === "sdk")).toBe(true);
    // Built-ins are not even registered, so they cannot be switched on later.
    s.session.setActiveToolsByName(["bash", "read", "write", "edit", "grep", "find", "ls", "GetAccounts"]);
    expect(s.session.getActiveToolNames()).toEqual(["GetAccounts"]);
  });

  test("uses the configured system prompt", async () => {
    const { store } = await fauxStore(await tempDir());
    const s = await store.getSession(randomUUID());
    expect(s.session.systemPrompt.startsWith(SYSTEM_PROMPT)).toBe(true);
    expect(s.session.systemPrompt).not.toContain("coding assistant");
  });

  test("caches one session per id", async () => {
    const { store } = await fauxStore(await tempDir());
    const id = randomUUID();
    const [a, b] = await Promise.all([store.getSession(id), store.getSession(id)]);
    expect(a).toBe(b);
    expect(await store.getSession(randomUUID())).not.toBe(a);
  });

  test("tools/list auth failure rejects and is not cached", async () => {
    const { store } = await fauxStore(await tempDir());
    const id = randomUUID();
    await setMode("auth_error");
    await expect(store.getSession(id)).rejects.toBeInstanceOf(MonarchAuthError);
    await setMode("ok");
    expect(toolNames(await store.getSession(id)).sort()).toEqual(["GetAccounts", "GetTransactions"]);
  });
});

describe("runTurn", () => {
  test("returns the reply, tool calls and usage", async () => {
    let seen = "";
    const { store } = await fauxStore(await tempDir(), [
      (context) => {
        seen = JSON.stringify(context.messages.at(-1));
        return callAccounts();
      },
      fauxAssistantMessage("You have Webster Checking."),
    ]);
    const res = await store.runTurn(turn(randomUUID(), "What accounts do I have?"));
    expect(res.reply).toBe("You have Webster Checking.");
    expect(res.tool_calls).toMatchObject([{ name: "GetAccounts", args: { intent: "account overview" }, ok: true }]);
    expect(res.usage.input_tokens).toBeGreaterThan(0);
    expect(res.usage.output_tokens).toBeGreaterThan(0);
    expect(seen).toContain(`Current time: ${NOW}\\nFrom: Zed\\n\\nWhat accounts do I have?`);
  });

  test("auth marker in a tool result fails the turn with MonarchAuthError", async () => {
    const { store } = await fauxStore(await tempDir(), [callAccounts(), fauxAssistantMessage("Sorry.")]);
    const id = randomUUID();
    await store.getSession(id);
    await setMode("auth_error");
    await expect(store.runTurn(turn(id, "What accounts do I have?"))).rejects.toBeInstanceOf(MonarchAuthError);
  });

  test("tool calls and auth state are per turn", async () => {
    const { store } = await fauxStore(await tempDir(), [
      callAccounts(),
      fauxAssistantMessage("Sorry."),
      fauxAssistantMessage("Hello again."),
    ]);
    const id = randomUUID();
    await store.getSession(id);
    await setMode("auth_error");
    await expect(store.runTurn(turn(id, "accounts?"))).rejects.toBeInstanceOf(MonarchAuthError);
    await setMode("ok");
    const res = await store.runTurn(turn(id, "hi"));
    expect(res).toMatchObject({ reply: "Hello again.", tool_calls: [] });
  });

  test("serializes concurrent turns in one session", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const slow = async () => {
      maxInFlight = Math.max(maxInFlight, ++inFlight);
      await sleep(50);
      inFlight--;
      return fauxAssistantMessage("done");
    };
    const { store } = await fauxStore(await tempDir(), [slow, slow]);
    const id = randomUUID();
    const replies = await Promise.all([store.runTurn(turn(id, "one")), store.runTurn(turn(id, "two"))]);
    expect(replies.map((r) => r.reply)).toEqual(["done", "done"]);
    expect(maxInFlight).toBe(1);
  });

  test("persists the session and resumes it from the data dir", async () => {
    const dataDir = await tempDir();
    const id = randomUUID();
    const first = await fauxStore(dataDir, [fauxAssistantMessage("Noted.")]);
    await first.store.runTurn(turn(id, "Remember the code word teal-42."));
    expect((await readdir(join(dataDir, id))).some((f) => f.endsWith(".jsonl"))).toBe(true);

    const second = await fauxStore(dataDir, [
      (context) => fauxAssistantMessage(JSON.stringify(context.messages).includes("teal-42") ? "teal-42" : "no idea"),
    ]);
    expect((await second.store.runTurn(turn(id, "What was the code word?"))).reply).toBe("teal-42");
  });

  test("a model error fails the turn", async () => {
    const { store } = await fauxStore(await tempDir(), [
      fauxAssistantMessage("", { stopReason: "error", errorMessage: "boom" }),
    ]);
    await expect(store.runTurn(turn(randomUUID(), "hi"))).rejects.toThrow(/boom/);
  });

  test("an aborted turn releases the session for the next turn", async () => {
    const { store } = await fauxStore(await tempDir(), [
      async () => {
        await sleep(300);
        return fauxAssistantMessage("too late");
      },
      fauxAssistantMessage("next"),
    ]);
    const id = randomUUID();
    const controller = new AbortController();
    const aborted = store.runTurn(turn(id, "slow"), controller.signal);
    setTimeout(() => controller.abort(), 50);
    await expect(aborted).rejects.toThrow();
    expect((await store.runTurn(turn(id, "again"))).reply).toBe("next");
  });
});
