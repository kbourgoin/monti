import json
from pathlib import Path

import httpx
import pytest
import respx
from gateway.router import DirectRecipient, GroupRecipient, Inbound
from gateway.signal_client import (
    GroupInfo,
    GroupUpdate,
    MemberRef,
    SignalClient,
    parse_envelope,
    parse_group_update,
    rest_group_id,
)

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIX / name).read_text())


def test_parse_envelope_text():
    assert parse_envelope(fixture("data_message.json")) == Inbound(
        "+16175550100", "uuid-sender", "hi", 1730000000000, None
    )


@pytest.mark.parametrize(
    "name", ["receipt", "typing", "reaction", "sync_sent", "attachment_only", "edit"]
)
def test_parse_envelope_ignores_non_text(name):
    assert parse_envelope(fixture(f"{name}.json")) is None


G1 = "UiRIyG9CIM/7fvTRKAEQF6QwC7RTkH5qQEW3uiERjp8="
G1_REST = "group.VWlSSXlHOUNJTS83ZnZUUktBRVFGNlF3QzdSVGtINXFRRVczdWlFUmpwOD0="
G2 = "mUUpE/hoVDLAgHVFTB56rbPVQSqZc+rpMR0DAArT0SQ="


def test_parse_envelope_group_id():
    inb = parse_envelope(fixture("group_message.json"))
    assert inb == Inbound("+16175550100", "uuid-sender", "hi", 1730000000000, G1)
    assert inb.is_group is True


def test_parse_envelope_group_v2_id():
    inb = parse_envelope(fixture("group_v2_message.json"))
    assert inb is not None and inb.is_group is True and inb.group_id == "abc="


def test_parse_group_update():
    frame = fixture("group_update.json")
    assert parse_envelope(frame) is None
    assert parse_group_update(frame) == GroupUpdate("+16175550100", "uuid-keith", G1)
    for name in ["group_message", "data_message", "receipt", "typing"]:
        assert parse_group_update(fixture(f"{name}.json")) is None
    assert parse_group_update({"envelope": {"dataMessage": {"groupInfo": {"type": "UPDATE"}}}}) is None
    assert parse_group_update("junk") is None


def test_rest_group_id():
    # signal-cli-rest-api addresses a group as "group." + base64(internal id).
    assert rest_group_id(G1) == G1_REST


def test_parse_envelope_garbage():
    assert parse_envelope({}) is None
    assert parse_envelope({"envelope": {"dataMessage": {"message": ""}}}) is None


BASE = "http://signal:8080"


@respx.mock
async def test_send():
    route = respx.post(f"{BASE}/v2/send").respond(201, json={})
    await SignalClient(BASE, "+1bot").send(DirectRecipient("+1me"), "hello")
    assert json.loads(route.calls[0].request.content) == {
        "message": "hello",
        "number": "+1bot",
        "recipients": ["+1me"],
    }


@respx.mock
async def test_typing_on_off_methods():
    put = respx.put(f"{BASE}/v1/typing-indicator/+1bot").respond(204)
    delete = respx.delete(f"{BASE}/v1/typing-indicator/+1bot").respond(204)
    c = SignalClient(BASE, "+1bot")
    await c.typing(DirectRecipient("+1me"), True)
    await c.typing(DirectRecipient("+1me"), False)
    assert json.loads(put.calls[0].request.content) == {"recipient": "+1me"}
    assert json.loads(delete.calls[0].request.content) == {"recipient": "+1me"}


@respx.mock
async def test_typing_failure_swallowed():
    respx.put(f"{BASE}/v1/typing-indicator/+1bot").mock(side_effect=httpx.ConnectError("x"))
    respx.delete(f"{BASE}/v1/typing-indicator/+1bot").respond(500)
    c = SignalClient(BASE, "+1bot")
    await c.typing(DirectRecipient("+1me"), True)
    await c.typing(DirectRecipient("+1me"), False)


@respx.mock
async def test_send_to_group_uses_rest_group_id():
    route = respx.post(f"{BASE}/v2/send").respond(201, json={})
    await SignalClient(BASE, "+1bot").send(GroupRecipient(G1), "hello")
    assert json.loads(route.calls[0].request.content) == {
        "message": "hello",
        "number": "+1bot",
        "recipients": [G1_REST],
    }


async def test_send_to_group_without_id_refused():
    with pytest.raises(ValueError):
        await SignalClient(BASE, "+1bot").send(GroupRecipient(""), "x")


