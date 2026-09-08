#!/usr/bin/env bash
# L4 VM reboot harness (docs/testing/test-strategy.md §L4).
#
# Exercises the REAL reboot/resume path that mocks cannot: install PatchCycle
# into a disposable QEMU VM, run a cycle that plants the reboot-required
# sentinel, let the VM actually reboot, and verify the resume service
# completes the cycle with a verified boot-id change.
#
# Requirements: qemu-system-x86_64, qemu-img, cloud-localds, curl, ssh,
# ssh-keygen, python3. Runs nightly/manually in CI, never per-PR.
#
# Usage: tests/vm/reboot_harness.sh [--keep] [--image <qcow2-url>]
set -euo pipefail

KEEP=1
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
WORK_DIR="$(mktemp -d /tmp/patchcycle-l4.XXXXXX)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPORT_FILE="$WORK_DIR/report.json"
SSH_PORT=${SSH_PORT:-10022}
POWER_CUT=${POWER_CUT:-0}

log() { printf '[l4] %s\n' "$*"; }
cleanup() {
    [[ -z ${VM_PID:-} ]] || kill "$VM_PID" 2>/dev/null || true
    if [[ $KEEP -eq 0 ]]; then rm -rf "$WORK_DIR"; else log "kept: $WORK_DIR"; fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep) KEEP=1 ;;
        --image) BASE_URL="$2"; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

log "workdir: $WORK_DIR"

