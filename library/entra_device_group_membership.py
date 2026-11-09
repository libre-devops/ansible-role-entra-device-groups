#!/usr/bin/python
# Copyright (c) 2026 Craig Thacker (Libre DevOps)
# MIT License (see LICENSE)

"""Make the device members of Microsoft Entra ID groups match a desired state."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: entra_device_group_membership
short_description: Make the device members of Microsoft Entra ID groups match a desired state
description:
  - Resolves groups and devices by name or ID through Microsoft Graph v1.0, reads each group's
    current device members, and adds or removes devices so the membership matches the
    desired state. Only device members are ever changed. Users, groups and other members are
    left alone, even in C(exact) mode.
  - Adds up to 20 devices per request with C(members@odata.bind), removes with
    C(DELETE /groups/{id}/members/{id}/$ref), follows C(@odata.nextLink) paging, and retries
    throttled (429) and unavailable (503, 504) responses, waiting for C(Retry-After).
  - Supports check mode (reports the plan and changes nothing) and diff mode.
  - Uses only the Python standard library and Ansible's own HTTP helpers.
version_added: "1.0.0"
author:
  - Craig Thacker
options:
  access_token:
    description: A Microsoft Graph access token.
    type: str
    required: true
  graph_url:
    description: Microsoft Graph root, without the version, for example https://graph.microsoft.com.
    type: str
    default: https://graph.microsoft.com
  groups:
    description:
      - The desired state, one entry per group.
    type: list
    elements: dict
    required: true
    suboptions:
      group:
        description: Group display name (exact match) or object ID.
        type: str
        required: true
      devices:
        description: Devices that must be members, by display name (exact match) or ID.
        type: list
        elements: str
        default: []
      mode:
        description:
          - C(add) only adds the listed devices. C(exact) also removes every device member that
            is not listed.
        type: str
        choices: [add, exact]
        default: add
      remove:
        description: Devices that must not be members, by display name or ID.
        type: list
        elements: str
        default: []
      operating_system:
        description:
          - Only match devices with this operatingSystem, for example Linux.
          - Overrides I(operating_system) for this group.
        type: str
  operating_system:
    description: Only match devices with this operatingSystem. Empty matches any.
    type: str
    default: ""
  duplicates:
    description:
      - What to do when a display name matches more than one device. C(newest) picks the one
        with the latest approximateLastSignInDateTime, C(all) uses every match, C(fail) stops.
    type: str
    choices: [newest, all, fail]
    default: newest
  missing:
    description:
      - What to do when a device in I(devices) does not exist.
      - Devices in I(remove) that do not exist are always only reported.
    type: str
    choices: [warn, fail]
    default: warn
  batch_size:
    description: Devices added per request. Microsoft Graph accepts at most 20.
    type: int
    default: 20
  max_retries:
    description: Retries for each request that is throttled (429) or unavailable (503, 504).
    type: int
    default: 5
  timeout:
    description: Seconds to wait for each HTTP response.
    type: int
    default: 60
  validate_certs:
    description: Check TLS certificates.
    type: bool
    default: true
notes:
  - Group IDs and device object IDs are GUIDs. A GUID in I(devices) or I(remove) is tried as a
    device object ID first, then as the Entra device ID (deviceId).
  - Needs the Microsoft Graph application permissions GroupMember.ReadWrite.All and
    Device.ReadWrite.All (Microsoft's least privileged pair for adding device members).
    Role-assignable groups also need RoleManagement.ReadWrite.Directory.
seealso:
  - name: Add members (Microsoft Graph)
    link: https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0
  - name: Remove member (Microsoft Graph)
    link: https://learn.microsoft.com/en-us/graph/api/group-delete-members?view=graph-rest-1.0
"""

