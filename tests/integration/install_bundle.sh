#!/usr/bin/env bash
# Runs only in a disposable OS container. Package changes are intentional.
set -euo pipefail
. /etc/os-release
if [[ $ID == ubuntu && $VERSION_ID == 22.04 ]]; then
    # Explicit CI fixture provisioning; the shipped installer never adds a PPA.
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get install -y -qq python3.11 python3.11-venv
fi
cd /bundle
sha256sum --check SHA256SUMS.txt
bash "install-$1.sh" --package-only
d3v-patchcycle version
d3v-patchcycle detect
d3v-patchcycle config-check
/opt/d3v-patchcycle/venv/bin/python -I - <<'PY'
from pathlib import Path
from patchcycle.config import load_config
cfg = load_config(Path('/etc/d3v-patchcycle/config.toml'))
assert not cfg.maintenance.enabled
assert cfg.maintenance.schedule == 'manual'
assert cfg.reboot.policy == 'notify_only'
assert not Path('/var/lib/d3v-patchcycle/state.json').exists()
PY
if bash "install-$1.sh" --package-only; then
    echo 'ERROR: reinstall should refuse existing installation' >&2
    exit 1
fi
echo 'PASS: verified bundle installed; maintenance disabled; existing installation preserved'
