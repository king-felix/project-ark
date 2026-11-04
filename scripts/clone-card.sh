#!/usr/bin/env bash
# =============================================================================
#  Project ARK — clone a working card onto a larger one, keeping the original.
#
#  This is the migration path that keeps everything: the box's own certificate
#  (so devices that trusted it keep trusting it), the owner's account and
#  sessions, the saved Wi-Fi profiles, the SSH host keys, every collection,
#  book, game, film and map.  It copies the card byte for byte.
#
#  It is deliberately awkward:
#
#    * it needs exactly TWO external physical cards, both passing the full
#      guard, in DIFFERENT capacity classes (that is the only way the guard can
#      tell source from destination);
#    * it refuses to run without a person at the keyboard;
#    * it makes you type the destination device name;
#    * it revalidates BOTH cards inside the privileged helper, immediately
#      before the first byte is written;
#    * it never touches the source card.
#
#  It is NEVER run by an agent, and never with input piped in.
#
#  Usage:  bash scripts/clone-card.sh --dry-run     # say what it would do
#          bash scripts/clone-card.sh               # do it, after confirming
#
#  After the copy: boot the Pi on the NEW card, then follow
#  docs/CARD-MIGRATION.md §5 — the filesystem still thinks it is the old size.
# =============================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$ROOT/scripts/lib-disk-guard.sh"
source "$GUARD"

HELPER="$ROOT/scripts/_clone-helper.sh"
LOG="$ROOT/logs/clone.log"
DONE="$ROOT/logs/.clone.done"
DRY=0

usage() {
  cat <<'EOM'
Clone a working ARK card onto a larger one.

  bash scripts/clone-card.sh --dry-run    say what would happen; write nothing
  bash scripts/clone-card.sh              copy, after you confirm the target

Plug in BOTH cards (the working one and the new, blank, larger one) through
USB readers, with no other external drives attached.  The larger card is the
destination; you will be asked to type its name to confirm.

The card being copied from is never written to.  Keep it as it is until the
new card has booted and answered.
EOM
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --dry-run) DRY=1 ;;
  "") ;;
  *) usage >&2; ark_die "unknown argument: $1" ;;
esac

mkdir -p "$ROOT/logs"

# ---- only a person runs this -----------------------------------------------
# Checked before anything else, so an unattended invocation cannot get as far as
# mounting, unmounting or resolving a device at all.
if (( ! DRY )); then
  [[ -t 0 ]] || ark_die "This script writes over a real card and needs a person at the keyboard.
   It refuses to run with its input piped or from a background job. Run it
   yourself, in a terminal:  bash $ROOT/scripts/clone-card.sh"
fi

echo
ark_show_disk_list >&2
echo

PAIR=$(ark_assert_sd_card_pair) || exit 1
SRC=$(printf '%s' "$PAIR" | sed -n 's/.*src=\([^ ]*\).*/\1/p')
DST=$(printf '%s' "$PAIR" | sed -n 's/.*dst=\([^ ]*\).*/\1/p')
[[ -n "$SRC" && -n "$DST" ]] || ark_die "could not read the two devices out of '$PAIR'"
[[ "$SRC" != "$DST" ]] || ark_die "source and destination resolved to the same device. Refusing."

SRC_ID=$(ark_card_identity "$SRC") || ark_die "could not record the source card's identity"
DST_ID=$(ark_card_identity "$DST") || ark_die "could not record the destination card's identity"

# The sizes were captured during the pair check; re-read them for the banner.
ark_validate_sd_device "$SRC"; SRC_BYTES=$ARK_SD_SIZE
ark_validate_sd_device "$DST"; DST_BYTES=$ARK_SD_SIZE

cat >&2 <<EOM

  source      : /dev/$SRC  ($(ark_human "$SRC_BYTES"))   ← never written
  destination : /dev/$DST  ($(ark_human "$DST_BYTES"))   ← EVERYTHING ON THIS IS ERASED
  copy size   : $(ark_human "$SRC_BYTES") then $(ark_human "$((DST_BYTES - SRC_BYTES))") left unallocated

  The copy takes roughly 30–90 minutes and the Mac must stay awake.
EOM

if (( DRY )); then
  echo >&2
  ark_ok "dry run — nothing was unmounted and nothing was written" >&2
  echo "   would run: dd if=/dev/r$SRC of=/dev/r$DST bs=4m" >&2
  exit 0
fi

# ---- confirm the destination ------------------------------------------------
echo >&2
ark_warn "Type the DESTINATION device name (without /dev/) to erase and copy onto it."
printf '   destination is %s — type %s: ' "/dev/$DST" "$DST" >&2
read -r typed
[[ "$typed" == "$DST" ]] || ark_die "you typed '$typed', not '$DST'. Nothing was written."

# ---- unmount both (non-destructive) ----------------------------------------
ark_info "Unmounting both cards (nothing is written yet)…"
diskutil unmountDisk "/dev/$SRC" >/dev/null 2>&1 || ark_warn "unmount of /dev/$SRC reported a problem (continuing; the copy re-checks)"
diskutil unmountDisk "/dev/$DST" >/dev/null 2>&1 || ark_warn "unmount of /dev/$DST reported a problem (continuing; the copy re-checks)"

# ---- privileged helper ------------------------------------------------------
# Written to disk so the AppleScript string stays trivial (no quoting hell).
cat > "$HELPER" <<EOF
#!/bin/bash
# generated by clone-card.sh — safe to delete
exec >"$LOG" 2>&1
NOW() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "===== ARK clone start \$(NOW) ====="
echo "source      : /dev/$SRC  ($SRC_ID)"
echo "destination : /dev/$DST  ($DST_ID)"
rm -f "$DONE"

