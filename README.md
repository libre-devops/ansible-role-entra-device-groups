<div align="center">
  <a href="https://libredevops.org">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://libredevops.org/assets/libre-devops-white.png">
      <img alt="Libre DevOps" src="https://libredevops.org/assets/libre-devops-black.png" width="300">
    </picture>
  </a>
</div>

# Ansible Role: Entra ID device groups

Adds devices to Microsoft Entra ID security groups and removes them, through Microsoft Graph, so each group's device members match a list you keep in variables or JSON files. Running it again changes nothing. Built for putting servers into the groups that scope Intune and Microsoft Defender for Endpoint policy. Nothing is needed on the control node beyond `ansible-core`.

[![CI](https://github.com/libre-devops/ansible-role-entra-device-groups/actions/workflows/ci.yml/badge.svg)](https://github.com/libre-devops/ansible-role-entra-device-groups/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/libre-devops/ansible-role-entra-device-groups?sort=semver&label=release)](https://github.com/libre-devops/ansible-role-entra-device-groups/releases/latest)
[![Galaxy](https://img.shields.io/badge/galaxy-libre__devops.entra__device__groups-EE0000?logo=ansible&logoColor=white)](https://galaxy.ansible.com/ui/standalone/roles/libre_devops/entra_device_groups/)
[![Licence](https://img.shields.io/github/license/libre-devops/ansible-role-entra-device-groups)](./LICENSE)

---

> **Status:** tested in CI against a mock of Microsoft Graph built from the Microsoft Learn API reference. Not yet run against a real tenant: start with `just live ... --check --diff` on a test tenant.

## What it does

For each group in the list, the role:

1. Finds the group by its exact display name or object ID, and stops if the name matches no group or several.
2. Refuses groups whose device members cannot be set directly: dynamic membership groups, Microsoft 365 groups and other groups that are not security groups.
3. Finds each device by its exact display name, object ID or Entra device ID, optionally only devices with a given operating system, and deals with names that match several devices or none the way you choose.
4. Reads the group's current members, following every page.
5. Adds the devices that are missing, 20 per request, and removes the ones that should go: the ones you list under `remove`, and in `exact` mode every other device member.
6. Reports, per group, what it added, removed and left alone, which names it could not find and which matched several devices, and sets the `entra_device_groups_results` fact.

Only device members are ever changed. Users, groups, service principals and contacts in the same group are left alone, even in `exact` mode.

It runs on the control node: put it in a play for `localhost`, or delegate it there. It signs in with an app registration's client ID and secret, or takes an access token you already have. It supports check mode and diff mode, retries throttled requests, and works with the national clouds.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [What this is for](#what-this-is-for)
- [The groups file](#the-groups-file)
- [Modes](#modes)
- [How devices are matched](#how-devices-are-matched)
- [Check mode and diff](#check-mode-and-diff)
- [Signing in](#signing-in)
- [Role variables](#role-variables)
- [Examples](#examples)
- [Using it from ansible-mde-satellite-release](#using-it-from-ansible-mde-satellite-release)
- [Troubleshooting](#troubleshooting)
- [The API calls it makes](#the-api-calls-it-makes)
- [Confirmed and assumed](#confirmed-and-assumed)
- [Developing](#developing)
- [Releasing](#releasing)
- [Sources](#sources)

## Requirements

| What | Detail |
|---|---|
| Control node | `ansible-core` 2.16 or later, and Python 3. No collections and no Python packages: the module uses the standard library and Ansible's own HTTP helper. |
| Network | HTTPS from the control node to Microsoft Graph and to Microsoft Entra ID's sign-in endpoint (or a national cloud's). |
| Groups | Assigned (not dynamic) security groups. |
| Devices | Device objects in Microsoft Entra ID. Servers managed by Defender for Endpoint security settings management get one when they enrol (see [What this is for](#what-this-is-for)). |
| Sign-in | An app registration with a client secret and the application permissions below, granted admin consent. Or a Microsoft Graph access token that carries the same rights. |

### Microsoft Graph permissions

| Permission (application) | Why |
|---|---|
| `GroupMember.ReadWrite.All` | Find groups, read their members, add and remove members. Microsoft's least privileged permission for this. |
| `Device.ReadWrite.All` | Microsoft's [Add members](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0) page lists `GroupMember.ReadWrite.All` **and** `Device.ReadWrite.All` as the least privileged application permissions for adding a device. It also covers looking devices up (`Device.Read.All` would be enough for that alone). |
| `RoleManagement.ReadWrite.Directory` | Only if a group is role-assignable (`isAssignableToRole`). The role warns when it is about to change such a group. Avoid putting devices in role-assignable groups unless you mean to. |

Grant them under **App registrations > your app > API permissions > Add a permission > Microsoft Graph > Application permissions**, then **Grant admin consent**.

With a user's token instead (delegated permissions), Microsoft lists `GroupMember.ReadWrite.All` and `Device.Read.All`, and the user also needs a Microsoft Entra role that can change the group's members, such as Groups Administrator, Intune Administrator (security groups only) or an owner of the group.

A group in a restricted management administrative unit can only be changed by an app or user with a role scoped to that unit, whatever the permissions above.

## Installation

From Galaxy:

```bash
ansible-galaxy role install libre_devops.entra_device_groups
```

Or pin a release from GitHub, as in [`examples/requirements.yml`](./examples/requirements.yml):

```yaml
roles:
  - name: libre_devops.entra_device_groups
    src: https://github.com/libre-devops/ansible-role-entra-device-groups.git
    scm: git
    version: 1.0.0
```

Without Galaxy, or to keep a copy inside your organisation, see [Using the role without Galaxy](./docs/using-without-galaxy.md). It covers git, release archives, requirements files that point at your own servers, AWX and Automation Controller, and execution environments.

## Quick start

```yaml
- name: Put servers into their policy groups
  hosts: localhost
  gather_facts: false
  roles:
    - role: libre_devops.entra_device_groups
      vars:
        entra_device_groups_tenant_id: contoso.example
        entra_device_groups_client_id: 00000000-0000-4000-8000-000000000000
        entra_device_groups_client_secret: "{{ vault_entra_client_secret }}"
        entra_device_groups_operating_system: Linux
        entra_device_groups_groups:
          - group: Linux servers - endpoint detection
            devices: [web01.example.internal, web02.example.internal]
          - group: Linux servers - pilot ring
            mode: exact
            devices: [web01.example.internal]
```

The first group gains `web01` and `web02` if they are not already members. The second ends up with `web01` as its only device member. Run it again and nothing changes. Add `--check --diff` to see what a run would do without doing it.

Or keep the list in a JSON file and use the example playbook:

```bash
ansible-playbook examples/playbook.yml -e @groups.json -e @vault.yml --ask-vault-pass
```

## What this is for

Microsoft Defender for Endpoint's security settings management lets Intune endpoint security policies reach servers that are not enrolled in Intune, Linux servers included. When such a server onboards to Defender for Endpoint and is in scope for security settings management, it gets a device object in Microsoft Entra ID (a "synthetic registration" if it is not otherwise registered). Policies are assigned to Microsoft Entra groups of those device objects, and assignment filters are ignored for these devices, so group membership is what decides which servers get which policy.

Microsoft recommends dynamic groups (for example on the operating system) for whole fleets, and that is the right tool when a rule describes the group. This role is for when membership is a decision rather than a rule: a pilot ring, servers named in a change request, an exclusion group, a test ring kept to exactly a list. The list lives in a JSON or YAML file in version control, and each run makes Entra ID match it.

Nothing in the role is specific to Defender for Endpoint or Linux: it works for any device objects and any assigned security group.

Timing matters. A server's device object appears only after it onboards and enrols, which Microsoft says usually takes a few minutes but can take up to 24 hours, and these devices check in for policy every 90 minutes. A device that is not there yet is reported (or fails the run, with `missing: fail`), and the next run adds it.

## The groups file

Each entry describes one group:

| Key | Required | Default | Meaning |
|---|---|---|---|
| `group` | yes | | The group's exact display name, or its object ID. |
| `devices` | no | `[]` | Devices that must be members: exact display names, object IDs or Entra device IDs. |
| `mode` | no | `add` | `add` only adds. `exact` also removes every device member not in `devices`. |
| `remove` | no | `[]` | Devices that must not be members, matched the same way as `devices`. |
| `operating_system` | no | `entra_device_groups_operating_system` | Only match devices with this `operatingSystem` for this group, for example `Linux`. |

A file holds either a list under `device_groups`, as in [`examples/groups.example.json`](./examples/groups.example.json):

```json
{
  "device_groups": [
    {
      "group": "Linux servers - endpoint detection",
      "operating_system": "Linux",
      "devices": ["web01.example.internal", "web02.example.internal", "db01.example.internal"],
      "remove": ["old-web01.example.internal"]
    },
    {
      "group": "Linux servers - pilot ring",
      "operating_system": "Linux",
      "mode": "exact",
      "devices": ["web01.example.internal"]
    },
    {
      "group": "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
      "devices": ["bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"]
    }
  ]
}
```

or a single entry:

```json
{ "group": "Linux servers - pilot ring", "mode": "exact", "devices": ["web01.example.internal"] }
```

YAML works as well as JSON. Give the role files with `entra_device_groups_files`, and entries directly with `entra_device_groups_groups`. Both can be used together.

The key is `device_groups`, not `groups`, on purpose. `groups` is one of Ansible's reserved variables (the inventory groups), so `-e @groups.json` with a `groups` key would be silently ignored. The role refuses a file with a `groups` key and says to rename it.

When the same group appears in more than one entry or file (matched on the `group` value, ignoring case), the entries are combined: their `devices` lists are joined, and so are their `remove` lists. The role stops before changing anything if the combined entries disagree: one says `add` and another `exact`, they give different operating systems, or a device is both wanted and to be removed.

## Modes

| Mode | Adds | Removes |
|---|---|---|
| `add` (default) | Listed devices that are not already members. | Only the devices under `remove`. |
| `exact` | Listed devices that are not already members. | Every device member that is not listed, and the devices under `remove`. |

In both modes the role only ever touches device members. A group that holds users or nested groups as well keeps them.

`exact` with an empty `devices` list removes every device from the group. Run with `--check --diff` first when you change a list.

Removal always uses `DELETE /groups/{id}/members/{id}/$ref`, which removes the membership. Without `/$ref`, Microsoft Graph deletes the object itself if the app is allowed to, so the module only builds removal paths through one function that checks both IDs are GUIDs and always ends the path in `/$ref`, and the tests check every removal the mock sees.

## How devices are matched

| You give | The role looks for |
|---|---|
| A name, such as `web01.example.internal` | Devices whose `displayName` equals it, using Microsoft Graph's `eq` filter. With an operating system set, only devices whose `operatingSystem` also equals that. Quotes in names are escaped. |
| A GUID | First a device with that object ID (`id`), then a device with that Entra device ID (`deviceId`). |

Then:

- **No match.** With `entra_device_groups_missing: warn` (the default), the role warns, lists the name under `not_found`, and carries on with the rest. With `fail`, it stops before changing that group. A name under `remove` that matches nothing is only listed under `not_found`.
- **Several matches.** A display name is not unique in Entra ID: a rebuilt server often leaves its old device object behind. With `entra_device_groups_duplicates: newest` (the default), the role uses the device with the latest `approximateLastSignInDateTime`. With `all`, it uses every match. With `fail`, it stops. Whatever the setting, the duplicates are listed under `ambiguous`, with each match's ID and last sign-in and the ones used. Give the object ID to pick one yourself.

Look up what Entra ID calls your servers before writing the list: short host name or fully qualified name, and the `operatingSystem` value. Microsoft's Intune documentation shows Defender-managed Linux servers with the operating system `Linux`.

## Check mode and diff

With `--check`, the role signs in and reads groups, devices and members, but sends no changes. The results and the summary say what it would add and remove.

With `--diff`, each changed group's device members are shown before and after, by name:

```text
TASK [libre_devops.entra_device_groups : Make the group memberships match] *****
--- before
+++ after
@@ -1,8 +1,9 @@
 Linux servers EDR:
+  web01
   web02
   web03
   web04
   web05
   web06
 Linux servers RTP:
-  old01
+  db01
```

The last task prints one line per group:

```text
Linux servers RTP (exact): would add 1, would remove 1, already members 0
```

## Signing in

| Method | Variables | How it works |
|---|---|---|
| Client credentials (preferred for automation) | `entra_device_groups_tenant_id`, `entra_device_groups_client_id`, `entra_device_groups_client_secret` | The role asks `{login_url}/{tenant}/oauth2/v2.0/token` for a token with the scope `{graph_url}/.default`. |
| A ready access token | `entra_device_groups_access_token` | Used as it is. The client credentials are ignored. |

Keep the secret or token in Ansible Vault or a secret store. Both are marked `no_log` in the argument specification, the token request hides its output, and the module never returns the token. When Microsoft Entra ID refuses the credentials, the role shows its `error_description` (for example `AADSTS7000215: Invalid client secret provided`), which holds no secrets.

Any Microsoft Graph access token for the right cloud works, for example from your own tooling or from the Azure CLI:

```bash
ansible-playbook examples/playbook.yml -e @groups.json \
  -e entra_device_groups_access_token="$(az account get-access-token --resource-type ms-graph --query accessToken -o tsv)"
```

A user's token can do what that user's Entra role and the consented delegated permissions allow, and no more (see [Requirements](#microsoft-graph-permissions)). Access tokens usually last about an hour (Microsoft's client credentials example shows `expires_in: 3599`), which is plenty for one run. Get a fresh one for each run.

### National clouds

Set both endpoints:

| Cloud | `entra_device_groups_graph_url` | `entra_device_groups_login_url` |
|---|---|---|
| Global (and US Government GCC) | `https://graph.microsoft.com` (default) | `https://login.microsoftonline.com` (default) |
| US Government L4 (GCC High) | `https://graph.microsoft.us` | `https://login.microsoftonline.us` |
| US Government L5 (DoD) | `https://dod-graph.microsoft.us` | `https://login.microsoftonline.us` |
| China (operated by 21Vianet) | `https://microsoftgraph.chinacloudapi.cn` | `https://login.chinacloudapi.cn` |

Every API the role calls is listed by Microsoft as available in all of these. A token from one cloud does not work in another. The module only ever sends the token to the host in `entra_device_groups_graph_url`, and stops if Microsoft Graph points a next-page link anywhere else.

## Role variables

Every variable is validated against [`meta/argument_specs.yml`](./meta/argument_specs.yml) when the role starts. Defaults are in [`defaults/main.yml`](./defaults/main.yml).

### Desired state

| Variable | Default | Description |
|---|---|---|
| `entra_device_groups_groups` | `[]` | The desired state, one entry per group, as described in [The groups file](#the-groups-file). |
| `entra_device_groups_files` | `[]` | JSON or YAML files on the control node with more entries. Each holds one entry, or a mapping with a `device_groups` list. Their entries are added to `entra_device_groups_groups`. |

### Sign-in and endpoints

| Variable | Default | Description |
|---|---|---|
| `entra_device_groups_access_token` | `""` | A Microsoft Graph access token. When set, the client credentials are not used. `no_log`. |
| `entra_device_groups_tenant_id` | `""` | Tenant ID or primary domain, for the client credentials flow. |
| `entra_device_groups_client_id` | `""` | Application (client) ID, for the client credentials flow. |
| `entra_device_groups_client_secret` | `""` | Client secret, for the client credentials flow. `no_log`. |
| `entra_device_groups_graph_url` | `https://graph.microsoft.com` | Microsoft Graph root, without the version. See [National clouds](#national-clouds). |
| `entra_device_groups_login_url` | `https://login.microsoftonline.com` | Microsoft Entra ID sign-in root, for the client credentials flow. |

### Matching

| Variable | Default | Description |
|---|---|---|
| `entra_device_groups_operating_system` | `""` | Only match devices with this `operatingSystem`, for example `Linux`. Empty matches any. An entry's `operating_system` overrides it. |
| `entra_device_groups_duplicates` | `newest` | When a name matches several devices: `newest` (latest sign-in), `all`, or `fail`. |
| `entra_device_groups_missing` | `warn` | When a device to add does not exist: `warn` and carry on, or `fail`. |

### Requests

| Variable | Default | Description |
|---|---|---|
| `entra_device_groups_batch_size` | `20` | Devices added per request, 1 to 20. Microsoft Graph accepts at most 20. |
| `entra_device_groups_max_retries` | `5` | Retries for each throttled (429) or unavailable (503, 504) request, waiting as long as `Retry-After` says, or 1, 2, 4, 8 and so on seconds without it (at most 120). |
| `entra_device_groups_timeout` | `60` | Seconds to wait for each HTTP response. |
| `entra_device_groups_validate_certs` | `true` | Check TLS certificates. Leave it on outside tests. |

### Facts set by the role

`entra_device_groups_results` holds one entry per group, in the order the groups were given:

| Key | Meaning |
|---|---|
| `group` | The group as given (name or ID). |
| `group_id`, `group_name` | The group's object ID and display name. |
| `mode` | `add` or `exact`. |
| `added` | Devices added, or in check mode that would be added, each with `id` and `displayName`. |
| `removed` | Devices removed, or that would be removed. |
| `unchanged` | Listed devices that were already members. |
| `not_found` | Names or IDs that matched no device, from `devices` and `remove`. |
| `ambiguous` | Names that matched several devices: `reference`, `matches` (each with `id`, `displayName`, `approximateLastSignInDateTime`) and `used`. |
| `changed` | Whether this group changed, or would. |

## Examples

### From a JSON file, with the example playbook

[`examples/playbook.yml`](./examples/playbook.yml) takes the file as extra variables (a `device_groups` list or a single entry) and the sign-in from an encrypted vars file ([`examples/vault.example.yml`](./examples/vault.example.yml)):

```bash
cp examples/vault.example.yml vault.yml        # fill in, then: ansible-vault encrypt vault.yml
cp examples/groups.example.json groups.json    # your groups and devices
ansible-playbook examples/playbook.yml -e @groups.json -e @vault.yml --ask-vault-pass --check --diff
ansible-playbook examples/playbook.yml -e @groups.json -e @vault.yml --ask-vault-pass
```

`.gitignore` keeps `groups.json` and `vault.yml` at the top of this repository out of git.

### Several files, kept with the inventory

```yaml
- name: Keep policy groups in line with the files
  hosts: localhost
  gather_facts: false
  roles:
    - role: libre_devops.entra_device_groups
      vars:
        entra_device_groups_operating_system: Linux
        entra_device_groups_missing: fail
        entra_device_groups_files:
          - "{{ inventory_dir }}/entra/pilot-ring.json"
          - "{{ inventory_dir }}/entra/broad-ring.json"
```

### From the inventory

Put every host in an inventory group into an Entra ID group, and nothing else:

```yaml
- name: Make the pilot ring group match the inventory
  hosts: localhost
  gather_facts: false
  roles:
    - role: libre_devops.entra_device_groups
      vars:
        entra_device_groups_operating_system: Linux
        entra_device_groups_groups:
          - group: Linux servers - pilot ring
            mode: exact
            devices: "{{ groups['pilot_ring'] }}"
```

This assumes the inventory names match the devices' display names in Entra ID. If Entra ID knows them by their short names, map them: `"{{ groups['pilot_ring'] | map('regex_replace', '\\..*$', '') | list }}"`.

### Take decommissioned servers out

```yaml
entra_device_groups_groups:
  - group: Linux servers - endpoint detection
    remove: [old-web01.example.internal, old-web02.example.internal]
```

### Use the results

```yaml
- name: Fail if any listed server is not in Entra ID yet
  ansible.builtin.assert:
    that: entra_device_groups_results | map(attribute='not_found') | flatten | length == 0
    fail_msg: "Not in Entra ID yet: {{ entra_device_groups_results | map(attribute='not_found') | flatten | join(', ') }}"
```

## Using it from ansible-mde-satellite-release

[ansible-mde-satellite-release](https://github.com/libre-devops/ansible-mde-satellite-release) releases new Defender for Endpoint versions through Red Hat Satellite, testing them first on VMs that it onboards and offboards within its test stage. It sets the enforcement level on the test VMs directly, so its built-in checks do not need group-scoped policy, and it does not use this role.

If your tests do need group-scoped policy, mind the order. A freshly built VM has no device object in Entra ID until it has onboarded and enrolled (minutes, sometimes up to 24 hours), and then it checks in for policy every 90 minutes. So adding freshly built VMs to groups before the test stage finds nothing to add, and the role reports them under `not_found`. Two ways that work:

- **A standing test ring.** Keep a few test servers onboarded between releases and point the release's test inventory at them. Before the test stage, this role keeps the test ring's policy group to exactly those servers:

  ```yaml
  # A sketch of an extra playbook, run between bootstrap and test. It is not part of
  # ansible-mde-satellite-release.
  - name: Keep the test ring's policy group to exactly the test servers
    hosts: localhost
    gather_facts: false
    roles:
      - role: libre_devops.entra_device_groups
        vars:
          entra_device_groups_operating_system: Linux
          entra_device_groups_missing: fail
          entra_device_groups_groups:
            - group: "{{ release_test_policy_group }}"
              mode: exact
              devices: "{{ groups['release_test'] }}"
  ```

  with the role added to its `requirements.yml` and the sign-in variables in its vault file. `release_test_policy_group` is a variable you would add.

- **Fresh VMs.** Onboard, wait until the devices appear in Entra ID, run the role, wait for a policy check-in, then run the checks. That needs waits the release playbooks do not have, which is why they set what the checks need on the VMs instead.

After a release, the same role can move production servers between ring groups from a file in that repository's inventory.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `Microsoft Entra ID refused the client credentials (401): AADSTS7000215 ...` | The client secret is wrong or has expired. Create a new one under the app registration's **Certificates & secrets**. |
| `... AADSTS700016 ...` or `AADSTS90002 ...` | The client ID is not an app in that tenant, or the tenant ID or domain is wrong. For a national cloud, set `entra_device_groups_login_url` as well. |
| `PATCH /groups/... returned 403` or `DELETE ... returned 403` | The app lacks `GroupMember.ReadWrite.All` or `Device.ReadWrite.All`, or admin consent was not granted. A role-assignable group also needs `RoleManagement.ReadWrite.Directory`. A group in a restricted management administrative unit needs a role scoped to that unit. |
| `GET /groups?... returned 403` | The app cannot look groups up. Check `GroupMember.ReadWrite.All` has admin consent. If it has and lookups still fail, add `GroupMember.Read.All` (see [Confirmed and assumed](#confirmed-and-assumed)). |
| `No group called '...'.` | The name differs (it must match the whole display name), or the app cannot see the group. Copy the name from the Microsoft Entra admin center, or use the object ID. |
| `2 groups are called '...'` | Display names are not unique. Use the object ID the message lists. |
| `... has dynamic membership` | Its members come from a rule. Change the rule, or use an assigned group. |
| `... is a Microsoft 365 group` or `... is not a security group` | Devices can only be members of security groups. |
| Warning `no device matches '...'` | The device is not in Entra ID yet (see [What this is for](#what-this-is-for)), its display name is different (short name or fully qualified), or its `operatingSystem` is not the one you filter on. |
| `'...' matches 2 devices` | Old device objects with the same name. Delete the stale ones, give the object ID, or leave `duplicates` at `newest`. |
| `These devices are both wanted and to be removed` | The same device is under `devices` and `remove` for one group, perhaps from two files. |
| `Group '...' is listed with both add and exact mode` | Two entries or files name the same group with different modes. Pick one. |
| `... must hold either one entry ... Rename its groups key to device_groups` | The file uses `groups`. Rename the key to `device_groups`. |
| `Refusing to send the token to ...` | Microsoft Graph returned a next-page link on another host. Check `entra_device_groups_graph_url` is the cloud the token is for. |
| `... returned 429` after retries | The tenant is being throttled hard. Raise `entra_device_groups_max_retries`, or run again later: the run can be repeated safely. |
| `exact` removed a device you wanted | It was not in that group's `devices` list, or its name now matches a different device object. Run with `--check --diff` before changing lists. |

## The API calls it makes

All under `{graph_url}/v1.0`.

| Call | Used for | Confirmed from |
|---|---|---|
| `POST {login_url}/{tenant}/oauth2/v2.0/token` with `client_id`, `client_secret`, `scope={graph_url}/.default`, `grant_type=client_credentials` | Access token | [Client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow) |
| `GET /groups/{id}?$select=...` | Group by object ID | [Get group](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0) |
| `GET /groups?$filter=displayName eq '...'&$select=...` | Group by name | [List groups](https://learn.microsoft.com/en-us/graph/api/group-list?view=graph-rest-1.0), and the [advanced queries](https://learn.microsoft.com/en-us/graph/aad-advanced-queries) tables (`eq` on `displayName` works by default) |
| `GET /groups/{id}/members`, with no query parameters, following `@odata.nextLink` | Current members, keeping those whose `@odata.type` is `#microsoft.graph.device` | [List group members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0), [Paging](https://learn.microsoft.com/en-us/graph/paging) |
| `GET /devices/{id}?$select=...` | Device by object ID | [Get device](https://learn.microsoft.com/en-us/graph/api/device-get?view=graph-rest-1.0) |
| `GET /devices?$filter=deviceId eq '...'&$select=...` | Device by Entra device ID | [device resource](https://learn.microsoft.com/en-us/graph/api/resources/device?view=graph-rest-1.0) (`deviceId` supports `eq`) and the advanced queries tables |
| `GET /devices?$filter=displayName eq '...' and operatingSystem eq '...'&$select=...` | Device by name | [List devices](https://learn.microsoft.com/en-us/graph/api/device-list?view=graph-rest-1.0) and the advanced queries tables |
| `PATCH /groups/{id}` with `members@odata.bind` (up to 20 `directoryObjects/{id}` references) | Add devices | [Add members](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0), example 2 |
| `POST /groups/{id}/members/$ref` with `@odata.id` | Add devices one at a time when a batch is refused | [Add members](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0), example 1 |
| `DELETE /groups/{id}/members/{id}/$ref` | Remove a device | [Remove member](https://learn.microsoft.com/en-us/graph/api/group-delete-members?view=graph-rest-1.0) |

Why the members list has no query parameters: on this API every query parameter except `$expand` needs the advanced query parameters (`ConsistencyLevel: eventual` and `$count`), which read an index that Microsoft says "might not be up-to-date with recent changes". A run straight after another could then see stale members and add or remove the wrong devices. Reading the plain list, which comes back 100 at a time, and picking devices out by `@odata.type` avoids that.

Why a refused batch is retried one device at a time: Microsoft says that if any reference in a `members@odata.bind` request is in error, no members are added, and it answers 400 when an object is already a member. A device that joined the group between reading the members and adding (another run, or someone in the portal) would otherwise stop the whole batch. One at a time, a device that is already a member counts as unchanged, and any other error stops the run with Microsoft Graph's message.

## Confirmed and assumed

Confirmed from Microsoft's documentation (see [Sources](#sources)):

- The least privileged application permissions for adding a device to a group are `GroupMember.ReadWrite.All` and `Device.ReadWrite.All`. Removing a member needs `GroupMember.ReadWrite.All`. Role-assignable groups also need `RoleManagement.ReadWrite.Directory`.
- `GroupMember.ReadWrite.All` "allows the app to list groups, read basic properties, read and update the membership of the groups".
- Devices can be members of security groups but not of Microsoft 365 groups. Members cannot be removed from groups with dynamic membership.
- A `members@odata.bind` request adds at most 20 members, and adds none if any reference is in error.
- Adding answers 400 when the object is already a member, 400 with "...don't exist" while a new group replicates (retry after a brief delay), and 404 when the object does not exist.
- `DELETE /groups/{id}/members/{id}` without `/$ref` deletes the member object itself when the app may manage it.
- On the members list, query parameters other than `$expand` need advanced query parameters, whose index might lag. Members the app cannot read fully still come back with `@odata.type` and `id`. The default page size is 100.
- `eq` on device `displayName`, `operatingSystem` and `deviceId`, and on group `displayName`, works without advanced query parameters.
- A device can be addressed by `id`, and `deviceId` is an alternate key.
- Use the whole `@odata.nextLink` URL for the next page.
- Throttled requests answer 429 with `Retry-After` in seconds. Without the header, Microsoft recommends exponential backoff.
- The token endpoint, form parameters, `/.default` scope and error format of the client credentials flow, and the national cloud endpoints.
- Defender for Endpoint security settings management supports Linux, creates a device identity in Entra ID for devices that are not registered, takes policy through Microsoft Entra groups, ignores assignment filters for these devices, and shows Linux devices' operating system as `Linux`. Enrolment usually takes minutes and can take up to 24 hours, and these devices check in for policy every 90 minutes.

Assumed, and handled defensively:

- **The wording of the "already a member" error.** Microsoft documents the 400 but not its message. The module treats a single add's 400 as "already a member" only when the message contains `already exist`. If the wording differs, the run stops with Microsoft Graph's message rather than misreporting.
- **`@odata.type` on every member.** Microsoft documents it for members the app cannot read fully, and real responses carry it, but the page's examples are shortened. A member without it would be treated as not a device and left alone, never removed.
- **Group lookups with `GroupMember.ReadWrite.All`.** The permissions reference says it lets the app list groups and read basic properties, but the List groups and Get group pages do not name it. If lookups answer 403, add `GroupMember.Read.All`, which those pages list. It is also assumed that `groupTypes`, `securityEnabled` and `isAssignableToRole` count as basic properties.
- **`and` between two default `eq` filters** (`displayName` and `operatingSystem`) works without advanced query parameters. Each is documented on its own. The module also filters the results by operating system itself.
- **Name matching follows Microsoft Graph's `eq`**, including its treatment of case. The role does not compare names again.
- **Retrying 503 and 504** the same way as 429. Microsoft's throttling guidance is about 429.
- **National cloud references.** `members@odata.bind` and `@odata.id` use `{graph_url}/v1.0/directoryObjects/{id}`. Microsoft's examples show the global host.
- **How Defender for Endpoint names a server's device object** (short or fully qualified host name). Check in your tenant.
- The role has been tested against the mock in this repository, built from the shapes above, with `ansible-core` 2.21. It has not yet been run against a live tenant or tested on `ansible-core` 2.16. Please report differences you find.

## Developing

You need [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just). CI runs the same recipes. No containers and no tenant are needed.

| Recipe | What it does |
|---|---|
| `just deps` | Installs the pinned Python tools (`uv.lock`, at least seven days old). |
| `just lint` | `yamllint`, `ansible-lint` on the production profile, `zizmor` on the workflows, and `ruff` on the Python. |
| `just unit` | The module's unit tests with `pytest`: quoting, plans, paging, retries, batching, refusals. |
| `just test` | Runs the Molecule scenario against the mock Microsoft Graph. |
| `just mock [port]` | Starts the mock in the foreground, for trying the role by hand. |
| `just live [args]` | Runs [`examples/playbook.yml`](./examples/playbook.yml) against a real tenant: `just live -e @groups.json -e @vault.yml --ask-vault-pass`. |
| `just secrets` | Scans the git history with gitleaks. |

[`molecule/fixtures/mock_graph.py`](./molecule/fixtures/mock_graph.py) is a stand-in for Microsoft Entra ID's token endpoint and the Microsoft Graph calls above, using only the Python standard library and keeping everything in memory. It is deliberately strict where the real service is risky: it pages members two at a time, refuses any query parameter on the members list, refuses a `members@odata.bind` with more than 20 references or with a device that is already a member, answers 500 to a removal without `/$ref`, and can be told to throttle. It counts what it saw at `/mock/stats`.

The `default` scenario runs on the control node:

| Step | What it proves |
|---|---|
| converge | Signs in with client credentials. Adds 25 devices to a group given by object ID, in batches of 20 and 5. From a JSON file and a YAML file, in add mode: adds three devices (the newer of two with the same name, and one with a quote in its name), removes one listed under `remove`, warns about one that does not exist, and keeps unlisted devices and a user. In exact mode: adds two (the Linux device, not a Windows one with the same name), removes the unlisted device, and keeps a user and a nested group. |
| idempotence | A second run changes nothing. |
| side_effect | Check mode plans an addition and makes none. A ready access token works, and two throttled responses are retried. Eleven refusals each stop with the expected message: a missing device with `missing: fail`, a duplicate name with `duplicates: fail`, a dynamic group, a Microsoft 365 group, two groups with one name, no such group, a device both wanted and removed, add and exact for one group, a file with a `groups` key, a wrong secret, and nothing to do. |
| verify | The memberships are exactly as expected, no request bound more than 20 devices, every removal used `/$ref`, no query parameter reached the members list, and the counters match: four batches, two removals, two throttled requests, one refused secret. |
| destroy | Stops the mock. It also runs first, to clear a mock left by a failed run. |

## Releasing

1. Move the Unreleased entries in [`CHANGELOG.md`](./CHANGELOG.md) under the new version.
2. Run `just release 1.2.3`. It tags the commit and pushes the tag.
3. The Release workflow creates the GitHub release and imports the tag into Galaxy, which takes the version from the tag. It needs the `GALAXY_API_KEY` secret, which the libre-devops organisation provides. If the import fails, fix the cause and run the Release workflow by hand with the tag to import it again.

## Sources

Retrieved 9 November 2026.

| Topic | Source |
|---|---|
| Adding members, batch limit, permissions, errors, member types | [Add members](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0) |
| Removing members, `/$ref`, dynamic groups | [Remove member](https://learn.microsoft.com/en-us/graph/api/group-delete-members?view=graph-rest-1.0) |
| Reading members, advanced query parameters, page size | [List group members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0) |
| Finding groups | [List groups](https://learn.microsoft.com/en-us/graph/api/group-list?view=graph-rest-1.0), [Get group](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0), [group resource type](https://learn.microsoft.com/en-us/graph/api/resources/group?view=graph-rest-1.0) |
| Finding devices | [List devices](https://learn.microsoft.com/en-us/graph/api/device-list?view=graph-rest-1.0), [Get device](https://learn.microsoft.com/en-us/graph/api/device-get?view=graph-rest-1.0), [device resource type](https://learn.microsoft.com/en-us/graph/api/resources/device?view=graph-rest-1.0) |
| Which filters work by default | [Advanced query capabilities on Microsoft Entra ID objects](https://learn.microsoft.com/en-us/graph/aad-advanced-queries) |
| Permissions | [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference) |
| Paging | [Paging Microsoft Graph data in your app](https://learn.microsoft.com/en-us/graph/paging) |
| Throttling | [Microsoft Graph throttling guidance](https://learn.microsoft.com/en-us/graph/throttling) |
| Sign-in | [OAuth 2.0 client credentials flow on the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow) |
| National clouds | [Microsoft Graph national cloud deployments](https://learn.microsoft.com/en-us/graph/deployments) |
| Defender for Endpoint and Entra groups | [Manage Microsoft Defender settings on devices not enrolled with Intune](https://learn.microsoft.com/en-us/intune/device-security/microsoft-defender/security-settings-management) |

## Licence

[MIT](./LICENSE).
