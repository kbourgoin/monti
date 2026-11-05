from monarch_proxy.allowlist import READ_TOOLS, is_allowed


def test_allowlist_exact():
    assert is_allowed("GetTransactions") and is_allowed("ListRules")
    assert not is_allowed("UpdateTransaction") and not is_allowed("ReportIssue")
    assert not is_allowed("GetSomethingNew")  # unknown tools denied, even with Get prefix


def test_allowlist_has_17_read_tools():
    assert len(READ_TOOLS) == 17
    assert not any(n.startswith(("Create", "Update", "Delete")) for n in READ_TOOLS)
