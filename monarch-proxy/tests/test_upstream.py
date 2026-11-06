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


async def test_startup_refreshes_an_expired_token_before_warning(tmp_path, monkeypatch, caplog):
    # Access token expired while the proxy was down: startup refreshes it like a tool call would,
    # instead of warning that the login is unusable.
    from monarch_proxy import auth as auth_mod

    up = upstream_mod.MonarchUpstream(FileTokenStorage(tmp_path / "t.json"))
    connects, refreshes = [], []

    async def connect():
        connects.append(1)
        if len(connects) == 1:
            raise MonarchAuthRequired("expired")
        return object()

    async def refresh(provider, *, client=None):
        refreshes.append(1)
        return True

    async def disconnect():
        return None

    monkeypatch.setattr(up, "_connect", connect)
    monkeypatch.setattr(up, "_disconnect", disconnect)
    monkeypatch.setattr(auth_mod, "force_refresh", refresh)
    with caplog.at_level("WARNING"):
        async with up:
            pass
    assert connects == [1, 1] and refreshes == [1]
    assert "unusable at startup" not in caplog.text
