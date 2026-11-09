# Contributing

Issues and pull requests are welcome. Open an issue first for anything large, so we can agree the approach before you write it.

## Set up

You need [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just). Then:

```bash
just deps      # pinned Python tools
just lint      # yamllint, ansible-lint (production profile), zizmor, ruff
just unit      # the module's unit tests
just test      # Molecule against the mock Microsoft Graph
```

`just` on its own lists every recipe.

## Rules

- Every variable is in `defaults/main.yml`, `meta/argument_specs.yml` and the README variables table, with the same default and description in all three.
- Internal variables start with `__entra_device_groups_` and are registered or set in tasks.
- Use fully qualified module names, name every task, and keep `ansible-lint` on the production profile clean. A `noqa` needs a comment saying why.
- Runs must be idempotent: `just test` runs an idempotence check. A run that finds every group as it should be sends no changes.
- The module only changes device members, and only removes through `/$ref`. Keep it that way, and keep the tests that prove it.
- The module uses the Python standard library and `ansible.module_utils` only. Add a unit test for any new behaviour in `library/`.
- Never commit a real tenant, group name, device name, host name, object ID or credential. Tests use the mock in `molecule/fixtures`. When Microsoft Graph behaves differently from the mock, change the mock to match and link the Microsoft documentation you checked.
- Docs and comments are in British English.

## Commits and releases

Write commit subjects in the imperative, wrapped at 72 characters. Add an entry under Unreleased in `CHANGELOG.md`.

Maintainers release with `just release 1.2.3`, which tags and pushes. The Release workflow then creates the GitHub release and imports the tag into Galaxy.