@respx.mock
async def test_group_typing():
    put = respx.put(f"{BASE}/v1/typing-indicator/+1bot").respond(204)
    delete = respx.delete(f"{BASE}/v1/typing-indicator/+1bot").respond(204)
    c = SignalClient(BASE, "+1bot")
    await c.typing(GroupRecipient(G1), True)
    await c.typing(GroupRecipient(G1), False)
    assert json.loads(put.calls[0].request.content) == {"recipient": G1_REST}
    assert json.loads(delete.calls[0].request.content) == {"recipient": G1_REST}


@respx.mock
async def test_list_groups_expanded():
    route = respx.get(f"{BASE}/v1/groups/+1bot", params={"expand": "true"}).respond(
        200, json=fixture("groups_expanded.json")
    )
    got = await SignalClient(BASE, "+1bot").list_groups()
    assert route.called
    assert got == [
        GroupInfo(
            G1,
            [MemberRef("+16175550100", "uuid-keith"), MemberRef(None, "uuid-alex"),
             MemberRef("+15553334444", "uuid-bot")],
            is_member=True,
            pending=[],
        ),
        GroupInfo(
            G2,
            [MemberRef("+16175550100", "uuid-keith"), MemberRef("+16175559999", "uuid-outsider"),
             MemberRef("+15553334444", "uuid-bot")],
            is_member=True,
            pending=[],
        ),
    ]


@respx.mock
async def test_list_groups_compact_strings():
    # Older servers (or expand ignored) list members as plain strings: a number, else a uuid.
    respx.get(f"{BASE}/v1/groups/+1bot").respond(200, json=fixture("groups_compact.json"))
    got = await SignalClient(BASE, "+1bot").list_groups()
    assert got == [
        GroupInfo(
            G1,
            [MemberRef("+16175550100", None), MemberRef(None, "uuid-alex"), MemberRef("+15553334444", None)],
            is_member=True,
            pending=[MemberRef(None, "uuid-invitee")],
        )
    ]


@respx.mock
async def test_list_groups_odd_entries_fail_closed():
    entry = {
        "internal_id": G1,
        "members": [{"number": "", "uuid": ""}, 42, None, {"number": "+16175550100"}],
        "pending_invites": None,
    }
    respx.get(f"{BASE}/v1/groups/+1bot").respond(200, json=[entry, "junk", {"members": []}])
    got = await SignalClient(BASE, "+1bot").list_groups()
    # Unidentifiable members stay in the list (as outsiders); missing `member` means not a member.
    assert got == [
        GroupInfo(
            G1,
            [MemberRef(None, None), MemberRef(None, None), MemberRef(None, None),
             MemberRef("+16175550100", None)],
            is_member=False,
            pending=[],
        )
    ]


@respx.mock
@pytest.mark.parametrize("resp", [httpx.Response(400, json={"error": "x"}), httpx.Response(200, json={"a": 1})])
async def test_list_groups_bad_response_raises(resp):
    respx.get(f"{BASE}/v1/groups/+1bot").mock(return_value=resp)
    with pytest.raises((httpx.HTTPStatusError, TypeError)):
        await SignalClient(BASE, "+1bot").list_groups()


@respx.mock
async def test_join_group():
    route = respx.post(f"{BASE}/v1/groups/+1bot/{G1_REST}/join").respond(204)
    await SignalClient(BASE, "+1bot").join_group(G1)
    assert route.called


def test_empty_group_info_is_a_group_not_a_dm():
    msg = {"envelope": {"sourceNumber": "+16175550100", "dataMessage": {"message": "hi", "groupInfo": {}}}}
    inb = parse_envelope(msg)
    assert inb is not None and inb.group_id == "" and inb.is_group is True


# A sender who hides their number: signal-cli sends sourceNumber null and puts the uuid in source.
HIDDEN = "37b3cefe-a96c-41e3-ac9b-ee4b2d8c9c08"


def test_hidden_number_sender_has_no_number():
    frame = {"envelope": {"source": HIDDEN, "sourceNumber": None, "sourceUuid": HIDDEN,
                          "dataMessage": {"timestamp": 1, "message": "Test"}}}
    assert parse_envelope(frame) == Inbound(None, HIDDEN, "Test", 1, None)


def test_hidden_number_group_update_has_no_number():
    frame = {"envelope": {"source": HIDDEN, "sourceNumber": None, "sourceUuid": HIDDEN,
                          "dataMessage": {"groupInfo": {"groupId": "g1", "type": "UPDATE"}}}}
    assert parse_group_update(frame) == GroupUpdate(None, HIDDEN, "g1")