EXAMPLES = r"""
- name: Put two servers in a policy group, and keep a second group to exactly these servers
  entra_device_group_membership:
    access_token: "{{ graph_token }}"
    operating_system: Linux
    groups:
      - group: Linux servers - EDR
        devices: [web01.corp.example, web02.corp.example]
      - group: Linux servers - real-time protection
        mode: exact
        devices: [web01.corp.example]
      - group: Linux servers - pilot
        remove: [web02.corp.example]
"""

RETURN = r"""
results:
  description: One entry per group.
  returned: always
  type: list
  elements: dict
  contains:
    group:
      description: The group as given.
      type: str
    group_id:
      description: The group's object ID.
      type: str
    group_name:
      description: The group's display name.
      type: str
    mode:
      description: add or exact.
      type: str
    added:
      description: Devices added (or, in check mode, that would be added), each with id and displayName.
      type: list
    removed:
      description: Devices removed (or that would be removed), each with id and displayName.
      type: list
    unchanged:
      description: Desired devices that were already members, each with id and displayName.
      type: list
    not_found:
      description: Device names or IDs that matched no device.
      type: list
    ambiguous:
      description: Device names that matched more than one device, with the matches and the ones used.
      type: list
    changed:
      description: Whether the group's membership changed (or would change).
      type: bool
"""

import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from email.utils import parsedate_to_datetime  # noqa: E402
from urllib.error import HTTPError, URLError  # noqa: E402
from urllib.parse import quote, urlsplit  # noqa: E402

from ansible.module_utils.basic import AnsibleModule  # noqa: E402
from ansible.module_utils.urls import open_url  # noqa: E402

API_VERSION = "v1.0"
DEVICE_TYPE = "#microsoft.graph.device"
MAX_BATCH = 20
RETRY_STATUSES = (429, 503, 504)
DEVICE_FIELDS = "id,deviceId,displayName,operatingSystem,approximateLastSignInDateTime"
GROUP_FIELDS = "id,displayName,groupTypes,securityEnabled,mailEnabled,isAssignableToRole"
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
REPLICATION_MESSAGE = "don't exist"
ALREADY_MEMBER_MESSAGE = "already exist"


class GraphError(Exception):
    """A Microsoft Graph request that failed for good."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


# ---------------------------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------------------------


def odata_quote(value):
    """Quote a string for an OData $filter: wrap in single quotes, double any inside."""
    return "'" + str(value).replace("'", "''") + "'"


def is_guid(value):
    return bool(GUID.match(str(value).strip()))


def chunked(items, size):
    """Split a list into lists of at most size items."""
    if size < 1:
        raise ValueError("size must be at least 1")
    return [items[i : i + size] for i in range(0, len(items), size)]


def retry_after_seconds(headers, attempt, cap=120):
    """Seconds to wait before a retry: Retry-After (seconds or an HTTP date) or exponential backoff."""
    value = None
    if headers is not None:
        value = headers.get("Retry-After") or headers.get("retry-after")
    if value:
        value = str(value).strip()
        if value.isdigit():
            return min(int(value), cap)
        try:
            when = parsedate_to_datetime(value)
            return max(0, min(int(when.timestamp() - time.time()), cap))
        except (TypeError, ValueError):
            pass
    return min(2**attempt, cap)


def pick_devices(reference, matches, policy):
    """Choose which of the devices matching one reference to use.

    Returns (selected, ambiguous) where ambiguous is None or a dict describing the duplicates.
    Raises ValueError when policy is fail and there are duplicates.
    """
    if len(matches) <= 1:
        return list(matches), None
    if policy == "fail":
        raise ValueError(
            f"'{reference}' matches {len(matches)} devices ({', '.join(m.get('id', '') for m in matches)}). "
            "Use a device ID, or set duplicates to newest or all."
        )
    if policy == "all":
        selected = list(matches)
    else:
        selected = [max(matches, key=lambda m: m.get("approximateLastSignInDateTime") or "")]
    ambiguous = {
        "reference": reference,
        "matches": [
            {
                "id": m.get("id"),
                "displayName": m.get("displayName"),
                "approximateLastSignInDateTime": m.get("approximateLastSignInDateTime"),
            }
            for m in matches
        ],
        "used": [m.get("id") for m in selected],
    }
    return selected, ambiguous


def compute_plan(current, desired, remove, mode):
    """Work out the changes for one group.

    current, desired and remove map device object ID to display name. current holds only the
    group's device members. Returns (to_add, to_remove, unchanged), each a sorted list of
    {"id", "displayName"} dicts.
    """
    conflict = set(desired) & set(remove)
    if conflict:
        raise ValueError(
            "These devices are both wanted and to be removed: {}".format(
                ", ".join(sorted(desired.get(i) or i for i in conflict))
            )
        )
    to_add_ids = set(desired) - set(current)
    to_remove_ids = set(remove) & set(current)
    if mode == "exact":
        to_remove_ids |= set(current) - set(desired)
    unchanged_ids = set(desired) & set(current)

    def entries(ids, names):
        return sorted(
            ({"id": i, "displayName": names.get(i)} for i in ids),
            key=lambda e: (e["displayName"] or "", e["id"]),
        )

    names = dict(current)
    names.update(desired)
    names.update(remove)
    return entries(to_add_ids, names), entries(to_remove_ids, names), entries(unchanged_ids, names)


def removal_path(group_id, device_id):
    """The path that removes a member from a group without deleting the member object.

    Microsoft Graph deletes the object itself when /$ref is missing, so the IDs are checked to
    be GUIDs (no slashes or query strings can slip in) and the path always ends in /$ref.
    """
    if not (is_guid(group_id) and is_guid(device_id)):
        raise ValueError(f"Group and member IDs must be GUIDs: {group_id!r}, {device_id!r}")
    return f"/groups/{group_id}/members/{device_id}/$ref"


def device_members(members):
    """Keep only the device members of a group's members list, as {id: displayName}."""
    return {
        m["id"]: m.get("displayName") for m in members if m.get("@odata.type") == DEVICE_TYPE and m.get("id")
    }


