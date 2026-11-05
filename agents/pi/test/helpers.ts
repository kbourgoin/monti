import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  fauxProvider,
  type FauxProviderHandle,
  type FauxResponseStep,
  InMemoryCredentialStore,
} from "@earendil-works/pi-ai";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { onTestFinished } from "vitest";
import { SessionStore } from "../src/sessions.ts";
import { FAKE_MONARCH_URL } from "./fakeMonarch.ts";

export const MCP_URL = `${FAKE_MONARCH_URL}/mcp`;
export const SYSTEM_PROMPT = "You are a test helper for Keith.";

export async function setMode(mode: "ok" | "auth_error"): Promise<void> {
  const r = await fetch(`${FAKE_MONARCH_URL}/_mode`, { method: "POST", body: JSON.stringify({ mode }) });
  if (!r.ok) throw new Error(`set mode failed: ${r.status}`);
}

/** A data dir removed when the calling test finishes. */
export async function tempDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "agent-pi-test-"));
  onTestFinished(() => rm(dir, { recursive: true, force: true }));
  return dir;
}

/** A SessionStore wired to fake-monarch and a scripted (faux) model: no network LLM calls. */
export async function fauxStore(
  dataDir: string,
  responses: FauxResponseStep[] = [],
): Promise<{ store: SessionStore; faux: FauxProviderHandle }> {
  const modelRuntime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(),
    modelsPath: null,
    refreshOnCreate: false,
  });
  const faux = fauxProvider();
  modelRuntime.registerNativeProvider(faux.provider);
  faux.setResponses(responses);
  const store = new SessionStore({
    dataDir,
    systemPrompt: SYSTEM_PROMPT,
    modelRuntime,
    model: faux.getModel(),
    monarchUrl: MCP_URL,
  });
  return { store, faux };
}
