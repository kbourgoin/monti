import pytest
from gateway.commands import Command, parse_command


@pytest.mark.parametrize(
    "t,exp",
    [
        ("/agent pi", Command("agent", "pi")),
        ("  /Agent PI ", Command("agent", "pi")),
        ("/new", Command("new", None)),
        ("/WHICH", Command("which", None)),
        ("/help", Command("help", None)),
        ("/new now", Command("new", "now")),
        ("/spending", None),
        ("hello", None),
    ],
)
def test_parse(t, exp):
    assert parse_command(t) == exp
