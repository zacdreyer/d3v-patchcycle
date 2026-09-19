#!/usr/bin/env bash
# Called by the OS-specific entrypoints; never pipe this script into a shell.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
OS_TYPE=$1
shift
PACKAGE_ONLY=false
PYTHON_BIN=
while (($#)); do
    case "$1" in
        --help|-h)
            echo "Usage: sudo bash install-${OS_TYPE}.sh [--python /absolute/python3.11+] [--package-only]"
            echo "First installation only. Installs prerequisites and local verified wheel; maintenance stays disabled."
            echo "--package-only skips systemd integration (for image builds)."
            exit 0 ;;
        --python) [[ $# -ge 2 ]] || { echo 'Missing --python path' >&2; exit 2; }; PYTHON_BIN=$2; shift 2 ;;
        --package-only) PACKAGE_ONLY=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 2; }
[[ $(uname -m) == x86_64 ]] || { echo 'Only x86_64 is supported.' >&2; exit 2; }
# os-release is administrator-controlled system configuration.
. /etc/os-release
[[ $ID == "$OS_TYPE" ]] || { echo "This installer is for $OS_TYPE, detected $ID." >&2; exit 2; }
case "$ID:$VERSION_ID" in
    ubuntu:22.04|ubuntu:24.04|debian:12|debian:13|rocky:9|rocky:9.*|almalinux:9|almalinux:9.*|fedora:44) ;;
    *) echo "Unsupported OS release: $ID $VERSION_ID" >&2; exit 2 ;;
esac
if ! $PACKAGE_ONLY && [[ ! -d /run/systemd/system ]]; then
    echo 'A running systemd host is required. For image builds only, use --package-only.' >&2
    exit 2
fi
for target in /opt/d3v-patchcycle /usr/local/bin/d3v-patchcycle /etc/d3v-patchcycle/config.toml; do
    if [[ -e $target || -L $target ]]; then
        echo "Existing installation/configuration at $target. Use the upgrade instructions in INSTALL.md." >&2
        exit 2
    fi
done
if [[ $ID == ubuntu && $VERSION_ID == 22.04 ]]; then
    PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3.11}
    if [[ ! -x $PYTHON_BIN ]] || ! "$PYTHON_BIN" -I -c 'import sys, venv; assert sys.version_info >= (3,11)'; then
        echo 'Ubuntu 22.04 needs an administrator-approved Python 3.11+ with venv/ensurepip.' >&2
        echo 'Provision it first, then rerun with --python /absolute/path/to/python. No third-party repository was added.' >&2
        exit 2
    fi
fi
if [[ -n $PYTHON_BIN && $PYTHON_BIN != /* ]]; then
    echo '--python must be an absolute path' >&2; exit 2
fi
case "$ID" in
    ubuntu|debian)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y python3 python3-venv ca-certificates
        PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
        ;;
    rocky|almalinux)
        dnf install -y python3.11 python3.11-pip ca-certificates
        PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3.11}
        ;;
    fedora)
        dnf install -y python3 python3-pip ca-certificates
        PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
        ;;
esac
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec env -i PATH="$PATH" LANG=C.UTF-8 "$PYTHON_BIN" -I "$SCRIPT_DIR/install.py" "$PACKAGE_ONLY"
