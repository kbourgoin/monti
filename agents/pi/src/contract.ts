// The gateway <-> agent HTTP turn contract, mirrored from contract/src/turn_contract.

export const AUTH_ERROR_MARKER = "MONARCH_AUTH_REQUIRED";

export interface TurnRequest {
  session_id: string;
  message_id: string;
  text: string;
  /** ISO-8601 with timezone, kept exactly as the gateway sent it. */
  now: string;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  ms: number;
  ok: boolean;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
}

export interface TurnResponse {
  reply: string;
  tool_calls: ToolCall[];
  usage: Usage;
}

export type ErrorCode = "monarch_auth" | "timeout" | "internal";

export interface ErrorResponse {
  error: { code: ErrorCode; message: string };
}

export interface Health {
  name: string;
  framework: string;
  model: string;
}

export const ERROR_STATUS: Record<ErrorCode, number> = { monarch_auth: 502, timeout: 504, internal: 500 };

/** Monarch needs a new login: the proxy said AUTH_ERROR_MARKER. Maps to 502 monarch_auth. */
export class MonarchAuthError extends Error {
  override name = "MonarchAuthError";
}

/** A malformed turn request. Maps to 400 with code internal. */
export class BadRequestError extends Error {
  override name = "BadRequestError";
}

// YYYY-MM-DD, optionally followed by [T ]HH:MM[:SS[.fraction]] and an offset (Z, ±HH:MM or ±HHMM).
const ISO_DATETIME =
  /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|z|[+-]\d{2}:?\d{2})?)?$/;

/** Print an ISO-8601 timestamp the way Python's datetime.isoformat() prints it after pydantic parses it. */
function pythonIsoformat(now: string): string {
  const m = ISO_DATETIME.exec(now);
  if (!m) throw new BadRequestError(`bad request: now is not an ISO-8601 datetime: ${JSON.stringify(now)}`);
  const [, y, mo, d, h = "00", mi = "00", s = "00", frac, offset] = m;
  const valid = new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +s));
  if (valid.getUTCMonth() !== +mo - 1 || valid.getUTCDate() !== +d || +h > 23 || +mi > 59 || +s > 59) {
    throw new BadRequestError(`bad request: now is not a valid datetime: ${JSON.stringify(now)}`);
  }
  const micros = (frac ?? "").slice(0, 6).padEnd(6, "0");
  let out = `${y}-${mo}-${d}T${h}:${mi}:${s}${/^0+$/.test(micros) ? "" : `.${micros}`}`;
  if (offset) {
    const tz = offset.toUpperCase() === "Z" ? "+00:00" : offset.replace(/^([+-]\d{2}):?(\d{2})$/, "$1:$2");
    out += tz === "-00:00" ? "+00:00" : tz;
  }
  return out;
}

/** Prefix the user text with the current time; system prompts stay static. Same output as the Python version. */
export function formatUserMessage(text: string, now: string): string {
  return `Current time: ${pythonIsoformat(now)}\n\n${text}`;
}

// session_id names a directory under PI_DATA_DIR, so it must not be able to escape it.
const SAFE_SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** Validate a decoded JSON body as a TurnRequest, or throw BadRequestError. */
export function parseTurnRequest(body: unknown): TurnRequest {
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new BadRequestError("bad request: body must be a JSON object");
  }
  const fields = body as Record<string, unknown>;
  for (const key of ["session_id", "message_id", "text", "now"]) {
    if (typeof fields[key] !== "string") throw new BadRequestError(`bad request: ${key} must be a string`);
  }
  const req = fields as unknown as TurnRequest;
  if (!SAFE_SESSION_ID.test(req.session_id)) {
    throw new BadRequestError("bad request: session_id must be 1-128 letters, digits, '-' or '_'");
  }
  pythonIsoformat(req.now);
  return { session_id: req.session_id, message_id: req.message_id, text: req.text, now: req.now };
}
