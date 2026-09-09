#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <ota-package.zip> [--device-serial <serial>] [--hdc <path>]" >&2
    echo "  --device-serial  if given, cross-check the package version_list against the device's" >&2
    echo "                   const.product.software.version (updater CheckVersion does an exact match)" >&2
    echo "  --hdc            path to hdc (default: hdc on PATH)" >&2
    exit 2
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

warn() {
    echo "WARN: $*" >&2
}

[ "$#" -ge 1 ] || usage
command -v unzip >/dev/null 2>&1 || die "unzip is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

device_serial=""
hdc_bin="hdc"
package="$1"; shift || true
while [ "$#" -gt 0 ]; do
    case "$1" in
        --device-serial) device_serial="$2"; shift 2 ;;
        --hdc) hdc_bin="$2"; shift 2 ;;
        *) die "unknown option: $1" ;;
    esac
done
[ -f "$package" ] || die "package does not exist: $package"
[ -s "$package" ] || die "package is empty: $package"

package_dir="$(cd "$(dirname "$package")" && pwd)"
package_path="$package_dir/$(basename "$package")"
package_size="$(stat -c '%s' "$package_path")"
required_mib=$(( (package_size + 1048575) / 1048576 + 128 ))

echo "[PACKAGE] $package_path"
echo "[SIZE]    $package_size bytes"
echo "[SHA256]  $(sha256sum "$package_path" | awk '{print $1}')"
echo "[DEVICE]  Reserve at least $required_mib MiB free under /data before staging"

echo "[CHECK]   Validating ZIP integrity..."
unzip -tq "$package_path"

member_list="$(unzip -Z1 "$package_path")"
member_count="$(printf '%s\n' "$member_list" | sed '/^$/d' | wc -l | tr -d ' ')"
echo "[ZIP]     $member_count members"

found_manifest=0
version_list_lines=""
for member in version_list updater_config/VERSION.mbn updater_config/updater_specified_config.xml; do
    if printf '%s\n' "$member_list" | grep -Fxq "$member"; then
        found_manifest=1
        echo "[MEMBER]  $member"
        if [ "$member" = "version_list" ] || [ "$member" = "updater_config/VERSION.mbn" ]; then
            printf '[VERSION] '
            unzip -p "$package_path" "$member" | tr -d '\r' | sed -n '1p'
            if [ "$member" = "version_list" ]; then
                version_list_lines="$(unzip -p "$package_path" "$member" | tr -d '\r' | sed '/^[[:space:]]*$/d')"
            fi
        fi
    fi
done

[ "$found_manifest" -eq 1 ] || warn "No standard updater manifest was found; confirm this is the final OTA package, not a nested updater or recovery component."

case "$(basename "$package_path")" in
    updater_full.zip|updater_diff.zip)
        warn "Use the final top-level update.zip when available; this name can also occur in nested or intermediate artifacts."
        ;;
esac

if [ -n "${device_serial:-}" ]; then
    command -v "$hdc_bin" >/dev/null 2>&1 || die "hdc not found: $hdc_bin"
    device_ver="$("$hdc_bin" -t "$device_serial" shell 'param get const.product.software.version' 2>/dev/null | tr -d '\r ')"
    [ -n "$device_ver" ] || die "could not read const.product.software.version from $device_serial"
    echo "[DEVICE]  ${device_serial} software.version = ${device_ver}"
    match=0
    while IFS= read -r line; do
        if [ -z "$line" ]; then continue; fi
        if [ "$line" = "$device_ver" ]; then match=1; break; fi
    done <<< "${version_list_lines}"
    if [ "$match" -eq 0 ]; then
        die "updater version mismatch: device software.version=${device_ver} not in package version_list. The OTA updater (updater_preprocess.cpp CheckVersion) compares const.product.software.version exactly against each version_list line. Fix by adding the product version (e.g. 1.3.0) to updater_config/VERSION.mbn, then regenerate update.zip."
    fi
    echo "[CHECK]   version_list matches device software.version"
fi

echo "[PASS]    Local package preflight passed"
