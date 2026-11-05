"""The agent's direct imports are declared, volatile ones are bounded, and the compiled
deepagents graph binds the Monarch tools but no shell `execute` tool."""

import ast
import sys
import tomllib
from pathlib import Path

from agent_deep import agent as agent_mod
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from mcp import types
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]

# Top-level import name -> distribution that provides it.
DISTRIBUTION = {
    "deepagents": "deepagents",
    "fastapi": "fastapi",
    "langchain": "langchain",
    "langchain_core": "langchain-core",
    "langgraph": "langgraph-checkpoint-sqlite",
    "mcp": "mcp",
    "turn_contract": "turn-contract",
    "uvicorn": "uvicorn",
}
# Pre-1.0 or fast-moving: a new minor/major must be an explicit choice.
MUST_BE_BOUNDED = {
    "deepagents",
    "langchain",
    "langchain-core",
    "langchain-anthropic",
    "langgraph-checkpoint-sqlite",
    "mcp",
}


def _requirements() -> dict[str, Requirement]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return {(r := Requirement(d)).name: r for d in deps}


def _imported_top_levels() -> set[str]:
    names = set()
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names - set(sys.stdlib_module_names) - {"agent_deep"}


def test_direct_imports_are_declared():
    reqs = _requirements()
    imported = _imported_top_levels()
    assert imported <= DISTRIBUTION.keys(), f"map new imports: {imported - DISTRIBUTION.keys()}"
    missing = {DISTRIBUTION[m] for m in imported} - reqs.keys()
    assert not missing, f"undeclared direct dependencies: {missing}"


def test_volatile_dependencies_have_upper_bounds():
    reqs = _requirements()
    unbounded = {
        name
        for name in MUST_BE_BOUNDED
        if name not in reqs
        or not any(s.operator in ("<", "<=", "~=") for s in reqs[name].specifier)
    }
    assert not unbounded, f"needs an upper bound: {unbounded}"
    assert "0.8" not in reqs["deepagents"].specifier


BOUND: list[list[str]] = []  # tool names, per bind_tools call


class ToolRecordingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        BOUND.append([t["name"] if isinstance(t, dict) else t.name for t in tools])
        return self


class FakeClient:
    def __init__(self, url):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        schema = {"type": "object", "properties": {}}
        return types.ListToolsResult(
            tools=[
                types.Tool(name=n, description=n, input_schema=schema)
                for n in ("GetAccounts", "GetTransactions")
            ]
        )


async def test_real_graph_binds_monarch_tools_but_not_execute(monkeypatch, tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("You are Monti.")
    monkeypatch.setenv("SYSTEM_PROMPT_PATH", str(prompt))
    monkeypatch.setattr(agent_mod, "Client", FakeClient)
    model = ToolRecordingModel(messages=iter([AIMessage(content="hi")]))
    monkeypatch.setattr(agent_mod, "init_chat_model", lambda name: model)
    BOUND.clear()

    graph, _ = await agent_mod.build_agent(mcp_url="http://x/mcp", model="m", checkpointer=None)
    out = await graph.ainvoke({"messages": [HumanMessage("hello")]})

    assert out["messages"][-1].content == "hi"
    assert BOUND, "the agent never bound tools to the model"
    names = set(BOUND[-1])
    assert {"GetAccounts", "GetTransactions"} <= names
    assert "execute" not in names
