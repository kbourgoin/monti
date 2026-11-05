// One persisted Pi session per gateway session_id, with only the Monarch tools and one turn at a time.
import { mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { type AssistantMessage, InMemoryCredentialStore, type Model } from "@earendil-works/pi-ai";
import {
  type AgentSession,
  createAgentSession,
  createExtensionRuntime,
  ModelRuntime,
  type ResourceLoader,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { formatUserMessage, MonarchAuthError, type TurnRequest, type TurnResponse } from "./contract.ts";
import { loadMonarchTools, newRecorder, type ToolRecorder } from "./monarchTools.ts";

export interface SessionStoreOptions {
  /** Sessions persist under `<dataDir>/<sessionId>/`. */
  dataDir: string;
  systemPrompt: string;
  modelRuntime: ModelRuntime;
  model: Model<any>;
  /** The Monarch proxy's MCP endpoint. */
  monarchUrl: string;
}

export interface PiAgentSession {
  id: string;
  session: AgentSession;
  /** Shared with the session's Monarch tools; reset at the start of each turn. */
  recorder: ToolRecorder;
  /** Serializes turns: Pi rejects a prompt while the session is busy. */
  lock: Mutex;
}

class Mutex {
  private tail: Promise<unknown> = Promise.resolve();

  run<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(fn);
    this.tail = result.catch(() => {});
    return result;
  }
}

export function toolNames(s: PiAgentSession): string[] {
  return s.session.getAllTools().map((t) => t.name);
}

/** Resources with nothing discovered from disk: no extensions, skills, prompt templates or context files. */
function fixedResources(systemPrompt: string): ResourceLoader {
  return {
    getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
    getSkills: () => ({ skills: [], diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}

/** Refuse a session whose tools are anything other than exactly our Monarch wrappers. */
function assertOnlyMonarchTools(session: AgentSession, monarch: string[]): void {
  const registered = session.getAllTools();
  const expected = [...monarch].sort().join(",");
  const actual = registered.map((t) => t.name).sort().join(",");
  const foreign = registered.filter((t) => t.sourceInfo.source !== "sdk").map((t) => t.name);
  if (actual !== expected || foreign.length > 0) {
    session.dispose();
    throw new Error(`refusing Pi session: tools [${actual}] are not exactly the Monarch tools [${expected}]`);
  }
}

export class SessionStore {
  private readonly sessions = new Map<string, Promise<PiAgentSession>>();
  private readonly opts: SessionStoreOptions;

  constructor(opts: SessionStoreOptions) {
    this.opts = opts;
  }

  /** The cached session for `sessionId`, created (or resumed from disk) on first use. */
  getSession(sessionId: string): Promise<PiAgentSession> {
    let pending = this.sessions.get(sessionId);
    if (!pending) {
      pending = this.create(sessionId);
      this.sessions.set(sessionId, pending);
      pending.catch(() => this.sessions.delete(sessionId));
    }
    return pending;
  }

  private async create(id: string): Promise<PiAgentSession> {
    const dir = join(this.opts.dataDir, id);
    await mkdir(dir, { recursive: true });
    const recorder = newRecorder();
    const monarchTools = await loadMonarchTools(this.opts.monarchUrl, recorder);
    const names = monarchTools.map((t) => t.name);
    const { session } = await createAgentSession({
      cwd: dir,
      agentDir: dir,
      model: this.opts.model,
      thinkingLevel: "off",
      modelRuntime: this.opts.modelRuntime,
      resourceLoader: fixedResources(this.opts.systemPrompt),
      settingsManager: SettingsManager.inMemory({ enableInstallTelemetry: false }),
      sessionManager: SessionManager.continueRecent(dir, dir),
      // Zero built-ins: start from no tools, and allow exactly the Monarch tool names.
      noTools: "all",
      tools: names,
      customTools: monarchTools,
    });
    assertOnlyMonarchTools(session, names);
    return { id, session, recorder, lock: new Mutex() };
  }

  /** Run one turn. Rejects with MonarchAuthError if Monarch needs a login; aborting `signal` stops the run. */
  async runTurn(req: TurnRequest, signal?: AbortSignal): Promise<TurnResponse> {
    const entry = await this.getSession(req.session_id);
    return entry.lock.run(async () => {
      signal?.throwIfAborted();
      const { session, recorder } = entry;
      recorder.calls = [];
      recorder.authFailed = false;
      const usage = { input_tokens: 0, output_tokens: 0 };
      let last: AssistantMessage | undefined;
      const unsubscribe = session.subscribe((event) => {
        if (event.type === "message_end" && event.message.role === "assistant") {
          last = event.message;
          const u = last.usage;
          usage.input_tokens += u.input + u.cacheRead + u.cacheWrite;
          usage.output_tokens += u.output;
        }
      });
      const abort = () => void session.abort();
      signal?.addEventListener("abort", abort, { once: true });
      try {
        await session.prompt(formatUserMessage(req.text, req.now), { expandPromptTemplates: false });
      } finally {
        signal?.removeEventListener("abort", abort);
        unsubscribe();
      }
      if (recorder.authFailed) throw new MonarchAuthError("Monarch login required (MONARCH_AUTH_REQUIRED)");
      signal?.throwIfAborted();
      if (last?.stopReason === "error" || last?.stopReason === "aborted") {
        throw new Error(`model ${last.stopReason}: ${last.errorMessage ?? "no details"}`);
      }
      return { reply: session.getLastAssistantText() ?? "", tool_calls: [...recorder.calls], usage };
    });
  }
}

const repoRoot = fileURLToPath(new URL("../../../", import.meta.url));

/** The production store: Anthropic `MODEL`, prompt from SYSTEM_PROMPT_PATH, sessions under PI_DATA_DIR. */
export async function sessionStoreFromEnv(env: NodeJS.ProcessEnv = process.env): Promise<SessionStore> {
  const modelId = env.MODEL || "claude-sonnet-5-5";
  const modelRuntime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(),
    modelsPath: null,
    refreshOnCreate: false,
  });
  if (env.ANTHROPIC_API_KEY) await modelRuntime.setRuntimeApiKey("anthropic", env.ANTHROPIC_API_KEY);
  const model = modelRuntime.getModel("anthropic", modelId);
  if (!model) throw new Error(`unknown Anthropic model: ${modelId}`);
  const promptPath = env.SYSTEM_PROMPT_PATH || join(repoRoot, "prompts", "system.md");
  return new SessionStore({
    dataDir: env.PI_DATA_DIR || join(repoRoot, "data", "agents", "pi"),
    systemPrompt: (await readFile(promptPath, "utf8")).trim(),
    modelRuntime,
    model,
    monarchUrl: env.MONARCH_MCP_URL || "http://monarch-proxy:8100/mcp",
  });
}
