import pytest
from gateway.config import User, load_settings

BASE = {
    "BOT_NUMBER": "+1555", "SIGNAL_URL": "http://s", "AGENTS": "pi=http://pi", "DEFAULT_AGENT": "pi",
}


def settings(users):
    return load_settings({**BASE, "USERS": users})


def test_users_parse_and_validate():
    got = settings("Keith=+16175550100, Alex=+16175550101/u-1").users
    assert got == (User("Keith", "+16175550100", None), User("Alex", "+16175550101", "u-1"))
    for bad in ["", " , ", "A=+1617555010,A=+16175550102", "A=+16175550100,B=+16175550100",
                "Keith", "=+16175550100", "A=6175550100", "A=+16175550100/"]:
        with pytest.raises(ValueError):
            settings(bad)
    with pytest.raises(ValueError):
        load_settings(BASE)
