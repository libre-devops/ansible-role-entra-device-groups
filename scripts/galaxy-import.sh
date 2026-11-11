#!/usr/bin/env bash
# Imports a release of this role into Ansible Galaxy, waits for the import to
# finish and prints Galaxy's log of it. The Release workflow runs it.
#
# Usage: GALAXY_API_KEY=... scripts/galaxy-import.sh <namespace> <role> <github owner> <github repo> <tag>
#
# GALAXY_API and GALAXY_POLL_SECONDS change the API address and the polling
# interval, for testing against a stand-in server.
#
# This calls Galaxy's import API itself rather than `ansible-galaxy role
# import`, because the GitHub organisation (libre-devops) and the Galaxy
# namespace (libre_devops) are spelt differently. Galaxy checks the request's
# github_user against the namespace name, and clones
# github.com/<github_user>/<repo> unless the request gives another clone URL,
# which ansible-galaxy cannot send. Sent the organisation name instead, Galaxy
# answers HTTP 500.

set -euo pipefail

usage="usage: galaxy-import.sh <namespace> <role> <github owner> <github repo> <tag>"
namespace=${1:?$usage}
role=${2:?$usage}
owner=${3:?$usage}
repo=${4:?$usage}
tag=${5:?$usage}
api=${GALAXY_API:-https://galaxy.ansible.com/api/v1}
: "${GALAXY_API_KEY:?set GALAXY_API_KEY to an API token for the $namespace namespace}"

if [[ ! $tag =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	echo "galaxy-import.sh: $tag is not a semver tag such as 1.0.0" >&2
	exit 2
fi

# The token goes to curl in a file only this user can read, never on the
# command line.
headers=$(mktemp)
trap 'rm -f -- "$headers"' EXIT
chmod 600 "$headers"
printf 'Authorization: Token %s\n' "$GALAXY_API_KEY" >"$headers"

field() {
	python3 -c 'import json, sys; print(json.load(sys.stdin)["results"][0][sys.argv[1]])' "$1"
}

body=$(python3 -c 'import json, sys; print(json.dumps(dict(zip(
	["github_user", "github_repo", "alternate_role_name", "alternate_clone_url", "github_reference"], sys.argv[1:]))))' \
	"$namespace" "$repo" "$role" "https://github.com/$owner/$repo.git" "$tag")

response=$(curl -sS --fail-with-body -H @"$headers" -H 'Content-Type: application/json' --data "$body" "$api/imports/")
id=$(field id <<<"$response")
echo "Galaxy import $id started: $namespace.$role $tag from github.com/$owner/$repo" >&2

state=PENDING
for _ in $(seq 1 90); do
	status=$(curl -sS --fail-with-body -H @"$headers" "$api/imports/?id=$id")
	state=$(field state <<<"$status")
	case $state in
	SUCCESS | FAILED | CANCELED | SKIPPED) break ;;
	esac
	sleep "${GALAXY_POLL_SECONDS:-10}"
done

python3 -c 'import json, sys
for message in json.load(sys.stdin)["results"][0]["summary_fields"]["task_messages"]:
    print(message["message_type"] + ": " + message["message_text"])' <<<"$status"

if [[ $state != SUCCESS ]]; then
	echo "galaxy-import.sh: import $id ended in state $state" >&2
	exit 1
fi
echo "Imported $namespace.$role $tag: https://galaxy.ansible.com/ui/standalone/roles/$namespace/$role/" >&2
