// Monarch tools for Pi: a thin wrapper that exposes each tool the proxy lists as a Pi custom tool.
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { AUTH_ERROR_MARKER, MonarchAuthError, type ToolCall } from "./contract.ts";

/** Per-turn record of the Monarch calls a session made. */
export interface ToolRecorder {
  calls: ToolCall[];
  authFailed: boolean;
}

export function newRecorder(): ToolRecorder {
  return { calls: [], authFailed: false };
}

const messageOf = (err: unknown): string => (err instanceof Error ? err.message : String(err));

/**
 * Run `fn` on a fresh MCP connection to the proxy and close it afterwards. Connecting per use keeps
 * nothing stale when the proxy restarts, and costs only a local handshake.
 */
async function withClient<T>(url: string, fn: (client: Client) => Promise<T>): Promise<T> {
  const transport = new StreamableHTTPClientTransport(new URL(url));
  const client = new Client({ name: "agent-pi", version: "0.1.0" });
  await client.connect(transport);
  try {
    return await fn(client);
  } finally {
    await transport.terminateSession().catch(() => {});
    await client.close().catch(() => {});
  }
}

/** List the proxy's tools and wrap each one; a tools/list auth failure rejects with MonarchAuthError. */
export async function loadMonarchTools(url: string, recorder: ToolRecorder): Promise<ToolDefinition[]> {
  let listed;
  try {
    listed = await withClient(url, async (client) => {
      const tools = [];
      let cursor: string | undefined;
      do {
        const page = await client.listTools(cursor ? { cursor } : undefined);
        tools.push(...page.tools);
        cursor = page.nextCursor;
      } while (cursor);
      return tools;
    });
  } catch (err) {
    if (messageOf(err).includes(AUTH_ERROR_MARKER)) throw new MonarchAuthError(messageOf(err));
    throw err;
  }

  return listed.map(
    (tool): ToolDefinition => ({
      name: tool.name,
      label: tool.title ?? tool.name,
      description: tool.description ?? tool.name,
      // MCP tools carry plain JSON Schema; Pi validates tool arguments against any JSON Schema.
      parameters: tool.inputSchema as ToolDefinition["parameters"],
      async execute(_toolCallId, params, signal) {
        const args = (params ?? {}) as Record<string, unknown>;
        const started = performance.now();
        const record = (ok: boolean, text: string) => {
          if (text.includes(AUTH_ERROR_MARKER)) recorder.authFailed = true;
          recorder.calls.push({ name: tool.name, args, ms: Math.round(performance.now() - started), ok });
        };
        let result;
        try {
          result = await withClient(url, (client) =>
            client.callTool({ name: tool.name, arguments: args }, undefined, { signal }),
          );
        } catch (err) {
          record(false, messageOf(err));
          throw err;
        }
        const blocks = Array.isArray(result.content) ? result.content : [];
        const text = blocks
          .map((block) => (block.type === "text" ? block.text : JSON.stringify(block)))
          .join("\n");
        const isError = result.isError === true;
        record(!isError, text);
        return { content: [{ type: "text", text }], details: undefined, isError };
      },
    }),
  );
}
