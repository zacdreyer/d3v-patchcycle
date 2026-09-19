# Install and run D3V PatchCycle

Use a test/staging machine first while this software is a release candidate.
Supported targets are **x86_64 Linux hosts running systemd**. Installation needs
root, working distribution repositories, and HTTPS access to those repositories.
The application has no third-party Python runtime dependencies.

## 1. Choose the matching download

Download the ZIP and `SHA256SUMS.txt` from the same GitHub release:
https://github.com/zacdreyer/d3v-patchcycle/releases

| Host | ZIP suffix | Installer | Python |
|---|---|---|---|
| Ubuntu 22.04 | ubuntu-22.04-x86_64.zip | install-ubuntu.sh | Provision 3.11+ first; see below |
| Ubuntu 24.04 | ubuntu-24.04-x86_64.zip | install-ubuntu.sh | Distribution Python |
| Debian 12 | debian-12-x86_64.zip | install-debian.sh | Distribution Python |
| Debian 13 | debian-13-x86_64.zip | install-debian.sh | Distribution Python |
| Rocky Linux 9 | rocky-9-x86_64.zip | install-rocky.sh | Repository python3.11 |
| AlmaLinux 9 | almalinux-9-x86_64.zip | install-almalinux.sh | Repository python3.11 |
| Fedora 44 | fedora-44-x86_64.zip | install-fedora.sh | Distribution Python |

Every ZIP starts with `d3v-patchcycle-<version>-`. One release contains all seven
OS bundles; they share the same wheel and version. Do not use them on RHEL,
CentOS, ARM, Windows, macOS, or other unlisted versions.

For example, for Debian 13 and release candidate 1.0.0rc2:

```bash
# In the directory containing your downloaded ZIP and release SHA256SUMS.txt:
grep '  d3v-patchcycle-1.0.0rc2-debian-13-x86_64.zip$' SHA256SUMS.txt > selected-checksum.txt
test -s selected-checksum.txt && sha256sum --check selected-checksum.txt || exit 1
mkdir patchcycle-install
unzip d3v-patchcycle-1.0.0rc2-debian-13-x86_64.zip -d patchcycle-install
cd patchcycle-install
sha256sum --check SHA256SUMS.txt || exit 1
sudo bash install-debian.sh
```

Stop if either checksum check fails. Checksums detect corrupt/mismatched
files; their authenticity depends on obtaining the manifest from the trusted
release page. Replace the filename and installer for your OS/version. Install
`unzip` using your distribution package manager if it is unavailable.

The installer adds Python/venv prerequisites, installs the bundled wheel without
contacting PyPI, and creates:

- `/opt/d3v-patchcycle/venv`: isolated Python environment;
- `/usr/local/bin/d3v-patchcycle`: command entrypoint;
- `/etc/d3v-patchcycle/config.toml`: root-owned mode 0600 starter configuration;
- `/var/lib/d3v-patchcycle`: private state/history;
- systemd maintenance and boot-resume services.

**Maintenance starts disabled, scheduling is manual, and reboot policy is
notify-only.** Installation does not start a maintenance cycle or reboot.
This is a first-install script: existing installations/configurations are refused.
For upgrades use section 5, which preserves your configuration and history.

### Ubuntu 22.04 prerequisite

Ubuntu 22.04's default Python 3.10 is too old. Provision an administrator-approved
Python 3.11+ with `venv` and `ensurepip` support. Do not replace system Python.
The installer deliberately does not add a third-party package repository.

```bash
# Example path; substitute the approved interpreter installed on your host:
/usr/bin/python3.11 -I -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 11)'
sudo bash install-ubuntu.sh --python /usr/bin/python3.11
```

The interpreter and its parent directories must be administrator-controlled.
For a fresh deployment without this prerequisite, Ubuntu 24.04 is simpler.

### Image builds

`--package-only` installs the wheel, entrypoint and disabled config without
calling systemd. On first boot of the actual systemd host run
`sudo d3v-patchcycle install`, then follow section 2. Containers are used to test
installation/provider compatibility; they do not prove host reboot recovery.

## 2. Review configuration and preview

```bash
sudo d3v-patchcycle version
sudo d3v-patchcycle detect
sudoedit /etc/d3v-patchcycle/config.toml
sudo d3v-patchcycle config-check
sudo d3v-patchcycle run --dry-run
```

The preview refreshes package metadata; it does not install updates, run hooks,
write cycle state, or reboot. Add your notification destination and health checks
before enabling automation. The complete configuration reference is at:
https://github.com/zacdreyer/d3v-patchcycle/blob/main/docs/specifications/configuration.md

Set `maintenance.enabled = true` when ready for a real maintenance cycle.
Keep `schedule = "manual"` and `reboot.policy = "notify_only"` for the first run.

