# Changelog

Notable changes to this role. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `docs/using-without-galaxy.md`, linked from the README's Installation section: installing the role without Galaxy (git, release archives, requirements files that name their own source), getting its collections with no Galaxy access, keeping a copy on your own git server or artifact store, AWX, Automation Controller and execution environments, and the ansible-core and Python versions it needs.

### Changed

- The Release workflow imports the release's own tag into Galaxy, rather than the default branch, and can be run by hand with a tag to import that tag again.

## 1.0.0 - 2026-11-11

### Added

- First release.
- The `entra_device_group_membership` module: makes the device members of Microsoft Entra ID security groups match a desired state through Microsoft Graph v1.0, using only the Python standard library and Ansible's HTTP helper.
- Groups by exact display name or object ID, stopping when a name matches no group or several. Dynamic membership, Microsoft 365 and other non-security groups are refused, and role-assignable groups bring a warning.
- Devices by exact display name (with quotes escaped), object ID or Entra device ID, with an optional operating system filter, a choice of what to do with duplicate names (`newest`, `all` or `fail`) and with missing devices (`warn` or `fail`).
- `add` and `exact` modes and a `remove` list. Only device members are ever changed.
- Additions in batches of up to 20 with `members@odata.bind`, falling back to one at a time when a batch is refused, so a device that is already a member counts as unchanged. Removals always through `/$ref`.
- Paging through `@odata.nextLink`, only ever to the configured Microsoft Graph host. Retries for 429, 503 and 504, waiting for `Retry-After`, and for replication delays on new groups.
- Check mode and diff mode.
- Desired state from variables, from JSON or YAML files (one entry, or a `device_groups` list), or both, combined per group.
- Sign-in with client credentials or a ready access token, and endpoints for the national clouds.
- The `entra_device_groups_results` fact and a one-line summary per group.
- An example playbook that takes `-e @groups.json`, with example groups and vault files.
- Unit tests, and a Molecule scenario against a mock of Microsoft Graph.
