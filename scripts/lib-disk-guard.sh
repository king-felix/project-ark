#!/usr/bin/env bash
# =============================================================================
#  Project ARK — macOS SD-card safety guard
#  Sourced by every script that can touch a block device.
#
#  Enforces AGENTS.md §2: an external, physical, whole, non-internal, removable
#  disk whose size falls in a *supported card class* — and never disk0/disk1 —
#  plus a second look at that same device immediately before anything is written.
#
#  WHY CAPACITY BANDS AND NOT A CEILING
#  ------------------------------------
#  A single "100–270 GB" window answered exactly one question ("is this a
#  128 or 256 GB card?") and said nothing about the next card somebody buys.
#  It also treated capacity as the *test* rather than one of several
#  independent facts.  This guard instead names the classes it supports and
#  requires the device to land inside one of them:
#
#      128 GB class   100–150 GB
#      256 GB class   200–300 GB
#      512 GB class   440–580 GB
#
#  (decimal GB, which is what `diskutil info TotalSize` reports).  The bands
#  are deliberately separated: a 200 GB disk is not "a 256 GB card that came up
#  short", and a 640 GB drive is not "a 512 GB card that grew".  Anything
#  outside every band is refused with the classes printed, so the fix is to add
#  a class here and in tests/test_disk_guard.py — never to widen a ceiling.
#
#  WHY EVERY CHECK FAILS CLOSED
#  ----------------------------
#  The original guard tested `[[ "$internal" == "yes" ]]`, so a reader that
#  returned nothing at all looked exactly like a safe external card.  Missing,
#  contradictory and malformed metadata are now all refusals: a device whose
#  identity cannot be read is not a device to write to.
#
#  WHY THE IDENTITY IS RE-CHECKED
#  ------------------------------
#  A device *name* is not a device.  /dev/disk4 is "whatever is fourth today".
#  `ark_card_identity` records the device, its exact size and any UUID the
#  reader reports; `ark_revalidate_sd_card` runs the whole guard again and
#  compares, immediately before the write, in the process that does the write.
# =============================================================================
set -Eeuo pipefail

# ---------------------------------------------------------------------------
#  Supported card classes: "label|min bytes|max bytes" (decimal GB).
#  Add a class here AND in tests/test_disk_guard.py — never widen a ceiling.
# ---------------------------------------------------------------------------
ARK_CARD_BANDS=(
  "128|100000000000|150000000000"
  "256|200000000000|300000000000"
  "512|440000000000|580000000000"
)

#: Never, whatever the metadata says.  disk0/disk1 are the internal Apple SSD
#: and/or its synthesized APFS containers on every laptop this project runs on.
ARK_FORBIDDEN_DISKS=(disk0 disk1)

#: What `ark_device_facts` prints for a key the plist did not contain.  A
#: sentinel, never an empty string: "absent" and "reported empty" are different
#: facts and only one of them is safe to guess about.
ARK_MISSING='__ARK_MISSING__'

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
#    Kept for callers that need one field (fix-pi-shell.sh asks for MountPoint)
#    and contract-compatible: a missing key prints nothing.
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

# ---------------------------------------------------------------------------
# 4. Every field the guard needs, in ONE diskutil call, with a sentinel for a
#    key the plist did not contain.  Exit 3 means "the metadata could not be
#    read at all" — the caller must refuse, not shrug.
# ---------------------------------------------------------------------------
ark_device_facts() { # $1=device -> key=value lines on stdout
  local dev=$1
  diskutil info -plist "/dev/$dev" 2>/dev/null | python3 -c '
import plistlib, sys

KEYS = ("Internal", "VirtualOrPhysical", "Ejectable", "RemovableMedia",
        "Removable", "TotalSize", "WholeDisk", "BusProtocol", "MediaName",
        "DiskUUID", "MediaUUID", "VolumeUUID", "MountPoint")
MISSING = "__ARK_MISSING__"

try:
    d = plistlib.loads(sys.stdin.buffer.read())
except Exception:
    sys.exit(3)                      # unreadable / not a plist => fail closed
if not isinstance(d, dict):
    sys.exit(3)

for k in KEYS:
    if k not in d:
        print("%s=%s" % (k, MISSING))
        continue
    v = d[k]
    if isinstance(v, bool):
        print("%s=%s" % (k, "yes" if v else "no"))
    elif isinstance(v, int):
        print("%s=%d" % (k, v))
    else:
        print("%s=%s" % (k, str(v)))
' || return 3
}

