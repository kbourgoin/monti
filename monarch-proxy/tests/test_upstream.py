import pytest
from monarch_proxy import upstream as upstream_mod
from monarch_proxy.auth import MonarchAuthRequired
from monarch_proxy.tokens import FileTokenStorage


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        pytest.fail("network client created despite missing tokens")

    monkeypatch.setattr(upstream_mod, "create_mcp_http_client", boom)
    monkeypatch.setattr(upstream_mod, "streamable_http_client", boom)


async def test_no_tokens_is_offline_at_startup_and_on_calls(tmp_path, no_network):
    up = upstream_mod.MonarchUpstream(FileTokenStorage(tmp_path / "none.json"))
    async with up:  # startup swallows auth-required, makes no I/O
        with pytest.raises(MonarchAuthRequired):
            await up.list_tools()
        with pytest.raises(MonarchAuthRequired):
            await up.call_tool("GetAccounts", {})
    assert not (tmp_path / "none.json").exists()
