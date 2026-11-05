import stat

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from monarch_proxy.tokens import FileTokenStorage


def _tok(access="a"):
    return OAuthToken(access_token=access, token_type="Bearer", refresh_token="r", expires_in=3600)


def _client():
    return OAuthClientInformationFull(
        client_id="cid",
        redirect_uris=["http://127.0.0.1:8765/callback"],
        token_endpoint_auth_method="none",
    )


async def test_roundtrip_and_mode(tmp_path):
    path = tmp_path / "sub" / "tokens.json"
    s = FileTokenStorage(path)
    assert await s.get_tokens() is None
    assert await s.get_client_info() is None
    await s.set_tokens(_tok())
    assert (await FileTokenStorage(path).get_tokens()).refresh_token == "r"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["tokens.json"]


async def test_client_info_preserved_when_tokens_written(tmp_path):
    path = tmp_path / "tokens.json"
    s = FileTokenStorage(path)
    await s.set_client_info(_client())
    await s.set_tokens(_tok("one"))
    await s.set_tokens(_tok("two"))
    fresh = FileTokenStorage(path)
    assert (await fresh.get_client_info()).client_id == "cid"
    assert (await fresh.get_tokens()).access_token == "two"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
