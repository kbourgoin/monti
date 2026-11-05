import { describe, expect, test } from "vitest";
import { BadRequestError, formatUserMessage, parseTurnRequest } from "../src/contract.ts";

describe("formatUserMessage matches python", () => {
  // Expected values are what turn_contract.format_user_message prints after pydantic parses `now`.
  test.each([
    ["2026-11-04T22:00:00-05:00", "2026-11-04T22:00:00-05:00"],
    ["2026-11-04T22:00:00Z", "2026-11-04T22:00:00+00:00"],
    ["2026-11-05T03:00:00.123Z", "2026-11-05T03:00:00.123000+00:00"],
    ["2026-11-05T03:00:00.000000+00:00", "2026-11-05T03:00:00+00:00"],
    ["2026-11-05T03:00:00.1234567+05:30", "2026-11-05T03:00:00.123456+05:30"],
    ["2026-11-04 22:00:00-05:00", "2026-11-04T22:00:00-05:00"],
    ["2026-11-04T22:00-05:00", "2026-11-04T22:00:00-05:00"],
    ["2026-11-04T22:00:00", "2026-11-04T22:00:00"],
    ["2026-11-04T22:00:00-0500", "2026-11-04T22:00:00-05:00"],
    ["2026-11-04", "2026-11-04T00:00:00"],
  ])("%s", (now, printed) => {
    expect(formatUserMessage("hi", now, "Zed")).toBe(`Current time: ${printed}\nFrom: Zed\n\nhi`);
  });
});

describe("parseTurnRequest", () => {
  const valid = {
    session_id: "6f1c1d2e-0000-4000-8000-000000000001",
    message_id: "m1",
    text: "hello",
    sender_name: "Zed",
    now: "2026-11-04T22:00:00-05:00",
  };

  test("accepts a valid request", () => {
    expect(parseTurnRequest(valid)).toEqual(valid);
  });

  test.each([
    ["empty object", {}],
    ["not an object", "hi"],
    ["numeric session_id", { ...valid, session_id: 1 }],
    ["missing text", { ...valid, text: undefined }],
    ["missing sender_name", { ...valid, sender_name: undefined }],
    ["bad now", { ...valid, now: "yesterday" }],
    ["impossible date", { ...valid, now: "2026-13-40T00:00:00Z" }],
    ["path traversal in session_id", { ...valid, session_id: "../../etc" }],
    ["empty session_id", { ...valid, session_id: "" }],
  ])("rejects %s", (_label, body) => {
    expect(() => parseTurnRequest(body)).toThrow(BadRequestError);
  });
});
