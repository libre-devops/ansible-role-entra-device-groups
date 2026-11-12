#!/usr/bin/env python3
"""Import a release tag of a role into Ansible Galaxy, with download links that work.

Usage: GALAXY_API_KEY=... galaxy-import.py ROLE GITHUB_OWNER GITHUB_REPO TAG
  e.g. galaxy-import.py libre_devops.mde_linux libre-devops ansible-role-mde-linux 1.0.0

Used by `just galaxy-import` and the Release workflow. Standard library only.

Why not `ansible-galaxy role import`: Galaxy namespace names cannot contain a hyphen, so the
role namespace is libre_devops while the GitHub organisation is libre-devops. Galaxy looks up
a role namespace named exactly the GitHub owner it is given, finds none for libre-devops and
answers HTTP 500. The Galaxy team's workaround (https://forum.ansible.com/t/45995) is to import
through the REST API, naming the namespace as the GitHub user and the real repository as
alternate_clone_url. Galaxy then records libre_devops as the role's GitHub user and builds
every download link from it, github.com/libre_devops/..., which does not exist, so installs
fail with HTTP 404. This script therefore also sets the role's GitHub user to the real owner,
as in https://forum.ansible.com/t/45861/17, and checks the tag's download link works. Each
import sets the GitHub user back to the namespace, so it does both every time.

GALAXY_API_URL overrides https://galaxy.ansible.com, for testing.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER = os.environ.get("GALAXY_API_URL", "https://galaxy.ansible.com").rstrip("/")
API = f"{SERVER}/api/v1"
TIMEOUT = 60
IMPORT_DEADLINE = 15 * 60
DONE = {"SUCCESS", "FAILED", "CANCELED", "SKIPPED"}


class Galaxy:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None):
        """One API request. Returns the decoded JSON, or {} for an empty answer."""
        headers = {"Accept": "application/json", "User-Agent": "galaxy-import"}
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        data = None
        if body is not None:
            # Galaxy answers HTTP 500 to a JSON body sent without this header.
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = urllib.request.Request(urllib.parse.urljoin(API + "/", path), data, headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace").strip()[:500]
            raise SystemExit(f"Galaxy: {method} {path} failed with HTTP {error.code}: {detail}") from None
        return json.loads(raw) if raw.strip() else {}


def import_tag(galaxy, namespace, name, repo, tag, clone_url):
    """Start the import, print Galaxy's log as it comes and fail unless it succeeds."""
    started = galaxy.call(
        "POST",
        "imports/",
        {
            "github_user": namespace,
            "github_repo": repo,
            "github_reference": tag,
            "alternate_clone_url": clone_url,
            "alternate_role_name": name,
        },
    )
    task_id = started["results"][0]["id"]
    print(f"Galaxy import task {task_id}")
    shown = 0
    deadline = time.monotonic() + IMPORT_DEADLINE
    while True:
        task = galaxy.call("GET", f"imports/?id={task_id}")["results"][0]
        messages = task["summary_fields"]["task_messages"]
        for message in messages[shown:]:
            kind = message.get("message_type", "INFO")
            prefix = "" if kind in ("INFO", "SUCCESS") else f"{kind}: "
            print(f"  {prefix}{message.get('message_text', '')}")
        shown = len(messages)
        if task["state"] in DONE:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"Galaxy import task {task_id} did not finish in {IMPORT_DEADLINE // 60} minutes")
        time.sleep(5)
    if task["state"] != "SUCCESS":
        raise SystemExit(f"Galaxy import task {task_id} ended {task['state']}; its log is above")


def find_role(galaxy, namespace, name):
    query = urllib.parse.urlencode({"owner__username": namespace, "name": name})
    found = [
        role
        for role in galaxy.call("GET", f"roles/?{query}")["results"]
        if role["name"] == name and role["summary_fields"]["namespace"]["name"] == namespace
    ]
    if len(found) != 1:
        raise SystemExit(f"Galaxy has {len(found)} roles named {namespace}.{name} after the import, not 1")
    return found[0]


def version_download_url(galaxy, role_id, tag):
    path = f"roles/{role_id}/versions/"
    while path:
        page = galaxy.call("GET", path)
        for version in page["results"]:
            if version["name"] == tag:
                return version["download_url"]
        path = page.get("next")
    raise SystemExit(f"Galaxy lists no version {tag} for role {role_id}")


def check_download(url):
    """GitHub answers 200, through a redirect, when the tag's archive exists."""
    request = urllib.request.Request(url, headers={"User-Agent": "galaxy-import"}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT):
            pass
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answers HTTP {error.code}") from None


def main(argv):
    if len(argv) != 5:
        raise SystemExit(__doc__.split("\n\n")[1])
    role, owner, repo, tag = argv[1:]
    namespace, _, name = role.partition(".")
    if not namespace or not name:
        raise SystemExit(f"{role} is not namespace.name")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise SystemExit(f"{tag} is not a semver tag such as 1.0.0")
    token = os.environ.get("GALAXY_API_KEY")
    if not token:
        raise SystemExit("Set GALAXY_API_KEY to a Galaxy API token")
    galaxy = Galaxy(token)
    clone_url = f"https://github.com/{owner}/{repo}"
    expected_download = f"{clone_url}/archive/{tag}.tar.gz"

    print(f"Importing {clone_url} at {tag} into Galaxy as {role}")
    import_tag(galaxy, namespace, name, repo, tag, clone_url)

    found = find_role(galaxy, namespace, name)
    wanted = {"github_user": owner, "github_repo": repo}
    changes = {key: value for key, value in wanted.items() if found[key] != value}
    if changes:
        galaxy.call("PUT", f"roles/{found['id']}/", changes)
        print(f"Pointed role {found['id']} at {clone_url}, which its download links are built from")

    download = version_download_url(galaxy, found["id"], tag)
    if download != expected_download:
        raise SystemExit(f"Galaxy offers {download} for {role} {tag}, not {expected_download}")
    check_download(download)
    print(f"{role} {tag} is on Galaxy: {SERVER}/ui/standalone/roles/{namespace}/{name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
