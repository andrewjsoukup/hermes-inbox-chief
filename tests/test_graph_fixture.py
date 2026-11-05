import pytest

from inbox_chief.graph import FixtureGraphClient, GraphError, HermesGraphClient, address_of


def test_reads(client):
    inbox = client.list_messages("inbox")
    assert [m["id"] for m in inbox] == ["m003", "m001"]  # newest first
    assert client.get_message("nope") is None
    conv = client.conversation("c-kestrel-ask")
    assert [m["id"] for m in conv] == ["m002", "m003"]  # oldest first, across folders
    assert "attachments" not in conv[1]  # attachments only via list_attachments
    assert len(client.list_attachments("m003")) == 3
    assert client.search_sent("KP-450 cut sheet")[0]["id"] == "m002"


def test_reply_all_draft_includes_cc_not_me(client):
    d = client.create_reply_draft("m001", reply_all=True)
    rcpts = {address_of(r) for r in d["toRecipients"] + d["ccRecipients"]}
    assert rcpts == {"dana.okafor@lakeshorecreamery.example", "lee.marsh@lakeshorecreamery.example"}
    assert d["isDraft"] and d["conversationId"] == "c-lakeshore-pump"
    d2 = client.create_reply_draft("m001", reply_all=False)
    assert [address_of(r) for r in d2["toRecipients"]] == ["dana.okafor@lakeshorecreamery.example"]
    assert d2["ccRecipients"] == []


def test_send_moves_draft_and_logs(client):
    d = client.create_draft({"subject": "x", "toRecipients": [{"emailAddress": {"address": "a@b.example"}}]})
    client.send_draft(d["id"])
    assert client.get_message(d["id"]) is None
    assert len(client.sends()) == 1
    with pytest.raises(GraphError):
        client.send_draft(d["id"])  # already sent


def test_no_send_client_refuses(fixture_data):
    c = FixtureGraphClient(fixture_data, allow_send=False)
    with pytest.raises(AssertionError):
        c.send_draft("m004")


def test_fixture_is_isolated_copy(fixture_data):
    c = FixtureGraphClient(fixture_data)
    c.update_message("m004", {"subject": "changed"})
    assert fixture_data["messages"][3]["subject"] == "Following up on samples"


class _Resp:
    def __init__(self, status, body=b"{}"):
        self.status, self.body = status, body

    @property
    def text(self):
        return self.body.decode()

    def json(self):
        import json
        return json.loads(self.body)


def test_hermes_client_uses_declared_oauth_id_and_graph_host():
    calls = []

    def fake(provider, method, url, *, json=None, headers=None, timeout=30):
        calls.append((provider, method, url))
        if "/messages/gone" in url:
            return _Resp(404, b'{"error":{"code":"ErrorItemNotFound"}}')
        return _Resp(200, b'{"mail":"Pat@Northwind-Demo.example","displayName":"Pat"}')

    c = HermesGraphClient(request=fake)
    assert c.me()["address"] == "pat@northwind-demo.example"
    assert c.get_message("gone") is None
    assert all(p == "microsoft" and u.startswith("https://graph.microsoft.com/v1.0/") for p, _, u in calls)


def test_hermes_client_reply_all_endpoint():
    seen = []
    c = HermesGraphClient(request=lambda p, m, u, **k: seen.append(u) or _Resp(201, b'{"id":"d1"}'))
    c.create_reply_draft("abc")
    c.create_reply_draft("abc", reply_all=False)
    assert seen[0].endswith("/abc/createReplyAll") and seen[1].endswith("/abc/createReply")
