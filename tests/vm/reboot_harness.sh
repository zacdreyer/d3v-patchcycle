#!/usr/bin/env bash
# L4 VM reboot harness (docs/testing/test-strategy.md §L4).
#
# Exercises the REAL reboot/resume path that mocks cannot: install PatchCycle
# into a disposable QEMU VM, run a cycle that plants the reboot-required
# sentinel, let the VM actually reboot, and verify the resume service
# completes the cycle with a verified boot-id change.
#
# Requirements: qemu-system-x86_64, qemu-img, cloud-localds (or genisoimage),
# curl. Runs nightly in CI (scheduled workflow), never per-PR.
#
# Usage: tests/vm/reboot_harness.sh [--keep] [--image debian-13]
set -euo pipefail

KEEP=0
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
WORK_DIR="$(mktemp -d /tmp/patchcycle-l4.XXXXXX)"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
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
IMAGE="$WORK_DIR/base.qcow2"
[[ -f "$IMAGE" ]] || curl -fsSL -o "$IMAGE" "$BASE_URL"
DISK="$WORK_DIR/vm.qcow2"
qemu-img create -f qcow2 -b "$IMAGE" -F qcow2 "$DISK" 20G >/dev/null

# cloud-init: python3 + a host->guest webhook sink via QEMU user-net hostfwd.
cat > "$WORK_DIR/user-data" <<'EOF'
#cloud-config
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
python3 - "$REPORT_FILE" <<'PYEOF' &
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        with open(sys.argv[1], "w") as fh:
            fh.write(body)
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

server = HTTPServer(("127.0.0.1", 18080), Sink)
threading.Timer(600, server.shutdown).start()
server.serve_forever()
PYEOF
SINK_PID=$!

log "booting VM (this takes a few minutes)…"
qemu-system-x86_64 -machine q35 -cpu max -m 2048 -smp 2 -nographic \
    -drive file="$DISK",if=virtio -drive file="$WORK_DIR/seed.iso",if=virtio \
    -netdev user,id=n0,hostfwd=tcp::10022-:22 \
    -device virtio-net-pci,netdev=n0 \
    -pidfile "$WORK_DIR/vm.pid" &
VM_PID=$!

wait_ssh() {
    for _ in $(seq 1 120); do
        if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -o ConnectTimeout=3 -p 10022 debian@127.0.0.1 true 2>/dev/null; then
            return 0
        fi
        sleep 5
    done
    return 1
}
SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 10022 debian@127.0.0.1"

wait_ssh || { log "SSH timeout"; exit 1; }
log "VM up; installing PatchCycle"
$SSH "sudo apt-get install -y -qq python3-pip python3-venv >/dev/null &&
      sudo pip install --break-system-packages -q /dev/null" 2>/dev/null || true
# Copy the repo in and install.
tar -C "$REPO_ROOT" --exclude=.git --exclude=.venv -cf - . | \
    $SSH "mkdir -p /tmp/pc && tar -C /tmp/pc -xf -"
$SSH "cd /tmp/pc && sudo python3 -m pip install --break-system-packages -q ."

# Configure: webhook to host sink, reboot allowed.
$SSH "sudo tee /etc/d3v-patchcycle/config.toml >/dev/null" <<'EOF'
[maintenance]
schedule = "manual"

[notifications.webhook]
enabled = true
url = "http://10.0.2.2:18080/report"
EOF
$SSH "cd /tmp/pc && sudo d3v-patchcycle install"
$SSH "systemctl is-enabled d3v-patchcycle-resume.service | grep -q enabled"

# Plant the reboot sentinel and run the cycle.
log "starting maintenance cycle (will reboot the VM)"
$SSH "sudo touch /var/run/reboot-required && \
      sudo d3v-patchcycle run --force" || true

# Wait for the reboot + resume to deliver the report.
for i in $(seq 1 60); do
    [[ -s "$REPORT_FILE" ]] && break
    sleep 5
done

[[ -s "$REPORT_FILE" ]] || { log "no report received"; exit 1; }
log "report received:"
cat "$REPORT_FILE"
grep -q '"reboot_completed": *true' "$REPORT_FILE" || {
    log "FAIL: reboot not verified"; exit 1;
}
grep -q '"outcome": *"success"' "$REPORT_FILE" || {
    log "FAIL: outcome not success"; exit 1;
}
log "PASS: full reboot/resume cycle verified"
kill "$SINK_PID" 2>/dev/null || true
kill "$VM_PID" 2>/dev/null || true