```bash
sudo d3v-patchcycle config-check
sudo d3v-patchcycle install
sudo d3v-patchcycle run
sudo d3v-patchcycle status
sudo d3v-patchcycle history
sudo journalctl -u d3v-patchcycle.service -u d3v-patchcycle-resume.service -n 100
```

`run` performs real package updates. Exit 6 means an administrator reboot is
required under the notify-only policy; it is not an installation failure.
Reports are also saved in `/var/lib/d3v-patchcycle/history/*.report.txt`.
An active SSH/login session blocks automatic reboots by default.

## 3. Enable scheduled maintenance

Edit the existing sections in the configuration (do not duplicate TOML tables).
Example settings after your first successful staging cycle:

```toml
[maintenance]
enabled = true
schedule = "weekly"
day = "sunday"
time = "02:00"
window_start = "02:00"
window_end = "05:00"

[reboot]
policy = "when_required"
existing_pending = "reboot_first"
allow_if_users_logged_in = false
```

```bash
sudo d3v-patchcycle config-check
sudo d3v-patchcycle install
sudo systemctl is-enabled d3v-patchcycle-resume.service
systemctl list-timers d3v-patchcycle.timer
```

Times are the host's local timezone (`timedatectl`). The timer is persistent:
a missed scheduled run may start when the timer is enabled or after boot.
The maintenance window still gates disruptive actions. To pause automation,
set `maintenance.enabled = false` and disable the timer:
`sudo systemctl disable --now d3v-patchcycle.timer`.
Do not disable the resume service while a cycle is pending.

## 4. Notification secrets

Set `smtp_password_env` or an `env:` webhook header in config. Put the actual
secret in a root-owned, mode 0600 environment file without putting it in shell
history:

```bash
sudo touch /etc/d3v-patchcycle/environment
sudo chown root:root /etc/d3v-patchcycle/environment
sudo chmod 0600 /etc/d3v-patchcycle/environment
sudoedit /etc/d3v-patchcycle/environment
```

Example file entry: `PATCHCYCLE_SMTP_PASSWORD="replace-with-your-secret"`.
Systemd services load this file. An interactive `sudo d3v-patchcycle run` does
not load it automatically; use `sudo systemctl start d3v-patchcycle.service`
for a configured service run that needs these secrets. Use STARTTLS for remote
SMTP credentials and HTTPS for webhooks.

## 5. Upgrade an existing installation

Download, extract, and verify the new bundle as in section 1. Record whether
the timer was enabled, disable it, and confirm there is no running service or
pending cycle. Finish or deliberately recover any pending cycle before upgrading.

```bash
systemctl is-enabled d3v-patchcycle.timer
sudo systemctl disable --now d3v-patchcycle.timer
systemctl is-active d3v-patchcycle.service d3v-patchcycle-resume.service
sudo d3v-patchcycle status
# Continue only when neither service is active and status is IDLE.
# From the new extracted bundle; substitute its exact wheel filename:
sudo /opt/d3v-patchcycle/venv/bin/python -I -m pip --isolated install --no-index --no-deps --upgrade ./d3v_patchcycle-1.0.0rc2-py3-none-any.whl
sudo d3v-patchcycle version
sudo d3v-patchcycle config-check
sudo d3v-patchcycle install
```

Back up configuration/state before upgrading. Existing config, history, and
secrets are preserved. `install` reapplies the configured schedule and enables
the timer for a non-manual schedule; leave maintenance disabled if you intend
to keep automation paused. Never install into distribution Python with `sudo pip`.

## 6. Uninstall and troubleshooting

```bash
sudo d3v-patchcycle uninstall          # retain config and history
# Optional deliberate deletion of config and state/history:
sudo d3v-patchcycle uninstall --purge
sudo /opt/d3v-patchcycle/venv/bin/python -m pip uninstall d3v-patchcycle
```

Purge retains the Python environment, external logs, and environment secret
file. Remove those separately only when no longer needed.

| Symptom | Action |
|---|---|
| Unsupported OS/architecture | Check section 1; do not bypass the gate |
| Python/venv unavailable | Install the listed prerequisites; on Ubuntu 22.04 provision approved Python 3.11+ |
| Existing installation refused | Use section 5; do not delete state to force installation |
| Interrupted first installation | Inspect the error and existing files; retain config/state and complete the venv/wheel steps from section 5 before rerunning `d3v-patchcycle install` |
| Unsafe owner/mode | Config must be root:root 0600; installation ancestors must be root-owned and not group/world-writable |
| Package manager lock held | Let the other updater finish; never delete its locks |
| No scheduled runs | Check enabled flag, manual schedule, timer listing, and local timezone |
| Reboot blocked | Check sessions, window, policy, and enabled resume service |
| Notification failure | Inspect status/report, network/TLS, credentials, and whether systemd loaded the secret file |

Release gates include unit tests, installation on all seven OS images, native
APT/DNF integration, and normal/power-loss recovery on a Debian VM. Per-OS
bundles do not imply that a real reboot VM has been tested for every OS.
