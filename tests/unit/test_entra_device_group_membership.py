"""Unit tests for library/entra_device_group_membership.py."""

import io
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from urllib.error import HTTPError

import entra_device_group_membership as m
import pytest

GROUP = "11111111-1111-4111-8111-111111111111"
D1 = "aaaaaaaa-0000-4000-8000-000000000001"
D2 = "aaaaaaaa-0000-4000-8000-000000000002"
D3 = "aaaaaaaa-0000-4000-8000-000000000003"
ROOT = "https://graph.example.test"


# --- helpers ---------------------------------------------------------------------------------


class Response:
    def __init__(self, body=None):
        self.body = b"" if body is None else json.dumps(body).encode()

    def read(self):
        return self.body


def http_error(code, body=None, headers=None):
    raw = json.dumps(body or {}).encode()
    return HTTPError("https://graph.example.test/x", code, "error", headers or {}, io.BytesIO(raw))


class FakeOpener:
    """Returns or raises the queued results in order, and records every call."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, method=None, data=None, headers=None, **_kwargs):
        self.calls.append({"url": url, "method": method, "body": json.loads(data) if data else None})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return Response(result)


def client(opener, sleeps=None):
    recorded = sleeps if sleeps is not None else []
    return m.GraphClient(ROOT, "token", opener=opener, sleep=recorded.append, max_retries=3)


# --- pure helpers ----------------------------------------------------------------------------


def test_odata_quote_doubles_single_quotes():
    assert m.odata_quote("web01") == "'web01'"
    assert m.odata_quote("o'neil's box") == "'o''neil''s box'"


def test_is_guid():
    assert m.is_guid(D1)
    assert not m.is_guid("web01")
    assert not m.is_guid(D1 + "/../x")


@pytest.mark.parametrize(("count", "sizes"), [(0, []), (5, [5]), (20, [20]), (45, [20, 20, 5])])
def test_chunked(count, sizes):
    assert [len(c) for c in m.chunked(list(range(count)), 20)] == sizes


def test_chunked_rejects_zero():
    with pytest.raises(ValueError):
        m.chunked([1], 0)


def test_retry_after_seconds():
    assert m.retry_after_seconds({"Retry-After": "7"}, 0) == 7
    assert m.retry_after_seconds({"Retry-After": "500"}, 0) == 120
    assert m.retry_after_seconds({}, 0) == 1
    assert m.retry_after_seconds(None, 3) == 8
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    assert 25 <= m.retry_after_seconds({"Retry-After": when}, 0) <= 30


OLD = {"id": D1, "displayName": "app01", "approximateLastSignInDateTime": "2026-01-01T00:00:00Z"}
NEW = {"id": D2, "displayName": "app01", "approximateLastSignInDateTime": "2026-11-01T00:00:00Z"}
NEVER = {"id": D3, "displayName": "app01", "approximateLastSignInDateTime": None}


def test_pick_devices_single_is_not_ambiguous():
    assert m.pick_devices("app01", [OLD], "fail") == ([OLD], None)


def test_pick_devices_newest():
    selected, info = m.pick_devices("app01", [OLD, NEVER, NEW], "newest")
    assert selected == [NEW]
    assert info["used"] == [D2]
    assert len(info["matches"]) == 3


def test_pick_devices_all():
    selected, _ = m.pick_devices("app01", [OLD, NEW], "all")
    assert selected == [OLD, NEW]


def test_pick_devices_fail():
    with pytest.raises(ValueError, match="matches 2 devices"):
        m.pick_devices("app01", [OLD, NEW], "fail")


def test_compute_plan_add_mode_only_adds():
    current = {D1: "web01", D3: "old01"}
    desired = {D1: "web01", D2: "web02"}
    add, remove, unchanged = m.compute_plan(current, desired, {}, "add")
    assert [d["id"] for d in add] == [D2]
    assert remove == []
    assert [d["id"] for d in unchanged] == [D1]


def test_compute_plan_exact_mode_removes_unlisted():
    current = {D1: "web01", D3: "old01"}
    desired = {D1: "web01", D2: "web02"}
    add, remove, _ = m.compute_plan(current, desired, {}, "exact")
    assert [d["id"] for d in add] == [D2]
    assert [d["id"] for d in remove] == [D3]


def test_compute_plan_explicit_remove_only_removes_members():
    current = {D1: "web01"}
    add, remove, _ = m.compute_plan(current, {}, {D1: "web01", D2: "web02"}, "add")
    assert add == []
    assert [d["id"] for d in remove] == [D1]


def test_compute_plan_conflict():
    with pytest.raises(ValueError, match="both wanted and to be removed"):
        m.compute_plan({}, {D1: "web01"}, {D1: "web01"}, "add")


def test_compute_plan_nothing_to_do():
    assert m.compute_plan({D1: "web01"}, {D1: "web01"}, {}, "exact") == (
        [],
        [],
        [{"id": D1, "displayName": "web01"}],
    )


def test_device_members_ignores_users_and_groups():
    members = [
        {"@odata.type": "#microsoft.graph.device", "id": D1, "displayName": "web01"},
        {"@odata.type": "#microsoft.graph.user", "id": D2, "displayName": "A User"},
        {"@odata.type": "#microsoft.graph.group", "id": D3, "displayName": "Nested"},
    ]
    assert m.device_members(members) == {D1: "web01"}


def test_removal_path_always_ends_in_ref():
    assert m.removal_path(GROUP, D1) == f"/groups/{GROUP}/members/{D1}/$ref"


@pytest.mark.parametrize("bad", ["../devices/x", "web01", D1 + "?x=1", ""])
def test_removal_path_refuses_non_guids(bad):
    with pytest.raises(ValueError):
        m.removal_path(GROUP, bad)


def test_merge_entries_merges_the_same_group_case_insensitively():
    merged = m.merge_entries(
        [
            {"group": "Linux EDR", "devices": ["web01"]},
            {"group": "linux edr", "devices": ["web02", "web01"], "remove": ["old01"]},
        ]
    )
    assert merged == [
        {
            "group": "Linux EDR",
            "devices": ["web01", "web02"],
            "remove": ["old01"],
            "mode": "add",
            "operating_system": None,
        }
    ]


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([{"group": "G", "mode": "add"}, {"group": "G", "mode": "exact"}], "both add and exact"),
        ([{"group": "G", "devices": "web01"}], "must be a list"),
        ([{"group": "G", "devices": [""]}], "must be a list"),
        ([{"devices": ["web01"]}], "has no group"),
        ([{"group": "G", "members": ["web01"]}], "unknown keys"),
        ([{"group": "G", "mode": "replace"}], "Use add or exact"),
        (["G"], "not a mapping"),
    ],
)
def test_merge_entries_rejects_bad_input(entries, message):
    with pytest.raises(ValueError, match=message):
        m.merge_entries(entries)


@pytest.mark.parametrize(
    ("group", "message"),
    [
        ({"displayName": "D", "groupTypes": ["DynamicMembership"], "securityEnabled": True}, "dynamic"),
        ({"displayName": "U", "groupTypes": ["Unified"], "securityEnabled": False}, "Microsoft 365"),
        ({"displayName": "M", "groupTypes": [], "securityEnabled": False}, "not a security group"),
    ],
)
def test_check_group_refuses(group, message):
    with pytest.raises(m.GraphError, match=message):
        m.check_group(group)


# --- Graph client ----------------------------------------------------------------------------


def test_get_all_follows_next_link():
    opener = FakeOpener(
        {"value": [{"id": "1"}], "@odata.nextLink": ROOT + "/v1.0/groups?$skiptoken=a"},
        {"value": [{"id": "2"}], "@odata.nextLink": ROOT + "/v1.0/groups?$skiptoken=b"},
        {"value": [{"id": "3"}]},
    )
    assert [i["id"] for i in client(opener).get_all("/groups")] == ["1", "2", "3"]
    assert opener.calls[1]["url"].endswith("$skiptoken=a")


def test_next_link_to_another_host_is_refused():
    opener = FakeOpener({"value": [], "@odata.nextLink": "https://elsewhere.example.test/v1.0/x"})
    with pytest.raises(m.GraphError, match="Refusing to send the token"):
        client(opener).get_all("/groups")


def test_request_retries_throttling_with_retry_after():
    sleeps = []
    opener = FakeOpener(http_error(429, headers={"Retry-After": "3"}), http_error(503), {"value": []})
    assert client(opener, sleeps).request("GET", "/devices") == {"value": []}
    assert sleeps == [3, 2]


def test_request_gives_up_after_max_retries():
    opener = FakeOpener(*[http_error(429, headers={"Retry-After": "1"})] * 4)
    with pytest.raises(m.GraphError) as raised:
        client(opener).request("GET", "/devices")
    assert raised.value.status == 429
    assert len(opener.calls) == 4


def test_request_does_not_retry_other_errors():
    opener = FakeOpener(http_error(403, {"error": {"message": "Insufficient privileges"}}))
    with pytest.raises(m.GraphError, match="Insufficient privileges"):
        client(opener).request("GET", "/devices")
    assert len(opener.calls) == 1


def test_request_retries_replication_delay_only_when_asked():
    body = {
        "error": {"message": "The source resource object or one of the objects being referenced don't exist."}
    }
    sleeps = []
    opener = FakeOpener(http_error(400, body), None)
    client(opener, sleeps).request("PATCH", "/groups/x", {"a": 1}, retry_replication=True)
    assert sleeps == [2]
    with pytest.raises(m.GraphError):
        client(FakeOpener(http_error(400, body))).request("PATCH", "/groups/x", {"a": 1})


def test_add_members_batches_of_twenty():
    ids = [f"aaaaaaaa-0000-4000-8000-{i:012d}" for i in range(45)]
    opener = FakeOpener(None, None, None)
    assert client(opener).add_members(GROUP, ids) == []
    sizes = [len(c["body"]["members@odata.bind"]) for c in opener.calls]
    assert sizes == [20, 20, 5]
    assert all(c["method"] == "PATCH" for c in opener.calls)
    assert opener.calls[0]["body"]["members@odata.bind"][0] == f"{ROOT}/v1.0/directoryObjects/{ids[0]}"


def test_add_members_falls_back_to_single_adds_when_a_device_is_already_a_member():
    message = (
        "One or more added object references already exist for the following modified properties: 'members'."
    )
    already = {"error": {"message": message}}
    opener = FakeOpener(http_error(400, already), http_error(400, already), None)
    assert client(opener).add_members(GROUP, [D1, D2]) == [D1]
    assert [c["method"] for c in opener.calls] == ["PATCH", "POST", "POST"]
    assert opener.calls[2]["url"].endswith(f"/groups/{GROUP}/members/$ref")


def test_find_devices_by_name_escapes_and_filters_operating_system():
    opener = FakeOpener({"value": [{"id": D1, "displayName": "o'neil", "operatingSystem": "Linux"}]})
    found = client(opener).find_devices("o'neil", "Linux")
    assert found[0]["id"] == D1
    url = opener.calls[0]["url"]
    assert "displayName%20eq%20%27o%27%27neil%27" in url
    assert "operatingSystem%20eq%20%27Linux%27" in url


def test_find_devices_by_guid_falls_back_to_device_id():
    opener = FakeOpener(http_error(404), {"value": [{"id": D2, "deviceId": D1, "displayName": "web01"}]})
    assert client(opener).find_devices(D1)[0]["id"] == D2
    assert "deviceId%20eq" in opener.calls[1]["url"]


def test_resolve_group_by_name_must_be_unique():
    opener = FakeOpener({"value": [{"id": "1"}, {"id": "2"}]})
    with pytest.raises(m.GraphError, match="2 groups are called"):
        client(opener).resolve_group("Linux EDR")
    with pytest.raises(m.GraphError, match="No group called"):
        client(FakeOpener({"value": []})).resolve_group("Missing")


# --- one group end to end, against a fake client ---------------------------------------------


class FakeClient:
    """In-memory stand-in for GraphClient."""

    def __init__(self, devices, members):
        self.devices = devices
        self.members = dict(members)
        self.writes = []

    def resolve_group(self, reference):
        return {"id": GROUP, "displayName": reference, "groupTypes": [], "securityEnabled": True}

    def group_members(self, _group_id):
        return dict(self.members)

    def find_devices(self, reference, operating_system=""):
        return [d for d in self.devices if d["displayName"] == reference or d["id"] == reference]

    def add_members(self, _group_id, ids, _batch_size=20):
        self.writes.append(("add", list(ids)))
        return []

    def remove_member(self, _group_id, device_id):
        self.writes.append(("remove", device_id))


DEVICES = [
    {"id": D1, "displayName": "web01"},
    {"id": D2, "displayName": "web02"},
    {"id": D3, "displayName": "old01"},
]
PARAMS = {"operating_system": "", "duplicates": "newest", "missing": "warn", "batch_size": 20}


def entry(**kwargs):
    base = {"group": "Linux EDR", "devices": [], "remove": [], "mode": "add", "operating_system": None}
    base.update(kwargs)
    return base


def test_apply_entry_check_mode_reports_and_writes_nothing():
    fake = FakeClient(DEVICES, {D3: "old01"})
    result = m.apply_entry(fake, entry(devices=["web01"], mode="exact"), PARAMS, True, lambda _m: None)
    assert result["changed"]
    assert [d["displayName"] for d in result["added"]] == ["web01"]
    assert [d["displayName"] for d in result["removed"]] == ["old01"]
    assert fake.writes == []


def test_apply_entry_applies_and_reports_missing():
    warnings = []
    fake = FakeClient(DEVICES, {})
    result = m.apply_entry(fake, entry(devices=["web01", "ghost01"]), PARAMS, False, warnings.append)
    assert fake.writes == [("add", [D1])]
    assert result["not_found"] == ["ghost01"]
    assert "ghost01" in warnings[0]


def test_apply_entry_missing_fail_stops_before_writing():
    fake = FakeClient(DEVICES, {})
    with pytest.raises(m.GraphError, match="No device matches ghost01"):
        m.apply_entry(fake, entry(devices=["web01", "ghost01"]), dict(PARAMS, missing="fail"), False, print)
    assert fake.writes == []


def test_apply_entry_is_idempotent():
    fake = FakeClient(DEVICES, {D1: "web01"})
    result = m.apply_entry(fake, entry(devices=["web01"], remove=["web02"]), PARAMS, False, print)
    assert not result["changed"]
    assert fake.writes == []