# Both cards, re-checked by the process that is about to write. A name resolved
# a minute ago in another shell proves nothing about what /dev/$DST is now.
if ! ( source "$GUARD"; ark_revalidate_sd_card "$SRC_ID" >/dev/null ); then
  echo "NOT STARTED: the source card changed or failed the guard"
  echo 5 > "$DONE"; exit 5
fi
if ! ( source "$GUARD"; ark_revalidate_sd_card "$DST_ID" >/dev/null ); then
  echo "NOT STARTED: the destination card changed or failed the guard"
  echo 5 > "$DONE"; exit 5
fi
echo "both cards revalidated; copying"

dd if="/dev/r$SRC" of="/dev/r$DST" bs=4m
rc=\$?
echo "===== dd finished rc=\$rc \$(NOW) ====="
if [[ "\$rc" != "0" ]]; then
  echo "\$rc" > "$DONE"
  exit "\$rc"
fi
sync

# A sample, not a proof of every byte: the first and last 8 MiB of the copied
# region. That catches a wrong destination and a truncated copy. The filesystem
# check on the Pi (90-verify) is what proves the rest.
#
# tail -c counts from the real end of the device. An earlier version computed
# a dd skip as (total - head) / 1 MiB, which truncated the remainder and left
# the final few hundred KB of every copy uncompared — exactly the bytes a
# silently short write loses.
EDGE=\$(( 8 * 1024 * 1024 ))
s_head=\$(dd if="/dev/r$SRC" bs=1m count=8 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
d_head=\$(dd if="/dev/r$DST" bs=1m count=8 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
s_tail=\$(tail -c "\$EDGE" "/dev/r$SRC" 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
d_tail=\$(tail -c "\$EDGE" "/dev/r$DST" 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
echo "head src: \$s_head"
echo "head dst: \$d_head"
echo "tail src: \$s_tail"
echo "tail dst: \$d_tail"
if [[ "\$s_head" == "\$d_head" && "\$s_tail" == "\$d_tail" && -n "\$s_head" ]]; then
  echo "SAMPLE OK — the first and last 8 MiB match"
  echo 0 > "$DONE"
else
  echo "SAMPLE MISMATCH — the copy does not match at its start or end"
  echo 6 > "$DONE"
fi
sync
EOM
chmod 700 "$HELPER"

AUTHLOG="$ROOT/logs/clone-auth.log"
rm -f "$DONE" "$LOG"
: > "$AUTHLOG"
ark_info "macOS will now ask for Touch ID / your password to write to /dev/r$DST."
osascript -e "do shell script \"/bin/bash '$HELPER'\"" >"$AUTHLOG" 2>&1 &
OSA_PID=$!

start=$(date +%s)
trap 'echo; ark_warn "Interrupted — the destination card may be partially written. Do not boot it; copy again."' INT TERM
while :; do
  [[ -f "$DONE" ]] && break
  if ! kill -0 "$OSA_PID" 2>/dev/null; then
    sleep 1
    [[ -f "$DONE" ]] && break
    break
  fi
  printf '\r   copying… %02d:%02d elapsed' $(( ($(date +%s) - start) / 60 )) $(( ($(date +%s) - start) % 60 ))
  sleep 15
done
echo

OSA_RC=0
wait "$OSA_PID" 2>/dev/null || OSA_RC=$?

if [[ ! -f "$DONE" ]]; then
  if grep -qi 'cancel' "$AUTHLOG" 2>/dev/null; then
    ark_die "Authorization was cancelled — nothing was written to /dev/$DST."
  fi
  tail -20 "$AUTHLOG" >&2 || true
  tail -20 "$LOG" 2>/dev/null >&2 || true
  ark_die "the privileged copy did not report completion (osascript rc=$OSA_RC). Nothing is verified."
fi

RC=$(tr -dc '0-9' < "$DONE" || true)
case "${RC:-9}" in
  0) ;;
  5) ark_die "NOTHING WAS WRITTEN: a card changed between choosing it and writing to it.
   The privileged helper revalidated both cards and refused. See $LOG." ;;
  6) ark_die "The copy does not match the original at its start or end.
   Do NOT boot the new card and do NOT erase the original. See $LOG." ;;
  *) tail -8 "$LOG" | tr '\r' '\n' | grep -v '^[[:space:]]*$' >&2
     ark_die "the copy failed (rc=$RC) — see $LOG. The original card was not written to." ;;
esac

# ---- done -------------------------------------------------------------------
ark_info "Ejecting both cards"
diskutil eject "/dev/$DST" >/dev/null 2>&1 || ark_warn "eject of /dev/$DST reported a problem; you can unplug it."
diskutil eject "/dev/$SRC" >/dev/null 2>&1 || ark_warn "eject of /dev/$SRC reported a problem; you can unplug it."
ark_ok "Copy made. The original card is unchanged — keep it exactly as it is."

cat <<EOM

===============================================================
  NEXT
    1) put the ORIGINAL card somewhere safe. Do not erase it and do not
       boot it again until the new one has proved itself.
    2) insert the NEW card into the Pi and power on.
    3) the filesystem is still the OLD size. Expand it deliberately:
         docs/CARD-MIGRATION.md §5
    4) prove it, read-only:
         ark-card-report
===============================================================
EOM
