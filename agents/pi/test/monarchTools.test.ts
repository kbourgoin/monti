import { afterEach, describe, expect, test } from "vitest";
import { AUTH_ERROR_MARKER, MonarchAuthError } from "../src/contract.ts";
import { loadMonarchTools, newRecorder } from "../src/monarchTools.ts";
import { MCP_URL, setMode } from "./helpers.ts";

afterEach(() => setMode("ok"));

function find(tools: Awaited<ReturnType<typeof loadMonarchTools>>, name: string) {
  const tool = tools.find((t) => t.name === name);
  if (!tool) throw new Error(`no tool ${name}`);
  return tool;
}

async function call(tools: Awaited<ReturnType<typeof loadMonarchTools>>, name: string, args: object) {
  return find(tools, name).execute("call-1", args, undefined, undefined, undefined as never);
}

describe("loadMonarchTools", () => {
  test("wraps each proxy tool, passing the MCP JSON schema through", async () => {
    const tools = await loadMonarchTools(MCP_URL, newRecorder());
    expect(tools.map((t) => t.name).sort()).toEqual(["GetAccounts", "GetTransactions"]);
    expect(find(tools, "GetAccounts").parameters).toEqual({ type: "object", properties: {} });
    expect(find(tools, "GetAccounts").description).toBe("List accounts.");
  });

  test("records ms/ok per call", async () => {
    const recorder = newRecorder();
    const tools = await loadMonarchTools(MCP_URL, recorder);
    const result = await call(tools, "GetAccounts", { intent: "account overview" });
    expect(result.isError).toBeFalsy();
    expect(JSON.stringify(result.content)).toContain("Webster Checking");
    expect(recorder.calls).toHaveLength(1);
    const [rec] = recorder.calls;
    expect(rec).toMatchObject({ name: "GetAccounts", args: { intent: "account overview" }, ok: true });
    expect(Number.isInteger(rec.ms) && rec.ms >= 0).toBe(true);
    expect(recorder.authFailed).toBe(false);
  });

  test("auth marker in a tool result sets authFailed", async () => {
    const recorder = newRecorder();
    const tools = await loadMonarchTools(MCP_URL, recorder);
    await setMode("auth_error");
    const result = await call(tools, "GetTransactions", {});
    expect(result.isError).toBe(true);
    expect(JSON.stringify(result.content)).toContain(AUTH_ERROR_MARKER);
    expect(recorder.authFailed).toBe(true);
    expect(recorder.calls).toMatchObject([{ name: "GetTransactions", ok: false }]);
  });

  test("tools/list failure with the auth marker rejects with MonarchAuthError", async () => {
    await setMode("auth_error");
    await expect(loadMonarchTools(MCP_URL, newRecorder())).rejects.toBeInstanceOf(MonarchAuthError);
  });

  test("an unreachable proxy rejects with an ordinary error", async () => {
    const err = await loadMonarchTools("http://127.0.0.1:1/mcp", newRecorder()).catch((e) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(MonarchAuthError);
  });
});
