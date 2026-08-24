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

KEEP=0
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
WORK_DIR="$(mktemp -d /tmp/patchcycle-l4.XXXXXX)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPORT_FILE="$WORK_DIR/report.json"

log() { printf '[l4] %s\n' "$*"; }
cleanup() {
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

# SSH keypair for cloud-init -> guest access.
ssh-keygen -q -t ed25519 -N "" -f "$WORK_DIR/id_ed25519"
PUBKEY="$(cat "$WORK_DIR/id_ed25519.pub")"

IMAGE="$WORK_DIR/base.qcow2"
[[ -f "$IMAGE" ]] || { log "downloading base image"; curl -fsSL -o "$IMAGE" "$BASE_URL"; }
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
packages: [python3, python3-venv]
runcmd:
  - mkdir -p /var/lib/d3v-patchcycle /etc/d3v-patchcycle
EOF
cat > "$WORK_DIR/meta-data" <<'EOF'
instance-id: patchcycle-l4
local-hostname: patchcycle-test
EOF
cloud-localds "$WORK_DIR/seed.iso" "$WORK_DIR/user-data" "$WORK_DIR/meta-data"

# Host-side webhook listener receives the final report (10.0.2.2 = host).
python3 "$SCRIPT_DIR/webhook_sink.py" "$REPORT_FILE" &
SINK_PID=$!

log "booting VM (this takes a few minutes)…"
qemu-system-x86_64 -machine q35 -cpu max -m 2048 -smp 2 -nographic \
    -drive file="$DISK",if=virtio -drive file="$WORK_DIR/seed.iso",if=virtio \
    -netdev user,id=n0,hostfwd=tcp::10022-:22 \
    -device virtio-net-pci,netdev=n0 \
    -pidfile "$WORK_DIR/vm.pid" &
VM_PID=$!

SSH="ssh -i $WORK_DIR/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -p 10022 debian@127.0.0.1"

wait_ssh() {
    for _ in $(seq 1 120); do
        if $SSH true 2>/dev/null; then return 0; fi
        sleep 5
    done
    return 1
}
wait_ssh || { log "SSH timeout"; exit 1; }
log "VM up; installing PatchCycle"

# Copy the repo in and install (stdlib-only runtime).
tar -C "$REPO_ROOT" --exclude=.git --exclude=.venv -cf - . | \
    $SSH "mkdir -p /tmp/pc && tar -C /tmp/pc -xf -"
$SSH "cd /tmp/pc && sudo python3 -m pip install --quiet --break-system-packages ."

# Configure: webhook to host sink, reboot allowed.
$SSH "sudo tee /etc/d3v-patchcycle/config.toml >/dev/null" <<'EOF'
[maintenance]
schedule = "manual"

[notifications.webhook]
enabled = true
url = "http://10.0.2.2:18080/report"
EOF
$SSH "sudo d3v-patchcycle install"
$SSH "systemctl is-enabled d3v-patchcycle-resume.service | grep -q enabled"
log "resume unit enabled"

# Plant the reboot sentinel and run the cycle (the VM will reboot).
log "starting maintenance cycle (will reboot the VM)"
$SSH "sudo touch /var/run/reboot-required && sudo d3v-patchcycle run --force" || true

# Wait for the reboot + resume to deliver the report.
for _ in $(seq 1 90); do
    [[ -s "$REPORT_FILE" ]] && break
    sleep 5
done

[[ -s "$REPORT_FILE" ]] || { log "FAIL: no report received"; exit 1; }
log "report received:"
cat "$REPORT_FILE"
grep -q '"reboot_completed": *true' "$REPORT_FILE" || { log "FAIL: reboot not verified"; exit 1; }
grep -q '"outcome": *"success"' "$REPORT_FILE" || { log "FAIL: outcome not success"; exit 1; }
log "PASS: full reboot/resume cycle verified"
kill "$SINK_PID" 2>/dev/null || true
kill "$VM_PID" 2>/dev/null || true