def merge_entries(entries):
    """Merge entries for the same group. Raises ValueError on conflicting modes or bad entries."""
    merged = {}
    order = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry {index + 1} is not a mapping: {entry!r}")
        group = str(entry.get("group") or "").strip()
        if not group:
            raise ValueError(f"Entry {index + 1} has no group.")
        unknown = set(entry) - {"group", "devices", "mode", "remove", "operating_system"}
        if unknown:
            raise ValueError("Entry for '{}' has unknown keys: {}".format(group, ", ".join(sorted(unknown))))
        mode = entry.get("mode") or "add"
        if mode not in ("add", "exact"):
            raise ValueError(f"Entry for '{group}' has mode '{mode}'. Use add or exact.")
        for key in ("devices", "remove"):
            value = entry.get(key) or []
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                raise ValueError(f"Entry for '{group}': {key} must be a list of device names or IDs.")
        key = group.lower()
        if key not in merged:
            merged[key] = {
                "group": group,
                "devices": [],
                "remove": [],
                "mode": mode,
                "operating_system": entry.get("operating_system"),
            }
            order.append(key)
        target = merged[key]
        if target["mode"] != mode:
            raise ValueError(f"Group '{group}' is listed with both add and exact mode.")
        if entry.get("operating_system") and target["operating_system"] not in (
            None,
            entry["operating_system"],
        ):
            raise ValueError(f"Group '{group}' is listed with different operating systems.")
        target["operating_system"] = target["operating_system"] or entry.get("operating_system")
        for name in ("devices", "remove"):
            for device in entry.get(name) or []:
                if device.strip() not in target[name]:
                    target[name].append(device.strip())
    return [merged[k] for k in order]


# ---------------------------------------------------------------------------------------------
# Microsoft Graph client
# ---------------------------------------------------------------------------------------------


