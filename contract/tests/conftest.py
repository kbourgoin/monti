import os

import httpx
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: needs a real LLM agent; run with -m llm")


def pytest_collection_modifyitems(config, items):
    if "llm" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="LLM test; pass -m llm to run")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def agent_url() -> str:
    url = os.environ.get("AGENT_URL")
    if not url:
        pytest.skip("AGENT_URL not set")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def client(agent_url):
    with httpx.Client(base_url=agent_url, timeout=130) as c:
        yield c


@pytest.fixture
def set_fake_mode():
    url = os.environ.get("FAKE_MONARCH_URL")
    if not url:
        pytest.skip("FAKE_MONARCH_URL not set")

    def _set(mode: str) -> None:
        httpx.post(f"{url.rstrip('/')}/_mode", json={"mode": mode}).raise_for_status()

    return _set
