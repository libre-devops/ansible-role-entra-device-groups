# Using libre_devops.entra_device_groups without Galaxy

You do not need Ansible Galaxy, or even the `ansible-galaxy` command, to use this role. A role is a directory: Ansible looks for `libre_devops.entra_device_groups` in a `roles/` directory next to your playbook, then in each directory on `roles_path`. Getting the directory there is the whole job, from GitHub or from a copy inside your own network.

> **Tested:** methods 1 to 3, the offline collections and the git server copy were run on 11 November 2026 with ansible-core 2.16 and 2.21 while Galaxy was unreachable, using `libre_devops.mde_linux` 1.0.0. The steps are the same for every libre_devops role. The AWX, Automation Controller, execution environment and automation hub notes follow those products' documentation and were not tested here.

## Contents

- [Before you start](#before-you-start)
- [Choose a method](#choose-a-method)
- [Method 1: clone it with git](#method-1-clone-it-with-git)
- [Method 2: unpack a release archive](#method-2-unpack-a-release-archive)
- [Method 3: a requirements file](#method-3-a-requirements-file)
- [Collections](#collections)
- [Publishing it inside your organisation](#publishing-it-inside-your-organisation)
- [Checking what you install](#checking-what-you-install)
- [Python and ansible-core versions](#python-and-ansible-core-versions)

## Before you start

**Pick a release.** Releases are git tags such as `1.0.0`, listed on the [releases page](https://github.com/libre-devops/ansible-role-entra-device-groups/releases). Pin one. `main` is where changes land before they are released.

**Keep the name.** The README and every example call the role `libre_devops.entra_device_groups`, so the directory must have that name: `roles/libre_devops.entra_device_groups`. If you give it another name, use that name in your playbooks instead, including in any `include_role`.

## Choose a method

| Method | Needs on the control node | Good for |
|---|---|---|
| [1. Clone it with git](#method-1-clone-it-with-git) | `git` | Keeping the role in your playbook repository as a submodule, or updating with `git`. |
| [2. Unpack a release archive](#method-2-unpack-a-release-archive) | `curl` (or a browser) and `tar` | Control nodes without `git`, and carrying the role into a network with no internet access. |
| [3. A requirements file](#method-3-a-requirements-file) | `ansible-galaxy`, which comes with ansible-core | Installing several roles and collections in one step, from GitHub or your own servers, as CI, AWX, Automation Controller and execution environment builds do. It never contacts Galaxy for an entry that names its own source. |

## Method 1: clone it with git

```bash
mkdir -p roles
git clone --branch 1.0.0 https://github.com/libre-devops/ansible-role-entra-device-groups.git roles/libre_devops.entra_device_groups
```

`--branch` takes a tag too. Git says the checkout is a detached HEAD, which is what you want for a fixed release. To move to a later release:

```bash
git -C roles/libre_devops.entra_device_groups fetch --tags
git -C roles/libre_devops.entra_device_groups checkout 1.1.0
```

If your playbooks are in a git repository, a submodule records the exact commit in it, so everyone who clones the repository gets the same role:

```bash
git submodule add https://github.com/libre-devops/ansible-role-entra-device-groups.git roles/libre_devops.entra_device_groups
git -C roles/libre_devops.entra_device_groups checkout 1.0.0
git add .gitmodules roles/libre_devops.entra_device_groups
git commit -m "Add libre_devops.entra_device_groups 1.0.0"
```

Anyone who clones your repository then runs `git submodule update --init`.

## Method 2: unpack a release archive

GitHub serves every release tag as an archive. No `git` is needed, and the file can be copied anywhere.

```bash
curl -fsSL -o ansible-role-entra-device-groups-1.0.0.tar.gz https://github.com/libre-devops/ansible-role-entra-device-groups/archive/refs/tags/1.0.0.tar.gz
sha256sum ansible-role-entra-device-groups-1.0.0.tar.gz
mkdir -p roles/libre_devops.entra_device_groups
tar -xzf ansible-role-entra-device-groups-1.0.0.tar.gz --strip-components=1 -C roles/libre_devops.entra_device_groups
```

Keep the SHA-256 from the first download and compare it whenever you install the same file again. `--strip-components=1` drops the archive's top directory, which is named after the repository and version, so the files land straight in the role's directory. To update, delete the directory and unpack the new release.

## Method 3: a requirements file

`ansible-galaxy` ships with ansible-core. Despite its name, it only contacts Galaxy for an entry that is just a name. Give each entry a source and it fetches from there:

```yaml
---
roles:
  # From GitHub, or from your own git server (see Publishing it inside your organisation).
  - name: libre_devops.entra_device_groups
    src: https://github.com/libre-devops/ansible-role-entra-device-groups.git
    scm: git
    version: 1.0.0

  # Or from an archive on any HTTPS server, such as an artifact store:
  # - name: libre_devops.entra_device_groups
  #   src: https://artifacts.example.internal/ansible/ansible-role-entra-device-groups-1.0.0.tar.gz
```

```bash
ansible-galaxy role install -r requirements.yml -p roles
```

Add `--force` after changing `version`, or the copy already installed stays. With `scm: git`, `version` also takes a full commit hash, the strictest pin there is.

## Collections

This role needs no collections, so installing the role is all there is to it.

## Publishing it inside your organisation

### On your own git server

Copy the repository with its tags, then point method 1 or 3 at your copy:

```bash
git clone --bare https://github.com/libre-devops/ansible-role-entra-device-groups.git
cd ansible-role-entra-device-groups.git
git push --all https://git.example.internal/ansible/ansible-role-entra-device-groups.git
git push --tags https://git.example.internal/ansible/ansible-role-entra-device-groups.git
```

To pick up new releases later, run in the same directory:

```bash
git fetch --prune --tags origin '+refs/heads/*:refs/heads/*'
git push --all https://git.example.internal/ansible/ansible-role-entra-device-groups.git
git push --tags https://git.example.internal/ansible/ansible-role-entra-device-groups.git
```

Run that on a schedule, or use your git server's own pull mirroring where it has one. Your requirements file then names your server:

```yaml
roles:
  - name: libre_devops.entra_device_groups
    src: https://git.example.internal/ansible/ansible-role-entra-device-groups.git
    scm: git
    version: 1.0.0
```

### As an archive in an artifact store

Download the release archive and record its SHA-256 as in [method 2](#method-2-unpack-a-release-archive), then upload it to anything that serves files over HTTPS: a generic repository in Artifactory or Nexus, or an internal web server. Install it with method 2 from your URL, or with the archive form of [method 3](#method-3-a-requirements-file).

### Private automation hub cannot hold it

Red Hat's private automation hub holds collections and execution environments, not standalone roles like this one. Keep the role on a git server or in an artifact store, and use the hub for the collections.

### AWX and Automation Controller

When they sync a project, AWX and Automation Controller install the roles listed in the project's `roles/requirements.yml`, from the sources it names. Point those at your git server or artifact store. Or commit the role into the project's `roles/` directory with method 1 or 2, and nothing is fetched at all.

### Execution environments

`ansible-builder` installs the roles and collections in the requirements file that the definition names under `dependencies.galaxy`, from the sources that file gives. Use the same requirements file as method 3.

## Checking what you install

- **Pin the commit, not only the tag.** This shows the commit a release tag points at:

  ```bash
  git ls-remote https://github.com/libre-devops/ansible-role-entra-device-groups.git refs/tags/1.0.0
  ```

  Use that hash as `version` in a requirements file, or with `git checkout` in method 1, and the role cannot change under you.
- **Signed commits.** Every commit on `main` is signed, and GitHub shows it as Verified.
- **Archives.** Compare the SHA-256 you recorded every time you install an archive.

## Python and ansible-core versions

### On the control node

`ansible --version` shows both: the first line is the ansible-core version, and `python version` is the Python that ansible-core itself runs on, on the control node. That is not your servers' Python.

This role needs ansible-core 2.16 or later. Distribution packages are often older, and older still on long-lived control nodes, such as Ansible 2.9. A current ansible-core can sit beside them without touching the system's Python:

```bash
python3.12 -m venv ~/ansible
~/ansible/bin/pip install ansible-core
~/ansible/bin/ansible --version
```

RHEL 8 and RHEL 9 both have `python3.12` in AppStream (`dnf install python3.12`).

### On other hosts

This role runs on the control node, which calls Microsoft Graph, so the only Python that matters is the one ansible-core uses there, and any ansible-core this role supports handles that.