class GraphClient:
    """A small Microsoft Graph client with retries and paging."""

    def __init__(
        self, graph_url, token, timeout=60, validate_certs=True, max_retries=5, opener=None, sleep=None
    ):
        self.root = graph_url.rstrip("/")
        self.base = f"{self.root}/{API_VERSION}"
        self.token = token
        self.timeout = timeout
        self.validate_certs = validate_certs
        self.max_retries = max_retries
        self.opener = opener or open_url
        self.sleep = sleep or time.sleep
        self.requests = 0

    def url(self, path):
        return path if path.startswith("http") else self.base + path

    def _same_host(self, url):
        a, b = urlsplit(url), urlsplit(self.root)
        return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())

    def request(self, method, path, body=None, retry_replication=False):
        """Send a request, retrying 429, 503 and 504. Returns the parsed JSON body or None."""
        url = self.url(path)
        if not self._same_host(url):
            raise GraphError(f"Refusing to send the token to {url}, which is not {self.root}.")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        attempt = 0
        replication_attempts = 0
        while True:
            self.requests += 1
            try:
                response = self.opener(
                    url,
                    method=method,
                    data=data,
                    headers=headers,
                    timeout=self.timeout,
                    validate_certs=self.validate_certs,
                    follow_redirects="none",
                )
                raw = response.read()
                return json.loads(raw) if raw else None
            except HTTPError as error:
                raw = error.read() if hasattr(error, "read") else b""
                try:
                    parsed = json.loads(raw) if raw else {}
                except ValueError:
                    parsed = {"raw": raw.decode("utf-8", "replace")}
                message = (parsed.get("error") or {}).get("message", "") if isinstance(parsed, dict) else ""
                if error.code in RETRY_STATUSES and attempt < self.max_retries:
                    self.sleep(retry_after_seconds(error.headers, attempt))
                    attempt += 1
                    continue
                # A group or device created moments ago may not have reached every replica.
                if (
                    retry_replication
                    and error.code == 400
                    and REPLICATION_MESSAGE in message
                    and replication_attempts < 3
                ):
                    self.sleep(2 * (replication_attempts + 1))
                    replication_attempts += 1
                    continue
                raise GraphError(
                    f"{method} {path} returned {error.code}: {message or parsed}",
                    status=error.code,
                    body=parsed,
                ) from error
            except URLError as error:
                if attempt < self.max_retries:
                    self.sleep(retry_after_seconds(None, attempt))
                    attempt += 1
                    continue
                raise GraphError(f"{method} {path} failed: {error.reason}") from error

    def get_all(self, path):
        """GET a collection and follow @odata.nextLink to the end."""
        items = []
        page = self.request("GET", path) or {}
        items.extend(page.get("value", []))
        while page.get("@odata.nextLink"):
            page = self.request("GET", page["@odata.nextLink"]) or {}
            items.extend(page.get("value", []))
        return items

    # Groups -----------------------------------------------------------------------------------

    def resolve_group(self, reference):
        if is_guid(reference):
            try:
                return self.request("GET", f"/groups/{reference}?$select={GROUP_FIELDS}")
            except GraphError as error:
                if error.status != 404:
                    raise
        flt = quote(f"displayName eq {odata_quote(reference)}", safe="")
        found = self.get_all(f"/groups?$filter={flt}&$select={GROUP_FIELDS}")
        if not found:
            raise GraphError(f"No group called '{reference}'.")
        if len(found) > 1:
            raise GraphError(
                f"{len(found)} groups are called '{reference}' ({', '.join(g['id'] for g in found)}). "
                "Use the group's object ID."
            )
        return found[0]

    def group_members(self, group_id):
        # No query parameters: they would need ConsistencyLevel: eventual, which reads an index
        # that can lag behind recent changes. Devices are picked out by @odata.type instead.
        return device_members(self.get_all(f"/groups/{group_id}/members"))

    # Devices ----------------------------------------------------------------------------------

    def find_devices(self, reference, operating_system=""):
        """All devices matching a display name, object ID or deviceId."""
        reference = reference.strip()
        matches = []
        if is_guid(reference):
            try:
                device = self.request("GET", f"/devices/{reference}?$select={DEVICE_FIELDS}")
                matches = [device] if device else []
            except GraphError as error:
                if error.status != 404:
                    raise
            if not matches:
                flt = quote(f"deviceId eq {odata_quote(reference)}", safe="")
                matches = self.get_all(f"/devices?$filter={flt}&$select={DEVICE_FIELDS}")
        else:
            expression = f"displayName eq {odata_quote(reference)}"
            if operating_system:
                expression += f" and operatingSystem eq {odata_quote(operating_system)}"
            flt = quote(expression, safe="")
            matches = self.get_all(f"/devices?$filter={flt}&$select={DEVICE_FIELDS}")
        if operating_system:
            matches = [
                m for m in matches if (m.get("operatingSystem") or "").lower() == operating_system.lower()
            ]
        return matches

    # Changes ----------------------------------------------------------------------------------

    def add_members(self, group_id, device_ids, batch_size=MAX_BATCH):
        """Add devices in batches. Returns the IDs that were already members (raced in)."""
        already = []
        for batch in chunked(list(device_ids), min(batch_size, MAX_BATCH)):
            body = {"members@odata.bind": [f"{self.base}/directoryObjects/{i}" for i in batch]}
            try:
                self.request("PATCH", f"/groups/{group_id}", body, retry_replication=True)
            except GraphError as error:
                if error.status != 400:
                    raise
                # One bad reference fails the whole batch, and nothing is added. Add the batch
                # one by one, so a device that became a member meanwhile does not stop the rest.
                for device_id in batch:
                    single = {"@odata.id": f"{self.base}/directoryObjects/{device_id}"}
                    try:
                        self.request(
                            "POST", f"/groups/{group_id}/members/$ref", single, retry_replication=True
                        )
                    except GraphError as single_error:
                        message = str(single_error)
                        if single_error.status == 400 and ALREADY_MEMBER_MESSAGE in message:
                            already.append(device_id)
                            continue
                        raise
        return already

    def remove_member(self, group_id, device_id):
        # Always /$ref: without it Graph deletes the device object itself.
        path = removal_path(group_id, device_id)
        self.request("DELETE", path)


