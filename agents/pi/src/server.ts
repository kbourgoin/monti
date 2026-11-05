// HTTP server for the turn contract: GET /health and POST /turn on port 8202.
import { createServer as createHttpServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { pathToFileURL } from "node:url";
import {
  BadRequestError,
  ERROR_STATUS,
  type ErrorCode,
  type ErrorResponse,
  type Health,
  MonarchAuthError,
  parseTurnRequest,
  type TurnRequest,
  type TurnResponse,
} from "./contract.ts";
import { sessionStoreFromEnv } from "./sessions.ts";

export interface TurnRunner {
  runTurn(req: TurnRequest, signal: AbortSignal): Promise<TurnResponse>;
}

export interface ServerOptions {
  runner: TurnRunner;
  model: string;
  /** Agents give up before the gateway's 120s timeout. */
  timeoutMs?: number;
}

const MAX_BODY_BYTES = 1 << 20;

class TurnTimeout extends Error {}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function sendError(res: ServerResponse, status: number, code: ErrorCode, message: string): void {
  send(res, status, { error: { code, message } } satisfies ErrorResponse);
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new BadRequestError("bad request: body too large");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new BadRequestError("bad request: body is not valid JSON");
  }
}

/** Run the turn, or reject with TurnTimeout after `ms` and abort it. */
async function withTimeout(runner: TurnRunner, req: TurnRequest, ms: number): Promise<TurnResponse> {
  const controller = new AbortController();
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new TurnTimeout(`turn took longer than ${ms / 1000}s`));
    }, ms);
  });
  try {
    return await Promise.race([runner.runTurn(req, controller.signal), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

async function handleTurn(opts: ServerOptions, req: IncomingMessage, res: ServerResponse): Promise<void> {
  let turn: TurnRequest;
  try {
    turn = parseTurnRequest(await readJson(req));
  } catch (err) {
    if (err instanceof BadRequestError) return sendError(res, 400, "internal", err.message);
    throw err;
  }
  try {
    send(res, 200, await withTimeout(opts.runner, turn, opts.timeoutMs ?? 110_000));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const code: ErrorCode =
      err instanceof MonarchAuthError ? "monarch_auth" : err instanceof TurnTimeout ? "timeout" : "internal";
    if (code === "internal") console.error(`turn ${turn.session_id} failed:`, err);
    sendError(res, ERROR_STATUS[code], code, message);
  }
}

export function createServer(opts: ServerOptions): Server {
  return createHttpServer((req, res) => {
    const route = `${req.method} ${req.url}`;
    if (route === "GET /health") {
      return send(res, 200, { name: "pi", framework: "pi-mono", model: opts.model } satisfies Health);
    }
    if (route === "POST /turn") {
      return void handleTurn(opts, req, res).catch((err) => {
        console.error("unhandled /turn error:", err);
        if (!res.headersSent) sendError(res, 500, "internal", String(err));
      });
    }
    sendError(res, 404, "internal", `not found: ${route}`);
  });
}

async function main(): Promise<void> {
  const model = process.env.MODEL || "claude-sonnet-5-5";
  const runner = await sessionStoreFromEnv();
  const host = process.env.AGENT_HOST || "127.0.0.1";
  createServer({ runner, model }).listen(8202, host, () => console.log(`agent-pi listening on ${host}:8202`));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
