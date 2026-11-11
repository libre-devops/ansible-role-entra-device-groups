# Libre DevOps Ansible role libre_devops.entra_device_groups. Run `just` to list the recipes.
#
# CI runs these same recipes, so a local run shows what CI will do. You need uv
# (https://docs.astral.sh/uv/). The tests run on this machine against a mock of Microsoft
# Graph, so no containers and no tenant are needed. Install just with
# `uv tool install rust-just` or `brew install just`.

set shell := ["bash", "-euo", "pipefail", "-c"]

role := "libre_devops.entra_device_groups"
github_owner := "libre-devops"
github_repo := "ansible-role-entra-device-groups"

# List the recipes.
default:
    @just --list

# Install the pinned Python tools.
deps:
    uv sync --locked

# Lint the YAML, the role (ansible-lint, production profile), the workflows (zizmor) and the
# Python (ruff).
lint: deps
    uv run yamllint .
    uv run ansible-lint
    uv run zizmor --offline .github/workflows
    uv run ruff check library tests molecule/fixtures
    uv run ruff format --check library tests molecule/fixtures

# Run the module's unit tests.
unit: deps
    uv run pytest

# List the Molecule scenarios.
scenarios:
    @find molecule -mindepth 2 -maxdepth 2 -name molecule.yml -printf '%h\n' | xargs -n1 basename | sort

# Run a Molecule scenario against the mock Microsoft Graph: just test
test scenario="default": deps
    uv run molecule test -s '{{ scenario }}'

# Run every scenario.
test-all: deps
    for s in $(just scenarios); do just test "$s"; done

# Start the mock Microsoft Graph in the foreground, for trying the role by hand.
mock port="18944":
    uv run python molecule/fixtures/mock_graph.py --port '{{ port }}'

# Run the example playbook against a real tenant: just live -e @groups.json -e @vault.yml --ask-vault-pass
live *args: deps
    mkdir -p .ansible/roles && ln -sfn "$PWD" '.ansible/roles/{{ role }}'
    uv run ansible-playbook examples/playbook.yml {{ args }}

# Scan the full git history for secrets (needs gitleaks on PATH).
secrets:
    gitleaks git --redact --verbose .

# Tag a release and push the tag. CI then imports it into Galaxy: just release 1.0.0
release version:
    [[ '{{ version }}' =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be semver, for example 1.0.0" >&2; exit 1; }
    git tag -a '{{ version }}' -m 'Release {{ version }}'
    git push origin '{{ version }}'

# Import a release tag into Galaxy and wait for the result (needs GALAXY_API_KEY): just galaxy-import 1.0.0
galaxy-import tag:
    [[ '{{ tag }}' =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "tag must be semver, for example 1.0.0" >&2; exit 1; }
    uv run ansible-galaxy role import --api-key "${GALAXY_API_KEY:?set GALAXY_API_KEY}" --branch '{{ tag }}' --role-name '{{ replace_regex(role, "^.*\\.", "") }}' '{{ github_owner }}' '{{ github_repo }}'
