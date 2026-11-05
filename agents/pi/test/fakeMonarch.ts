// Vitest global setup: run the repo's fake Monarch MCP server for the whole test run.
import { type ChildProcess, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

export const FAKE_MONARCH_PORT = 8191;
export const FAKE_MONARCH_URL = `http://127.0.0.1:${FAKE_MONARCH_PORT}`;

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));

async function waitUntilUp(child: ChildProcess): Promise<void> {
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error(`fake-monarch exited with ${child.exitCode}`);
    try {
      const r = await fetch(`${FAKE_MONARCH_URL}/_mode`, { method: "POST", body: '{"mode":"ok"}' });
      if (r.ok) return;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("fake-monarch did not start");
}

export default async function setup(): Promise<() => void> {
  const child = spawn("uv", ["run", "fake-monarch", "--port", String(FAKE_MONARCH_PORT)], {
    cwd: repoRoot,
    stdio: "ignore",
  });
  await waitUntilUp(child);
  return () => {
    child.kill();
  };
}
