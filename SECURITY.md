# Security policy

## Reporting a vulnerability

Report vulnerabilities in this role privately, through [GitHub's private vulnerability reporting](https://github.com/libre-devops/ansible-role-entra-device-groups/security/advisories/new). Do not open a public issue.

## What the role handles that is sensitive

- **Credentials.** The client secret is only sent to Microsoft Entra ID's token endpoint (`entra_device_groups_login_url`). The token request uses `no_log`, the client secret and access token are marked `no_log` in the role's argument specification, and the module marks the token `no_log` too. Keep them in Ansible Vault or a secret store.
- **The access token.** The module only sends it to the host in `entra_device_groups_graph_url`, and stops if a next-page link from Microsoft Graph points anywhere else. It never returns the token. Redirects are not followed.
- **Permissions.** The app needs `GroupMember.ReadWrite.All` and `Device.ReadWrite.All`. Together they let it change the members of any group it can see, and change device properties. Use a dedicated app registration for this role, give it nothing else, and keep its secret short-lived.
- **Deleting objects.** Microsoft Graph deletes a member object itself when a removal is sent without `/$ref` and the app may manage that object. The module builds every removal through one function that checks both IDs are GUIDs and always ends the path in `/$ref`, and the tests check every removal.
- **Group and device lists.** Group names, device names and object IDs describe your estate. The examples hold made-up values only, and `.gitignore` keeps `groups.json` and `vault.yml` at the top of a clone out of git. Keep real lists in a private repository.
- **TLS.** Certificates are checked unless you set `entra_device_groups_validate_certs: false`, which is only for the tests against the local mock.
