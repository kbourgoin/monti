import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, test, vi } from "vitest";
import { MonarchAuthError, type TurnRequest, type TurnResponse } from "../src/contract.ts";
import { createServer, type TurnRunner } from "../src/server.ts";
import { fauxStore, setMode, tempDir } from "./helpers.ts";

const servers: Server[] = [];
afterEach(async () => {
  await setMode("ok");
  await Promise.all(servers.splice(0).map((s) => new Promise((resolve) => s.close(resolve))));
});

async function serve(runner: TurnRunner, timeoutMs = 5_000): Promise<string> {
  const server = createServer({ runner, model: "claude-test", timeoutMs });
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

const body = (): TurnRequest => ({
  session_id: randomUUID(),
  message_id: randomUUID(),
  text: "hello",
  sender_name: "Zed",
  now: "2026-11-04T22:00:00-05:00",
});
const post = (url: string, payload: unknown) =>
  fetch(`${url}/turn`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof payload === "string" ? payload : JSON.stringify(payload),
  });
const ok: TurnRunner = {
  runTurn: async (req) => ({ reply: `echo ${req.text}`, tool_calls: [], usage: { input_tokens: 1, output_tokens: 2 } }),
};

describe("server", () => {
  test("GET /health", async () => {
    const url = await serve(ok);
    const r = await fetch(`${url}/health`);
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ name: "pi", framework: "pi-mono", model: "claude-test" });
  });

  test("POST /turn {} → 400 internal", async () => {
    const r = await post(await serve(ok), {});
    expect(r.status).toBe(400);
    const json = await r.json();
    expect(json.error.code).toBe("internal");
    expect(typeof json.error.message).toBe("string");
  });

  test("invalid JSON → 400 internal", async () => {
    const r = await post(await serve(ok), "{nope");
    expect(r.status).toBe(400);
    expect((await r.json()).error.code).toBe("internal");
  });

  test("unsafe session_id → 400 internal", async () => {
    const r = await post(await serve(ok), { ...body(), session_id: "../escape" });
    expect(r.status).toBe(400);
  });

  test("a turn returns the runner's response", async () => {
    const r = await post(await serve(ok), body());
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ reply: "echo hello", tool_calls: [], usage: { input_tokens: 1, output_tokens: 2 } });
  });

  test("MonarchAuthError → 502 monarch_auth", async () => {
    const url = await serve({ runTurn: async () => Promise.reject(new MonarchAuthError("MONARCH_AUTH_REQUIRED")) });
    const r = await post(url, body());
    expect(r.status).toBe(502);
    expect((await r.json()).error.code).toBe("monarch_auth");
  });

  test("other errors → 500 internal", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const url = await serve({ runTurn: async () => Promise.reject(new Error("kaput")) });
    const r = await post(url, body());
    expect(r.status).toBe(500);
    expect((await r.json()).error).toEqual({ code: "internal", message: "kaput" });
    expect(logged).toHaveBeenCalled();
    logged.mockRestore();
  });

  test("a turn past the timeout → 504 timeout and the run is aborted", async () => {
    let signal: AbortSignal | undefined;
    const hang: TurnRunner = {
      runTurn: (_req, s) => {
        signal = s;
        return new Promise<TurnResponse>(() => {});
      },
    };
    const r = await post(await serve(hang, 50), body());
    expect(r.status).toBe(504);
    expect((await r.json()).error.code).toBe("timeout");
    expect(signal?.aborted).toBe(true);
  });

  test("unknown route → 404", async () => {
    const r = await fetch(`${await serve(ok)}/nope`);
    expect(r.status).toBe(404);
  });

  test("tools/list auth failure on a new session → 502 monarch_auth (real store)", async () => {
    const { store } = await fauxStore(await tempDir());
    const url = await serve(store);
    await setMode("auth_error");
    const r = await post(url, body());
    expect(r.status).toBe(502);
    expect((await r.json()).error.code).toBe("monarch_auth");
  });
});
