#!/usr/bin/env bash
# =============================================================================
#  Project ARK — macOS SD-card safety guard
#  Sourced by every script that can touch a block device.
#  Enforces .clinerules §2: external + physical + 100..270 GB, never disk0/disk1.
# =============================================================================
set -Eeuo pipefail

ARK_MIN_BYTES=$((100 * 1000 * 1000 * 1000))   # 100 GB decimal
ARK_MAX_BYTES=$((270 * 1000 * 1000 * 1000))   # 270 GB decimal
ARK_FORBIDDEN_DISKS=(disk0 disk1)

ark_die() { printf '\n\033[1;31m✗ ARK ABORT:\033[0m %s\n' "$*" >&2; exit 1; }
ark_ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
ark_info(){ printf '\033[1;36m→\033[0m %s\n' "$*"; }
ark_warn(){ printf '\033[1;33m!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Show what macOS sees. Always run this and show the raw output to the user.
# ---------------------------------------------------------------------------
ark_show_disk_list() {
  echo "============================================================"
  echo " diskutil list external physical"
  echo "============================================================"
  local out
  out=$(diskutil list external physical 2>&1 || true)
  if [[ -z "${out//[[:space:]]/}" ]]; then
    echo "(no external physical disks reported)"
  else
    printf '%s\n' "$out"
  fi
  echo "============================================================"
}

# ---------------------------------------------------------------------------
# 2. Enumerate candidate external physical whole disks (diskutil identifiers)
# ---------------------------------------------------------------------------
ark_external_candidates() {
  diskutil list external physical 2>/dev/null \
    | grep -E '^/dev/disk[0-9]+ \(external, ?physical\)' \
    | sed -E 's|^/dev/(disk[0-9]+).*|\1|' \
    | sort -u
}

# ---------------------------------------------------------------------------
# 3. Read a single field out of `diskutil info -plist` without needing jq.
# ---------------------------------------------------------------------------
ark_plist_field() { # $1=device  $2=key
  local dev=$1 key=$2
  diskutil info -plist "/dev/$dev" 2>/dev/null | python3 -c "
import plistlib, sys
key = sys.argv[1]
try:
    # READ FULLY FIRST: plistlib.load() on a pipe raises on Python 3.9
    # ('File or stream is not seekable'), which silently blanked every field
    # and made this guard refuse a perfectly valid card.
    d = plistlib.loads(sys.stdin.buffer.read())
except Exception:
    print('')
    sys.exit(0)
v = d.get(key, '')
if isinstance(v, bool):
    print('yes' if v else 'no')
elif isinstance(v, int):
    print(v)
else:
    print(v)
" "$key"
}

ark_human() { # bytes -> human
  python3 -c "import sys;b=int(sys.argv[1]);print(f'{b/1e9:.1f} GB ({b/2**30:.1f} GiB)')" "${1:-0}"
}

# ---------------------------------------------------------------------------
# 4. THE GUARD. Resolve exactly one safe device or abort.
#    Usage:  SD_DEV=$(ark_assert_sd_card)   -> "disk2"
# ---------------------------------------------------------------------------
ark_assert_sd_card() {
  local candidates
  candidates=$(ark_external_candidates || true)

  if [[ -z "$candidates" ]]; then
    ark_show_disk_list >&2
    ark_die "No external physical disk found. The SD card is NOT mounted/plugged in.
   Insert the microSD card (via USB-C reader) and re-run. Refusing to guess a device."
  fi

  local n; n=$(printf '%s\n' "$candidates" | wc -l | tr -d ' ')
  if [[ "$n" != "1" ]]; then
    ark_show_disk_list >&2
    ark_die "Found $n external physical disks ($(echo $candidates)) — ambiguous.
   Unplug every other external drive, leaving only the SD card, then re-run."
  fi

  local dev="$candidates"

  # Hard denylist: internal SSD / synthesized APFS containers.
  local forbidden
  for forbidden in "${ARK_FORBIDDEN_DISKS[@]}"; do
    [[ "$dev" == "$forbidden" ]] && ark_die "/dev/$dev is in the forbidden list. NEVER flash $forbidden."
  done

  # Cross-check the plist metadata.
  local internal synth ejectable removable size
  internal=$(ark_plist_field  "$dev" Internal)
  synth=$(ark_plist_field     "$dev" VirtualOrPhysical)
  ejectable=$(ark_plist_field "$dev" Ejectable)
  removable=$(ark_plist_field "$dev" RemovableMedia)
  size=$(ark_plist_field      "$dev" TotalSize)

  [[ "$internal" == "yes" ]] && ark_die "/dev/$dev reports Internal=yes. Refusing."
  [[ "$synth" == "Virtual" ]] && ark_die "/dev/$dev is a synthesized/virtual device. Refusing."
  if [[ "$ejectable" != "yes" && "$removable" != "yes" ]]; then
    ark_die "/dev/$dev is neither Ejectable nor RemovableMedia. Refusing."
  fi
  [[ -z "$size" || "$size" -lt "$ARK_MIN_BYTES" ]] && ark_die "/dev/$dev size ${size:-?} bytes is below 100 GB. Not the target card."
  [[ "$size" -gt "$ARK_MAX_BYTES" ]] && ark_die "/dev/$dev size ${size:-?} bytes exceeds 270 GB. Not the target card."

  # Final belt-and-braces: it must still be listed as external physical right now.
  ark_external_candidates | grep -qx "$dev" || ark_die "/dev/$dev vanished from external physical list."

  {
    echo "------------------------------------------------------------"
    echo " TARGET SD CARD — VERIFIED SAFE"
    echo "   device      : /dev/$dev  (raw: /dev/r$dev)"
    echo "   size        : $(ark_human "$size")"
    echo "   Internal    : $internal"
    echo "   Ejectable   : $ejectable"
    echo "   Removable   : $removable"
    echo "   Type        : ${synth:-Physical}"
    echo "------------------------------------------------------------"
  } >&2

  printf '%s' "$dev"
}

# ---------------------------------------------------------------------------
# 5. Guard for shell callers that just want a pass/fail
# ---------------------------------------------------------------------------
ark_guard_or_exit() {
  local dev
  dev=$(ark_assert_sd_card)
  echo "$dev"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  ark_show_disk_list
  echo
  dev=$(ark_assert_sd_card) && ark_ok "Safe target resolved: /dev/$dev"
fi
