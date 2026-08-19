#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <ota-package.zip>" >&2
    exit 2
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

warn() {
    echo "WARN: $*" >&2
}

[ "$#" -eq 1 ] || usage
command -v unzip >/dev/null 2>&1 || die "unzip is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

package="$1"
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
for member in version_list updater_config/VERSION.mbn updater_config/updater_specified_config.xml; do
    if printf '%s\n' "$member_list" | grep -Fxq "$member"; then
        found_manifest=1
        echo "[MEMBER]  $member"
        if [ "$member" = "version_list" ] || [ "$member" = "updater_config/VERSION.mbn" ]; then
            printf '[VERSION] '
            unzip -p "$package_path" "$member" | tr -d '\r' | sed -n '1p'
        fi
    fi
done

[ "$found_manifest" -eq 1 ] || warn "No standard updater manifest was found; confirm this is the final OTA package, not a nested updater or recovery component."

case "$(basename "$package_path")" in
    updater_full.zip|updater_diff.zip)
        warn "Use the final top-level update.zip when available; this name can also occur in nested or intermediate artifacts."
        ;;
esac

echo "[PASS]    Local package preflight passed"