# Pull one key out of the facts block.  Values may contain "=" — only the first
# one separates the key from the value.
ark_fact() { # $1=facts text  $2=key
  printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1
}

ark_human() { # bytes -> human
  python3 -c "import sys;b=int(sys.argv[1]);print(f'{b/1e9:.1f} GB ({b/2**30:.1f} GiB)')" "${1:-0}"
}

# ---------------------------------------------------------------------------
# 5. The capacity policy as a pure question: which class is this many bytes in?
#    Prints the label ("128"/"256"/"512") or nothing, and exits 1 outside every
#    band.  No device, no diskutil — this is the part tests can drive directly.
# ---------------------------------------------------------------------------
ark_capacity_class() { # $1=bytes
  local bytes=${1:-} band label min max
  [[ "$bytes" =~ ^[0-9]+$ ]] || return 1
  for band in "${ARK_CARD_BANDS[@]}"; do
    IFS='|' read -r label min max <<<"$band"
    if (( 10#$bytes >= 10#$min && 10#$bytes <= 10#$max )); then
      printf '%s' "$label"
      return 0
    fi
  done
  return 1
}

#: The bands, as one human sentence, for a refusal message that tells the
#: reader what would have been accepted.
ark_supported_classes() {
  local band label min max out=""
  for band in "${ARK_CARD_BANDS[@]}"; do
    IFS='|' read -r label min max <<<"$band"
    out="${out}${out:+, }${label} GB ($((min / 1000000000))–$((max / 1000000000)) GB)"
  done
  printf '%s' "$out"
}

# ---------------------------------------------------------------------------
# 6. THE PER-DEVICE CHECK.  Given one identifier, prove it is a safe card or
#    die with the reason.  Writes nothing to stdout (callers capture stdout for
#    the device name); records what it found in the ARK_SD_* globals.
#
#    Every line below is a separate, independent fact about the device.  No one
#    of them — least of all capacity — is allowed to stand in for the others.
# ---------------------------------------------------------------------------
ARK_SD_SIZE=""
ARK_SD_CLASS=""
ARK_SD_FACTS=""

ark_validate_sd_device() { # $1=device
  local dev=$1 forbidden facts key int virt eject rem whole size class

  [[ -n "$dev" && "$dev" =~ ^disk[0-9]+$ ]] \
    || ark_die "refusing to inspect '$dev': not a whole-disk identifier (disk<N>)."

  for forbidden in "${ARK_FORBIDDEN_DISKS[@]}"; do
    [[ "$dev" == "$forbidden" ]] \
      && ark_die "/dev/$dev is in the forbidden list (${ARK_FORBIDDEN_DISKS[*]}). NEVER flash $forbidden."
  done

  facts=$(ark_device_facts "$dev") \
    || ark_die "could not read diskutil metadata for /dev/$dev.
   A disk whose identity cannot be read is not a target. Check the reader
   (reseat the card, plug it straight into the Mac) and re-run."

  for key in Internal VirtualOrPhysical Ejectable RemovableMedia TotalSize WholeDisk; do
    [[ "$(ark_fact "$facts" "$key")" == "$ARK_MISSING" ]] \
      && ark_die "/dev/$dev reports no $key value.
   Required metadata is missing, so this cannot be proved to be the card.
   Refusing (capacity alone is not evidence)."
  done

  int=$(ark_fact "$facts" Internal)
  virt=$(ark_fact "$facts" VirtualOrPhysical)
  eject=$(ark_fact "$facts" Ejectable)
  rem=$(ark_fact "$facts" RemovableMedia)
  whole=$(ark_fact "$facts" WholeDisk)
  size=$(ark_fact "$facts" TotalSize)

  # -- internal? ------------------------------------------------------------
  [[ "$int" == "yes" ]] && ark_die "/dev/$dev reports Internal=yes. That is the Mac's own disk. Refusing."
  [[ "$int" != "no" ]]  && ark_die "/dev/$dev reports Internal='$int' — neither yes nor no.
   Contradictory metadata. Refusing."

  # -- whole disk? ----------------------------------------------------------
  [[ "$whole" == "no" ]] && ark_die "/dev/$dev is a partition (WholeDisk=no), not a whole card. Refusing."
  [[ "$whole" != "yes" ]] && ark_die "/dev/$dev reports WholeDisk='$whole' — neither yes nor no.
   Contradictory metadata. Refusing."

  # -- synthesized? ---------------------------------------------------------
  # macOS says 'Virtual' for an APFS container and 'Unknown' for some physical
  # Apple disks, so 'Physical' cannot be required (that would refuse a real
  # card on a reader that answers 'Unknown').  'Virtual' is the deny signal,
  # and any other answer is a fact the guard cannot interpret — refused.
  [[ "$virt" == "Virtual" ]] && ark_die "/dev/$dev is a synthesized/virtual device. Refusing."
  if [[ "$virt" != "Physical" && "$virt" != "Unknown" ]]; then
    ark_die "/dev/$dev reports VirtualOrPhysical='$virt' — not Physical, Unknown or Virtual.
   Metadata the guard cannot interpret. Refusing."
  fi

  # -- removable MEDIA? -----------------------------------------------------
  # This is the one fact that separates an SD card from a USB hard disk, and it
  # has to be RemovableMedia specifically.  A USB SSD or portable drive reports
  # Internal=no, WholeDisk=yes, Ejectable=yes (eject is how Finder removes it)
  # and RemovableMedia=no — so "Ejectable OR RemovableMedia" described every
  # external disk on the desk, not a card.  With 500 GB portable drives now
  # inside the 512 GB class, that difference is the difference between erasing
  # a card and erasing somebody's backup.
  #
  # A reader that does not say RemovableMedia=yes is refused rather than
  # guessed at: say so plainly, and use a reader that reports it.
  [[ "$rem" != "yes" ]] && ark_die "/dev/$dev does not report RemovableMedia=yes (it says '$rem').
   Only media a reader describes as REMOVABLE is treated as an SD card, because
   an external USB drive is Ejectable too and can be the same size. If this
   really is the card, use a reader that reports removable media."
  [[ "$eject" != "yes" ]] && ark_die "/dev/$dev reports removable media but Ejectable='$eject'.
   Contradictory metadata. Refusing."

  # -- size, well formed? ---------------------------------------------------
  [[ "$size" =~ ^[0-9]+$ ]] \
    || ark_die "/dev/$dev reports TotalSize='$size', which is not a number of bytes.
   Malformed metadata. Refusing."
  (( 10#$size > 0 )) || ark_die "/dev/$dev reports a size of 0 bytes. Refusing."

  # -- size, in a class we support? ----------------------------------------
  class=$(ark_capacity_class "$size") || true
  [[ -n "$class" ]] || ark_die "/dev/$dev is $(ark_human "$size").
   That is not a supported card class. Supported: $(ark_supported_classes).
   To support another card, add its band to ARK_CARD_BANDS in
   scripts/lib-disk-guard.sh and a test for it — do not widen a ceiling."

  ARK_SD_SIZE="$size"
  ARK_SD_CLASS="$class"
  ARK_SD_FACTS="$facts"
}

# ---------------------------------------------------------------------------
# 7. THE GUARD.  Resolve exactly one safe device or abort.
#    Usage:  SD_DEV=$(ark_assert_sd_card)   -> "disk2"   (stdout carries only it)
# ---------------------------------------------------------------------------
ark_assert_sd_card() {
  local candidates n dev

  candidates=$(ark_external_candidates || true)

  if [[ -z "$candidates" ]]; then
    ark_show_disk_list >&2
    ark_die "No external physical disk found. The SD card is NOT mounted/plugged in.
   Insert the microSD card (via USB-C reader) and re-run. Refusing to guess a device."
  fi

  n=$(printf '%s\n' "$candidates" | wc -l | tr -d ' ')
  if [[ "$n" != "1" ]]; then
    ark_show_disk_list >&2
    ark_die "Found $n external physical disks — ambiguous:
$(printf '%s\n' "$candidates" | sed 's/^/     \/dev\//')
   Unplug every other external drive, leaving only the SD card, then re-run.
   The guard will not choose between them, whatever their sizes."
  fi

  dev="$candidates"
  ark_validate_sd_device "$dev"

  # Final belt-and-braces: it must *still* be listed as external physical.
  ark_external_candidates | grep -qx "$dev" \
    || ark_die "/dev/$dev vanished from the external physical list during the check."

  {
    echo "------------------------------------------------------------"
    echo " TARGET SD CARD — VERIFIED SAFE"
    echo "   device      : /dev/$dev  (raw: /dev/r$dev)"
    echo "   size        : $(ark_human "$ARK_SD_SIZE")"
    echo "   class       : ${ARK_SD_CLASS} GB card"
    echo "   Internal    : $(ark_fact "$ARK_SD_FACTS" Internal)"
    echo "   Ejectable   : $(ark_fact "$ARK_SD_FACTS" Ejectable)"
    echo "   Removable   : $(ark_fact "$ARK_SD_FACTS" RemovableMedia)"
    echo "   Type        : $(ark_fact "$ARK_SD_FACTS" VirtualOrPhysical)"
    echo "   Whole disk  : $(ark_fact "$ARK_SD_FACTS" WholeDisk)"
    echo "------------------------------------------------------------"
  } >&2

  printf '%s' "$dev"
}

# ---------------------------------------------------------------------------
# 8. IDENTITY.  A device name is not a device: /dev/disk4 is "whatever is
#    fourth today".  Record the name, the exact byte size and any UUID the
#    reader reports, as one whitespace-free token a caller can keep.
# ---------------------------------------------------------------------------
ark_card_identity() { # $1=device -> "dev=disk4 size=512107000000 uuid=..." (stdout)
  local dev=$1 facts size uuid k v
  facts=$(ark_device_facts "$dev") || return 3
  size=$(ark_fact "$facts" TotalSize)

  # Most whole disks publish NO UUID at all — measured on this Mac: a whole
  # SSD has none of DiskUUID/MediaUUID/VolumeUUID, and a card reader is under
  # no obligation to do better.  So the token is recorded when a UUID exists and
  # says so honestly when it does not.  Be precise about what that buys: the
  # re-check catches a renumbered device, a changed size, and a changed UUID
  # when there is one.  It cannot separate two identical cards in the same
  # reader — nothing in the metadata can — which is why the cloning tool makes
  # a person type the destination and why the original card is never written.
  uuid="-"
  for k in DiskUUID MediaUUID VolumeUUID; do
    v=$(ark_fact "$facts" "$k")
    if [[ -n "$v" && "$v" != "$ARK_MISSING" ]]; then uuid="$v"; break; fi
  done

  printf 'dev=%s size=%s uuid=%s' "$dev" "$size" "$uuid"
}

# ---------------------------------------------------------------------------
# 9. REVALIDATE.  Run immediately before a write, in the process doing the
#    write, with the token captured when the card was chosen.  Every fact is
#    checked again — not just the name — and then the recorded token must still
#    match.  Prints the device on success.
# ---------------------------------------------------------------------------
ark_revalidate_sd_card() { # $1=identity token
  local expected=${1:-} dev now

  [[ -n "$expected" ]] \
    || ark_die "revalidation needs the identity captured when the card was chosen (got nothing)."

  dev=$(printf '%s' "$expected" | sed -n 's/^dev=\([^ ]*\).*/\1/p')
  [[ -n "$dev" ]] \
    || ark_die "the captured device identity is malformed: '$expected'"

  # A stale identifier is not a device.  It must be external *now*.
  ark_external_candidates | grep -qx "$dev" \
    || ark_die "/dev/$dev is no longer an external physical disk.
   The device changed since it was chosen. Nothing was written; unplug the
   reader and start again."

  ark_validate_sd_device "$dev"

  now=$(ark_card_identity "$dev") \
    || ark_die "could not re-read /dev/$dev immediately before the write."

  [[ "$now" == "$expected" ]] || ark_die "the device changed since it was chosen:
     when chosen : $expected
     right now   : $now
   Nothing was written. Unplug the reader and start again."

  printf '%s' "$dev"
}

# ---------------------------------------------------------------------------
# 10. TWO CARDS.  Cloning needs a source and a destination, so "exactly one
#     external disk" is the wrong rule.  Each card passes the full per-device
#     check; the larger one is the destination; and they must be in *different*
#     capacity classes, because two cards of the same size cannot be told apart
#     by the only facts the guard can see.  The human still names the
#     destination.  Prints "src=disk4 dst=disk5" on stdout.
# ---------------------------------------------------------------------------
ark_assert_sd_card_pair() {
  local candidates n a b size_a size_b class_a class_b src dst src_size dst_size

  candidates=$(ark_external_candidates || true)
  if [[ -z "$candidates" ]]; then
    ark_show_disk_list >&2
    ark_die "No external physical disk found. Neither card is mounted.
   Plug in the card that works (the source) and the blank one (the destination),
   then re-run."
  fi

  n=$(printf '%s\n' "$candidates" | wc -l | tr -d ' ')
  if [[ "$n" != "2" ]]; then
    ark_show_disk_list >&2
    ark_die "Found $n external physical disks; cloning needs exactly 2 (source + destination).
$(printf '%s\n' "$candidates" | sed 's/^/     \/dev\//')
   Leave only the two cards plugged in, then re-run."
  fi

  a=$(printf '%s\n' "$candidates" | sed -n 1p)
  b=$(printf '%s\n' "$candidates" | sed -n 2p)

  ark_validate_sd_device "$a"; size_a=$ARK_SD_SIZE; class_a=$ARK_SD_CLASS
  ark_validate_sd_device "$b"; size_b=$ARK_SD_SIZE; class_b=$ARK_SD_CLASS

  if (( size_a > size_b )); then
    dst=$a; src=$b; dst_size=$size_a; src_size=$size_b
  else
    dst=$b; src=$a; dst_size=$size_b; src_size=$size_a
  fi

  [[ "$class_a" != "$class_b" ]] || ark_die "both cards are in the ${class_a} GB class.
   With the same capacity there is nothing in the metadata that tells the source
   from the destination, and this guard will not guess. Use two different
   capacities, or copy with a tool that asks you to name the destination."

  {
    echo "------------------------------------------------------------"
    echo " TWO CARDS — the larger is the DESTINATION"
    echo "   source      : /dev/$src   ($(ark_human "$src_size"))"
    echo "   destination : /dev/$dst   ($(ark_human "$dst_size"))"
    echo "   You will be asked to type the destination name before anything is"
    echo "   written. Capacity decides which is which; you confirm it."
    echo "------------------------------------------------------------"
  } >&2

  printf 'src=%s dst=%s' "$src" "$dst"
}

# ---------------------------------------------------------------------------
# 11. Guard for shell callers that just want a pass/fail
#
#     ark_die's `exit` only ends the command-substitution subshell, so a
#     function that captured the device and carried on would return 0 with an
#     empty string on a refusal.  Callers that use this as a gate would read
#     "refused" as "passed".  The device and the exit code are therefore both
#     checked here, explicitly.
# ---------------------------------------------------------------------------
ark_guard_or_exit() {
  local dev
  dev=$(ark_assert_sd_card) || {
    echo "ark_guard_or_exit: the guard refused — see the message above" >&2
    exit 2
  }
  if [[ -z "$dev" ]]; then
    echo "ark_guard_or_exit: the guard produced no device — refusing" >&2
    exit 2
  fi
  echo "$dev"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  ark_show_disk_list
  echo
  # The same trap as above: `dev=$(...) && ark_ok` leaves the script's status
  # to whatever runs next.  An explicit exit is the only honest answer for a
  # command whose whole purpose is to say whether a card is safe to write.
  if ! dev=$(ark_assert_sd_card); then
    echo >&2
    echo "Supported card classes: $(ark_supported_classes)" >&2
    exit 1
  fi
  ark_ok "Safe target resolved: /dev/$dev"
  echo
  echo "Supported card classes: $(ark_supported_classes)"
fi
