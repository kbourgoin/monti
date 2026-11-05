from gateway.formatting import split_reply


def test_split_short():
    assert split_reply("hi") == ["hi"]


def test_split_on_paragraphs():
    assert split_reply("a" * 1000 + "\n\n" + "b" * 1000) == ["a" * 1000, "b" * 1000]


def test_split_falls_back_to_lines_then_hard():
    parts = split_reply("x" * 4000)
    assert all(len(p) <= 1500 for p in parts) and "".join(parts) == "x" * 4000


def test_split_lines():
    text = "\n".join(["line" * 50] * 20)
    assert all(len(p) <= 1500 for p in split_reply(text))


def test_split_empty():
    assert split_reply("   ") == ["(no reply)"]
