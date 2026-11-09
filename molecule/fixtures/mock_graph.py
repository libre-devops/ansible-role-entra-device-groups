#!/usr/bin/env python3
"""A stand-in for Microsoft Graph v1.0 and the Entra ID token endpoint, for the Molecule tests.

It serves only the calls the role makes, with the shapes Microsoft documents, and keeps
everything in memory:

  POST   /{tenant}/oauth2/v2.0/token               client credentials -> access_token
  GET    /v1.0/groups?$filter=displayName eq 'X'   groups by name
  GET    /v1.0/groups/{id}                         one group
  GET    /v1.0/groups/{id}/members                 members, two per page, with @odata.nextLink
  PATCH  /v1.0/groups/{id}                         members@odata.bind, at most 20
  POST   /v1.0/groups/{id}/members/$ref            add one member
  DELETE /v1.0/groups/{id}/members/{id}/$ref       remove one member
  GET    /v1.0/devices?$filter=...                 devices by displayName (and operatingSystem) or deviceId
  GET    /v1.0/devices/{id}                        one device

Like Microsoft Graph, it answers 400 when a member is added twice, 400 when more than 20
members are bound at once, and refuses query parameters on the members list (they need
advanced query parameters, which the role avoids). It also refuses a member removal without
/$ref, which would delete the object itself, and counts it as a failure.

Test helpers:
  GET    /mock/state      each group's members by name
  GET    /mock/stats      request counters
  POST   /mock/throttle   {"count": N}: answer the next N Graph requests with 429, Retry-After 1
  POST   /mock/shutdown   stop the server

Usage: mock_graph.py [--port 18944]
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

# Made-up credentials the tests sign in with. Nothing real.
TENANT = "molecule.example"
CLIENT_ID = "22222222-2222-4222-8222-222222222222"
CLIENT_SECRET = "molecule-client-secret"
TOKEN = "molecule-graph-token"
PAGE_SIZE = 2
BAD_SECRET = "AADSTS7000215: Invalid client secret provided."
ALREADY_MEMBER = (
    "One or more added object references already exist for the following modified properties: 'members'."
)
DEVICE = "#microsoft.graph.device"
USER = "#microsoft.graph.user"
GROUP = "#microsoft.graph.group"


def guid(prefix, number):
    return f"{prefix}-0000-4000-8000-{number:012d}"


def device(number, name, operating_system="Linux", last_sign_in="2026-11-01T00:00:00Z"):
    return {
        "@odata.type": DEVICE,
        "id": guid("dddddddd", number),
        "deviceId": guid("eeeeeeee", number),
        "displayName": name,
        "operatingSystem": operating_system,
        "approximateLastSignInDateTime": last_sign_in,
    }


def initial_state():
    devices = [
        device(1, "web01"),
        device(2, "web02"),
        device(3, "web03"),
        device(4, "web04"),
        device(5, "web05"),
        device(6, "web06"),
        device(7, "db01"),
        device(8, "db01", operating_system="Windows"),
        device(9, "old01"),
        device(10, "app01", last_sign_in="2026-01-01T00:00:00Z"),
        device(11, "app01", last_sign_in="2026-10-30T00:00:00Z"),
        device(12, "o'neil-lab01"),
        device(13, "db02"),
    ]
    devices += [device(100 + i, f"bulk{i:02d}") for i in range(1, 26)]
    users = [
        {"@odata.type": USER, "id": guid("cccccccc", 1), "displayName": "Alex Example"},
        {"@odata.type": USER, "id": guid("cccccccc", 2), "displayName": "Bea Example"},
    ]
    nested = {"@odata.type": GROUP, "id": guid("bbbbbbbb", 99), "displayName": "Nested group"}

    def group(number, name, members=(), group_types=(), security=True):
        return {
            "id": guid("aaaaaaaa", number),
            "displayName": name,
            "groupTypes": list(group_types),
            "securityEnabled": security,
            "mailEnabled": not security,
            "isAssignableToRole": False,
            "members": [m["id"] for m in members],
        }

    by_name = {d["displayName"]: d for d in devices}
    groups = [
        group(
            1,
            "Linux servers EDR",
            [users[0]] + [by_name[n] for n in ("web02", "web03", "web04", "web05", "web06")],
        ),
        group(2, "Linux servers RTP", [by_name["old01"], users[1], nested]),
        group(3, "Linux servers bulk"),
        group(4, "Linux servers dynamic", group_types=["DynamicMembership"]),
        group(5, "Team site", group_types=["Unified"], security=False),
        group(6, "Twin group"),
        group(7, "Twin group"),
    ]
    objects = {o["id"]: o for o in devices + users + [nested]}
    return {"groups": {g["id"]: g for g in groups}, "devices": devices, "objects": objects}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = initial_state()
        self.throttle = 0
        self.stats = {
            "tokens": 0,
            "bad_tokens": 0,
            "graph_requests": 0,
            "throttled": 0,
            "patches": 0,
            "single_adds": 0,
            "removals": 0,
            "removals_without_ref": 0,
            "rejected_queries": 0,
            "bound_per_patch": [],
        }


STATE = State()


def parse_filter(expression):
    """Turn "a eq 'x' and b eq 'y'" into {"a": "x", "b": "y"}, undoing doubled quotes."""
    clauses = {}
    for match in re.finditer(r"(\w+) eq '((?:[^']|'')*)'", expression):
        clauses[match.group(1)] = match.group(2).replace("''", "'")
    return clauses


class Handler(BaseHTTPRequestHandler):
    server_version = "MockGraph/1.0"

    def log_message(self, *_args):
        pass

    # Responses ------------------------------------------------------------------------------

    def send(self, status, body=None, headers=None):
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def error(self, status, code, message, headers=None):
        self.send(status, {"error": {"code": code, "message": message}}, headers)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    # Routing --------------------------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        self.route("GET")

    def do_POST(self):  # noqa: N802
        self.route("POST")

    def do_PATCH(self):  # noqa: N802
        self.route("PATCH")

    def do_DELETE(self):  # noqa: N802
        self.route("DELETE")

    def route(self, method):
        url = urlsplit(self.path)
        path = unquote(url.path)
        query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        with STATE.lock:
            if path.startswith("/mock/"):
                return self.mock(method, path)
            if method == "POST" and path == f"/{TENANT}/oauth2/v2.0/token":
                return self.token()
            if not path.startswith("/v1.0/"):
                return self.error(404, "NotFound", "No such endpoint.")
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self.error(401, "InvalidAuthenticationToken", "Access token is empty or invalid.")
            STATE.stats["graph_requests"] += 1
            if STATE.throttle > 0:
                STATE.throttle -= 1
                STATE.stats["throttled"] += 1
                return self.error(429, "TooManyRequests", "Please retry again later.", {"Retry-After": "1"})
            return self.graph(method, path[len("/v1.0") :], query)

    def mock(self, method, path):
        if path == "/mock/stats":
            return self.send(200, STATE.stats)
        if path == "/mock/state":
            objects = STATE.data["objects"]

            def is_device(object_id):
                return objects[object_id]["@odata.type"] == DEVICE

            groups = [
                {
                    "id": gid,
                    "displayName": g["displayName"],
                    "devices": sorted(objects[m]["displayName"] for m in g["members"] if is_device(m)),
                    "others": sorted(objects[m]["displayName"] for m in g["members"] if not is_device(m)),
                    "device_ids": sorted(m for m in g["members"] if is_device(m)),
                }
                for gid, g in STATE.data["groups"].items()
            ]
            return self.send(200, {"groups": groups})
        if method == "POST" and path == "/mock/throttle":
            STATE.throttle = int(json.loads(self.body() or b"{}").get("count", 1))
            return self.send(200, {"throttle": STATE.throttle})
        if method == "POST" and path == "/mock/shutdown":
            self.send(200, {"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        return self.error(404, "NotFound", "No such mock endpoint.")

    def token(self):
        form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()}
        STATE.stats["tokens"] += 1
        if form.get("grant_type") != "client_credentials" or not form.get("scope", "").endswith("/.default"):
            STATE.stats["bad_tokens"] += 1
            return self.send(400, {"error": "invalid_request", "error_description": "AADSTS900144: bad."})
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            STATE.stats["bad_tokens"] += 1
            return self.send(
                401,
                {"error": "invalid_client", "error_description": BAD_SECRET},
            )
        return self.send(200, {"token_type": "Bearer", "expires_in": 3599, "access_token": TOKEN})

    # Microsoft Graph ------------------------------------------------------------------------

    def graph(self, method, path, query):
        data = STATE.data
        parts = [p for p in path.split("/") if p]

        if method == "GET" and parts == ["groups"]:
            wanted = parse_filter(query.get("$filter", ""))
            name = wanted.get("displayName")
            found = [self.public_group(g) for g in data["groups"].values() if g["displayName"] == name]
            return self.send(200, {"value": found})

        if parts[:1] == ["groups"] and len(parts) >= 2:
            group = data["groups"].get(parts[1])
            if group is None:
                return self.error(404, "Request_ResourceNotFound", f"Resource '{parts[1]}' does not exist.")
            if method == "GET" and len(parts) == 2:
                return self.send(200, self.public_group(group))
            if method == "GET" and parts[2:] == ["members"]:
                return self.list_members(group, query)
            if method == "PATCH" and len(parts) == 2:
                bind = json.loads(self.body() or b"{}").get("members@odata.bind", [])
                return self.add(group, [b.rsplit("/", 1)[-1] for b in bind], batch=True)
            if method == "POST" and parts[2:] == ["members", "$ref"]:
                reference = json.loads(self.body() or b"{}").get("@odata.id", "")
                return self.add(group, [reference.rsplit("/", 1)[-1]], batch=False)
            if method == "DELETE" and len(parts) >= 4 and parts[2] == "members":
                if parts[4:] != ["$ref"]:
                    STATE.stats["removals_without_ref"] += 1
                    return self.error(500, "MockRefused", "Removal without /$ref would delete the object.")
                if parts[3] not in group["members"]:
                    return self.error(404, "Request_ResourceNotFound", "Not a member.")
                group["members"].remove(parts[3])
                STATE.stats["removals"] += 1
                return self.send(204)

        if method == "GET" and parts == ["devices"]:
            wanted = parse_filter(query.get("$filter", ""))
            found = [
                self.public_device(d)
                for d in data["devices"]
                if all(d.get(k) == v for k, v in wanted.items()) and wanted
            ]
            return self.send(200, {"value": found})

        if method == "GET" and parts[:1] == ["devices"] and len(parts) == 2:
            match = [d for d in data["devices"] if d["id"] == parts[1]]
            if not match:
                return self.error(404, "Request_ResourceNotFound", f"Resource '{parts[1]}' does not exist.")
            return self.send(200, self.public_device(match[0]))

        return self.error(400, "BadRequest", f"The mock does not serve {method} {path}.")

    def list_members(self, group, query):
        extra = set(query) - {"$skiptoken"}
        if extra:
            STATE.stats["rejected_queries"] += 1
            return self.error(
                400, "Request_UnsupportedQuery", f"Needs advanced query parameters: {sorted(extra)}"
            )
        start = int(query.get("$skiptoken") or 0)
        members = [STATE.data["objects"][m] for m in group["members"]]
        keep = ("@odata.type", "id", "displayName")
        page = [{k: v for k, v in m.items() if k in keep} for m in members[start : start + PAGE_SIZE]]
        body = {"value": page}
        if start + PAGE_SIZE < len(members):
            host = self.headers.get("Host")
            next_page = f"/v1.0/groups/{group['id']}/members?$skiptoken={start + PAGE_SIZE}"
            body["@odata.nextLink"] = f"http://{host}{next_page}"
        return self.send(200, body)

    def add(self, group, ids, batch):
        if "DynamicMembership" in group["groupTypes"]:
            return self.error(400, "Request_BadRequest", "Cannot add members to a dynamic group.")
        if batch and len(ids) > 20:
            return self.error(400, "Request_BadRequest", "At most 20 members can be added at once.")
        for object_id in ids:
            if object_id not in STATE.data["objects"]:
                return self.error(404, "Request_ResourceNotFound", f"Resource '{object_id}' does not exist.")
            if object_id in group["members"]:
                return self.error(
                    400,
                    "Request_BadRequest",
                    ALREADY_MEMBER,
                )
        group["members"].extend(ids)
        if batch:
            STATE.stats["patches"] += 1
            STATE.stats["bound_per_patch"].append(len(ids))
        else:
            STATE.stats["single_adds"] += 1
        return self.send(204)

    @staticmethod
    def public_group(group):
        return {k: v for k, v in group.items() if k != "members"}

    @staticmethod
    def public_device(item):
        return {k: v for k, v in item.items() if k != "@odata.type"}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18944)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
