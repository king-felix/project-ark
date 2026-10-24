#!/usr/bin/env bash
# =============================================================================
#  ARK — repair an already-flashed card: give `pi` a usable shell.
#  Patches the FAT32 boot partition in place (no re-flash), then the Pi fixes
#  itself on the next boot and reboots.
#  Usage: bash scripts/fix-pi-shell.sh
# =============================================================================
set -Eeuo pipefail
source "/Users/basestar/Survival-Pi/scripts/lib-disk-guard.sh"

ROOT="/Users/basestar/Survival-Pi"
PIFIX="$ROOT/pi/bootfix/pifix.sh"

[[ -f "$PIFIX" ]] || ark_die "missing $PIFIX"
bash -n "$PIFIX"  || ark_die "$PIFIX fails bash -n"

echo
ark_show_disk_list >&2
SD_DEV=$(ark_assert_sd_card)
PLAIN="/dev/$SD_DEV"

# ---- locate the FAT32 boot partition ---------------------------------------
BOOT_PART=$(diskutil list "$PLAIN" 2>/dev/null \
  | awk '/FAT|MS-DOS|Microsoft Basic Data/ {print $NF}' | head -1)
[[ -n "$BOOT_PART" ]] || { diskutil list "$PLAIN" >&2; ark_die "no FAT32 boot partition found on $PLAIN"; }
ark_info "boot partition: /dev/$BOOT_PART"

MP=$(ark_plist_field "$BOOT_PART" MountPoint)
if [[ -z "$MP" ]]; then
  ark_info "mounting /dev/$BOOT_PART"
  diskutil mount "/dev/$BOOT_PART" >/dev/null 2>&1 || true
  MP=$(ark_plist_field "$BOOT_PART" MountPoint)
fi
[[ -n "$MP" && -d "$MP" ]] || ark_die "could not mount /dev/$BOOT_PART (unplug/replug the reader and retry)"
ark_info "mounted at: $MP"

# ---- Where does this image mount the FAT partition? ------------------------
# Bookworm/Trixie: the FAT partition IS /boot/firmware, so its root carries
# cmdline.txt, config.txt, *.dtb, bootcode.bin. Legacy (Buster) mounted the
# same partition at /boot. The card's own cmdline backup shows the truth:
#   systemd.run=/boot/firmware/firstrun.sh
if [[ -f "$MP/cmdline.txt" ]]; then
  BOOTDIR="$MP";           PIFIX_REMOTE="/boot/firmware/pifix.sh"
elif [[ -f "$MP/firmware/cmdline.txt" ]]; then
  BOOTDIR="$MP/firmware";  PIFIX_REMOTE="/boot/pifix.sh"
else
  ls -la "$MP" >&2
  ark_die "cannot find cmdline.txt on /dev/$BOOT_PART"
fi
ark_info "cmdline: $BOOTDIR/cmdline.txt → $PIFIX_REMOTE"

# ---- prove the first boot already happened (self-cleaning sanity check) ----
if [[ -f "$BOOTDIR/firstrun.sh" ]]; then
  ark_warn "firstrun.sh is still on the card — it may not have run. Continuing anyway."
else
  ark_ok "firstrun.sh already cleaned itself up (it ran)"
fi

# ---- drop the repair script ------------------------------------------------
cp -f "$PIFIX" "$BOOTDIR/pifix.sh"
chmod 755 "$BOOTDIR/pifix.sh" 2>/dev/null || true
# FAT32 cannot store CRLF-free-ness: force LF explicitly
if grep -qU $'\r' "$BOOTDIR/pifix.sh"; then
  tr -d '\r' < "$BOOTDIR/pifix.sh" > "$BOOTDIR/.pifix.tmp" && mv -f "$BOOTDIR/.pifix.tmp" "$BOOTDIR/pifix.sh"
fi
[[ $(wc -c < "$BOOTDIR/pifix.sh") -gt 500 ]] || ark_die "pifix.sh looks truncated on the card"
ark_ok "pifix.sh written ($(wc -l < "$BOOTDIR/pifix.sh" | tr -d ' ') lines)"

# ---- arm it via the kernel command line (same mechanism rpi-imager uses) ----
python3 - "$BOOTDIR/cmdline.txt" "$PIFIX_REMOTE" <<'PY'
import re, sys, pathlib
path, remote = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
raw = p.read_text(encoding="utf-8", errors="replace")
line = raw.replace("\r", "").strip()
# strip any previous run tokens, then append ours
line = re.sub(r"\s*systemd\.run=\S+", "", line)
line = re.sub(r"\s*systemd\.run_success_action=\S+", "", line)
line = re.sub(r"\s*systemd\.unit=kernel-command-line\.target", "", line)
line = " ".join(line.split())
line += f" systemd.run={remote} systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
with open(path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(line + "\n")
print(f"cmdline.txt armed: {line}")
PY

# ---- verify what is actually on the card now -------------------------------
grep -q "systemd.run=$PIFIX_REMOTE" "$BOOTDIR/cmdline.txt" || ark_die "cmdline.txt does not contain the run token"
[[ $(wc -l < "$BOOTDIR/cmdline.txt") -eq 1 ]] || ark_die "cmdline.txt must be exactly one line (it is not)"
ark_ok "cmdline.txt has exactly 1 line and the run token"

sync
diskutil unmount "/dev/$BOOT_PART" >/dev/null 2>&1 || ark_warn "unmount reported a problem"
ark_ok "card patched and unmounted — safe to remove"
cat <<'EOM'

===============================================================
  NEXT (physical):
    1) pull the microSD out of the Mac
    2) put it back in the Pi and plug in power
    3) wait ~90 s: the Pi repairs the account, then reboots itself
===============================================================
EOM