# ---------------------------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------------------------


def check_group(group):
    """Refuse groups whose members cannot be devices or cannot be changed directly."""
    types = group.get("groupTypes") or []
    name = group.get("displayName") or group.get("id")
    if "DynamicMembership" in types:
        raise GraphError(f"'{name}' has dynamic membership: its members cannot be added or removed.")
    if "Unified" in types:
        raise GraphError(
            f"'{name}' is a Microsoft 365 group: devices can only be members of security groups."
        )
    if not group.get("securityEnabled", True):
        raise GraphError(f"'{name}' is not a security group: devices can only be members of security groups.")


def resolve_devices(client, references, operating_system, duplicates, report_missing):
    """Resolve references to {id: displayName}, collecting not_found and ambiguous."""
    resolved, not_found, ambiguous = {}, [], []
    for reference in references:
        matches = client.find_devices(reference, operating_system)
        if not matches:
            not_found.append(reference)
            continue
        selected, info = pick_devices(reference, matches, duplicates)
        if info:
            ambiguous.append(info)
        for device in selected:
            resolved[device["id"]] = device.get("displayName")
    if report_missing and not_found:
        report_missing(not_found)
    return resolved, not_found, ambiguous


def apply_entry(client, entry, params, check_mode, warn):
    group = client.resolve_group(entry["group"])
    check_group(group)
    operating_system = entry.get("operating_system") or params["operating_system"]
    missing = []

    desired, not_found, ambiguous = resolve_devices(
        client, entry["devices"], operating_system, params["duplicates"], missing.extend
    )
    if missing and params["missing"] == "fail":
        raise GraphError(
            "No device matches {} for group '{}'.".format(", ".join(missing), group["displayName"])
        )
    for reference in missing:
        warn("Group '{}': no device matches '{}'.".format(group["displayName"], reference))
    remove, remove_not_found, remove_ambiguous = resolve_devices(
        client, entry["remove"], operating_system, params["duplicates"], None
    )
    current = client.group_members(group["id"])
    to_add, to_remove, unchanged = compute_plan(current, desired, remove, entry["mode"])

    if group.get("isAssignableToRole") and (to_add or to_remove):
        warn(
            f"'{group['displayName']}' is role-assignable: changing its members also needs "
            "RoleManagement.ReadWrite.Directory."
        )

    if not check_mode:
        if to_add:
            already = set(client.add_members(group["id"], [d["id"] for d in to_add], params["batch_size"]))
            if already:
                unchanged.extend(d for d in to_add if d["id"] in already)
                to_add = [d for d in to_add if d["id"] not in already]
        for device in to_remove:
            client.remove_member(group["id"], device["id"])

    after = {k: v for k, v in current.items() if k not in {d["id"] for d in to_remove}}
    after.update({d["id"]: d["displayName"] for d in to_add})
    return {
        "group": entry["group"],
        "group_id": group["id"],
        "group_name": group.get("displayName"),
        "mode": entry["mode"],
        "added": to_add,
        "removed": to_remove,
        "unchanged": unchanged,
        "not_found": not_found + remove_not_found,
        "ambiguous": ambiguous + remove_ambiguous,
        "changed": bool(to_add or to_remove),
        "_before": sorted((v or k) for k, v in current.items()),
        "_after": sorted((v or k) for k, v in after.items()),
    }


