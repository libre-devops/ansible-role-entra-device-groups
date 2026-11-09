<!-- markdownlint-disable first-line-h1 -->

## Overview

<!-- What this pull request changes, and why. -->

## This PR fixes / adds / changes / removes

1. *Replace me*

### Breaking changes

1. *Replace me, or state "None".*

## Testing evidence

<!-- CI run, or the output of the just recipes you ran locally. For changes to the Microsoft
Graph calls, a run against a test tenant with `just live`, with group names, device names, IDs and
tenant details removed. -->

## Checklist

- [ ] `just lint` passes.
- [ ] `just unit` passes.
- [ ] `just test` passes.
- [ ] `README.md`, `meta/argument_specs.yml` and `defaults/main.yml` agree on every variable.
- [ ] `CHANGELOG.md` has an entry under Unreleased.
- [ ] No real tenant, group name, device name, host name, ID or credential is committed. Tests use the mock Microsoft Graph in `molecule/fixtures`.