python3 -m venv "$WORK_DIR/build-venv"
"$WORK_DIR/build-venv/bin/pip" install --quiet build
"$WORK_DIR/build-venv/bin/python" -m build --wheel --outdir "$WORK_DIR/artifacts" "$REPO_ROOT" > "$WORK_DIR/build.log" 2>&1
sha256sum "$WORK_DIR"/artifacts/*.whl > "$WORK_DIR/artifact-sha256.txt"

# SSH keypair for cloud-init -> guest access.
ssh-keygen -q -t ed25519 -N "" -f "$WORK_DIR/id_ed25519"
PUBKEY="$(cat "$WORK_DIR/id_ed25519.pub")"

IMAGE="$WORK_DIR/base.qcow2"
[[ -f "$IMAGE" ]] || { log "downloading base image"; curl --retry 3 --retry-all-errors -fsSL -o "$IMAGE" "$BASE_URL"; }
DISK="$WORK_DIR/vm.qcow2"
qemu-img create -f qcow2 -b "$IMAGE" -F qcow2 "$DISK" 20G >/dev/null

# cloud-init: python3 + authorized key + dirs.
cat > "$WORK_DIR/user-data" <<EOF
#cloud-config
hostname: patchcycle-test
users:
  - name: debian
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $PUBKEY
package_update: true
packages: [python3, python3-venv, python3-pip, dpkg-dev]
runcmd:
  - mkdir -p /var/lib/d3v-patchcycle /etc/d3v-patchcycle
EOF
cat > "$WORK_DIR/meta-data" <<'EOF'
instance-id: patchcycle-l4
local-hostname: patchcycle-test
EOF
cloud-localds "$WORK_DIR/seed.iso" "$WORK_DIR/user-data" "$WORK_DIR/meta-data"

log "booting VM (this takes a few minutes)…"
ACCEL=tcg
[[ ! -r /dev/kvm || ! -w /dev/kvm ]] || ACCEL=kvm
start_vm() {
qemu-system-x86_64 -accel "$ACCEL" -machine q35 -cpu max -m 2048 -smp 2 -nographic \
    -drive file="$DISK",if=virtio -drive file="$WORK_DIR/seed.iso",if=virtio \
    -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
    -device virtio-net-pci,netdev=n0 \
    -pidfile "$WORK_DIR/vm.pid" > "$WORK_DIR/serial.log" 2>&1 &
VM_PID=$!
}
start_vm

SSH="ssh -i $WORK_DIR/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -p $SSH_PORT debian@127.0.0.1"

wait_ssh() {
    for _ in $(seq 1 120); do
        if $SSH true 2>/dev/null; then return 0; fi
        sleep 5
    done
    return 1
}
wait_ssh || { log "SSH timeout"; exit 1; }
$SSH "sudo cloud-init status --wait" > "$WORK_DIR/cloud-init.log" 2>&1
log "VM up; installing PatchCycle"

# Install the built artifact, not an editable source checkout.
tar -C "$WORK_DIR/artifacts" -cf - . | \
    $SSH "mkdir -p /tmp/pc && tar -C /tmp/pc -xf -"
$SSH "sudo python3 -m venv /opt/pc-venv && sudo /opt/pc-venv/bin/pip install --quiet /tmp/pc/*.whl"
$SSH "sudo tee /opt/pc-sink.py >/dev/null" < "$REPO_ROOT/tests/vm/webhook_sink.py"

# SSH polling is an intentional active session in this disposable guest.
$SSH "sudo install -m 600 /dev/null /etc/d3v-patchcycle/config.toml && sudo tee /etc/d3v-patchcycle/config.toml >/dev/null" <<'EOF'
[maintenance]
schedule = "manual"

[reboot]
allow_if_users_logged_in = true
existing_pending = "continue_then_reboot"

[notifications.webhook]
enabled = true
url = "http://127.0.0.1:18080/report"
EOF
$SSH "sudo mkdir -p /var/lib/patchcycle-l4"
$SSH "sudo touch /var/lib/patchcycle-l4/disposable && sudo tee /opt/pc-fixture.py >/dev/null" < "$REPO_ROOT/tests/vm/package_fixture.py"
PREPARE=prepare
[[ "$POWER_CUT" != 1 ]] || PREPARE=prepare-power-cut
$SSH "sudo python3 /opt/pc-fixture.py $PREPARE" > "$WORK_DIR/package-prepare.log" 2>&1
$SSH "sudo tee /etc/systemd/system/patchcycle-l4-sink.service >/dev/null" <<'EOF'
[Unit]
Description=Disposable L4 webhook capture
Before=d3v-patchcycle-resume.service
[Service]
ExecStart=/usr/bin/python3 /opt/pc-sink.py /var/lib/patchcycle-l4/report.json
Restart=always
[Install]
WantedBy=multi-user.target
EOF
$SSH "sudo systemctl daemon-reload && sudo systemctl enable --now patchcycle-l4-sink.service"
$SSH "sudo /opt/pc-venv/bin/d3v-patchcycle install"
$SSH "systemctl is-enabled d3v-patchcycle-resume.service | grep -q enabled"
log "resume unit enabled"

# Plant the reboot sentinel and run the cycle (the VM will reboot).
log "starting maintenance cycle (will reboot the VM)"
$SSH "cat /proc/sys/kernel/random/boot_id" > "$WORK_DIR/boot-before.txt"
$SSH "sudo touch /var/run/reboot-required && sudo systemctl start --no-block d3v-patchcycle.service"

if [[ "$POWER_CUT" == 1 ]]; then
    READY=0
    for _ in $(seq 1 120); do
        if $SSH "sudo test -f /var/lib/patchcycle-l4/transaction-started" 2>/dev/null; then
            READY=1
            break
        fi
        sleep 5
    done
    [[ "$READY" == 1 ]] || { log "FAIL: transaction did not start"; exit 1; }
    $SSH "sudo cat /var/lib/d3v-patchcycle/state.json" > "$WORK_DIR/pre-cut-state.json"
    log "cutting power to disposable guest during package postinst"
    kill -KILL "$VM_PID"
    wait "$VM_PID" 2>/dev/null || true
    start_vm
    wait_ssh || { log "SSH timeout after power restore"; exit 1; }
fi

# Wait for the reboot + resume to deliver the report.
for _ in $(seq 1 240); do
    if $SSH "sudo cat /var/lib/patchcycle-l4/report.json" > "$REPORT_FILE.tmp" 2>/dev/null; then
        mv "$REPORT_FILE.tmp" "$REPORT_FILE"
        break
    fi
    sleep 5
done

$SSH "sudo journalctl -u 'd3v-patchcycle*' --no-pager" > "$WORK_DIR/cycle.log"
[[ -s "$REPORT_FILE" ]] || { log "FAIL: no report received"; exit 1; }
$SSH "cat /proc/sys/kernel/random/boot_id" > "$WORK_DIR/boot-after.txt"
cmp -s "$WORK_DIR/boot-before.txt" "$WORK_DIR/boot-after.txt" && { log "FAIL: boot ID unchanged"; exit 1; }
log "report received:"
cat "$REPORT_FILE"
python3 - "$REPORT_FILE" "$POWER_CUT" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    report = json.load(stream)
if sys.argv[2] == '1':
    assert report['outcome'] == 'failed', report
    assert report['error']['kind'] == 'unexpected-reboot', report
    assert report['error']['stage'] == 'UPGRADING', report
    assert report['reboot_completed'] is False, report
    sys.exit(0)
assert report['outcome'] in ('success', 'no_updates'), report
assert report['reboot_completed'] is True, report
assert report['error'] is None, report
assert report['packages_upgraded'] >= 1, report
assert all(check['ok'] for check in report['health_results']), report
PY
VERIFY=verify
[[ "$POWER_CUT" != 1 ]] || VERIFY=verify-power-cut
$SSH "sudo python3 /opt/pc-fixture.py $VERIFY" > "$WORK_DIR/package-verify.log" 2>&1
$SSH "sudo python3 /opt/pc-fixture.py verify-archive" > "$WORK_DIR/archive-verify.log" 2>&1
$SSH "sudo /opt/pc-venv/bin/d3v-patchcycle config-check && sudo /opt/pc-venv/bin/d3v-patchcycle status && sudo /opt/pc-venv/bin/d3v-patchcycle history && sudo /opt/pc-venv/bin/d3v-patchcycle install && sudo /opt/pc-venv/bin/d3v-patchcycle uninstall && sudo test -f /etc/d3v-patchcycle/config.toml && sudo test -d /var/lib/d3v-patchcycle/history && sudo /opt/pc-venv/bin/d3v-patchcycle install" > "$WORK_DIR/operator-smoke.log" 2>&1
log "PASS: full reboot/resume cycle verified"
kill "$VM_PID" 2>/dev/null || true