def main():
    module = AnsibleModule(
        argument_spec={
            "access_token": {"type": "str", "required": True, "no_log": True},
            "graph_url": {"type": "str", "default": "https://graph.microsoft.com"},
            "groups": {
                "type": "list",
                "elements": "dict",
                "required": True,
                "options": {
                    "group": {"type": "str", "required": True},
                    "devices": {"type": "list", "elements": "str", "default": []},
                    "mode": {"type": "str", "choices": ["add", "exact"], "default": "add"},
                    "remove": {"type": "list", "elements": "str", "default": []},
                    "operating_system": {"type": "str"},
                },
            },
            "operating_system": {"type": "str", "default": ""},
            "duplicates": {"type": "str", "choices": ["newest", "all", "fail"], "default": "newest"},
            "missing": {"type": "str", "choices": ["warn", "fail"], "default": "warn"},
            "batch_size": {"type": "int", "default": MAX_BATCH},
            "max_retries": {"type": "int", "default": 5},
            "timeout": {"type": "int", "default": 60},
            "validate_certs": {"type": "bool", "default": True},
        },
        supports_check_mode=True,
    )
    params = module.params
    if not 1 <= params["batch_size"] <= MAX_BATCH:
        module.fail_json(msg=f"batch_size must be between 1 and {MAX_BATCH}.")

    try:
        entries = merge_entries(params["groups"])
    except ValueError as error:
        module.fail_json(msg=str(error))

    client = GraphClient(
        params["graph_url"],
        params["access_token"],
        timeout=params["timeout"],
        validate_certs=params["validate_certs"],
        max_retries=params["max_retries"],
    )
    results = []
    try:
        for entry in entries:
            results.append(apply_entry(client, entry, params, module.check_mode, module.warn))
    except (GraphError, ValueError) as error:
        module.fail_json(msg=str(error), results=[strip_private(r) for r in results])

    changed = any(r["changed"] for r in results)
    diff = {
        "before": "".join(
            "{}:\n{}".format(r["group_name"], "".join(f"  {n}\n" for n in r["_before"])) for r in results
        ),
        "after": "".join(
            "{}:\n{}".format(r["group_name"], "".join(f"  {n}\n" for n in r["_after"])) for r in results
        ),
    }
    module.exit_json(
        changed=changed, results=[strip_private(r) for r in results], diff=diff, requests=client.requests
    )


def strip_private(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


if __name__ == "__main__":
    main()
