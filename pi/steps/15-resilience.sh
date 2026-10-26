#!/usr/bin/env bash
# =============================================================================
#  PHASE 1b — what the box does about itself when the power comes back
#
#  A Pi in a house with an unreliable supply gets its plug pulled.  The things
#  that actually break, in the order they cost the most:
#
#   * the clock is wrong (no battery-backed clock), so every "3 hours ago" on
#     every page is wrong and a copy looks like it came from the future;
#   * a small file is half written, and the page that reads it either crashes or
#     silently shows nothing;
#   * a database will not open, and the box runs on with an empty shelf;
#   * half-built uploads and bundles pile up until the card is full.
#
#  This phase installs the one tool that repairs all four (ark-self-heal, run at
#  boot before the portal opens), plus the operating-system settings that make the
#  next cut cheaper: a narrower write-behind window, a tmpfs for the throwaway
#  folders, a hardware watchdog that reboots a hung box, and Restart=always on
#  every long-running unit.
#
#  It changes nothing a visitor can see, and it takes no copy: a copy is still
#  the owner's deliberate act (phase 8).
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 1b — surviving a power cut"

# ---- the tool and the two modules it imports --------------------------------
for mod in ark_atomic.py ark_self_heal.py; do
  ark_copy "$ARK_SRC/api/$mod" "$ARK_ROOT/api/$mod" 644 root:root
done
ark_copy "$ARK_SRC/bin/ark-self-heal" /usr/local/sbin/ark-self-heal 755 root:root
ark_ok "installed ark-self-heal and its modules"

# ---- the clock has to survive a cut -----------------------------------------
# A Pi 4 has no battery-backed clock.  fake-hwclock writes the time the box last
# knew to the card and puts it back at boot, which is what keeps a reboot from
# starting in 1970 (and a timestamp from sorting before the shelf it describes).
if ark_have fake-hwclock; then
  ark_info "fake-hwclock is already installed"
else
  ark_apt_install fake-hwclock
fi
systemctl enable fake-hwclock.service >/dev/null 2>&1 \
  || ark_warn "could not enable fake-hwclock.service"
ark_ok "fake-hwclock remembers the time across a cold boot"

# ---- a narrower write-behind window ----------------------------------------
# The kernel normally holds dirty pages for up to 30 s.  Fifteen is the point
# where a cut costs a few seconds of writes instead of half a minute, and it costs
# almost nothing on a card.  swappiness is lowered for the same reason: a Pi that
# swaps to a card wears it out and stalls while it does.
ark_write /etc/sysctl.d/99-ark-powercut.conf 644 root:root <<'EOF'
# Project ARK — settings that make a power cut cost less (phase 15-resilience).
vm.dirty_writeback_centisecs = 1500
vm.dirty_expire_centisecs = 1500
vm.dirty_background_ratio = 5
vm.swappiness = 10
# A kernel panic must not leave the box dark: reboot, and let the filesystem
# check and ark-self-heal put it back together.
kernel.panic = 10
kernel.panic_on_oops = 0
EOF
sysctl -q -p /etc/sysctl.d/99-ark-powercut.conf >/dev/null 2>&1 \
  || ark_warn "could not apply every kernel setting now (they apply at the next boot)"
ark_ok "write-behind narrowed to 15 s, swappiness lowered, a panic reboots"

# ---- the throwaway folders live in memory ----------------------------------
# /tmp and /var/tmp hold nothing worth keeping across a cut, and a card cut while
# writing them is exactly what leaves a half-written file behind.  A tmpfs costs
# nothing to lose.  The sizes are generous enough for apt and pip, and finite so a
# runaway cannot eat the RAM the box needs to serve pages.
if [[ -L /tmp || "$(findmnt -no FSTYPE /tmp 2>/dev/null || true)" == "tmpfs" ]]; then
  ark_info "the throwaway folders already live in memory"
elif grep -q '^# ARK: throwaway folders' /etc/fstab; then
  ark_info "the throwaway folders are already in /etc/fstab"
else
  cp -a /etc/fstab /etc/fstab.ark-before-tmpfs
  {
    printf '\n# ARK: throwaway folders live in memory, so a cut leaves nothing half-written\n'
    printf 'tmpfs\t/tmp\ttmpfs\tnodev,nosuid,size=256M,mode=1777\t0\t0\n'
    if [[ -d /var/tmp && ! -L /var/tmp ]]; then
      printf 'tmpfs\t/var/tmp\ttmpfs\tnodev,nosuid,size=256M,mode=1777\t0\t0\n'
    fi
  } >> /etc/fstab
  if findmnt --verify >/dev/null 2>&1; then
    rm -f /etc/fstab.ark-before-tmpfs
    ark_ok "the throwaway folders will live in memory (from the next boot)"
  else
    cp -a /etc/fstab /etc/fstab.ark-review
    cp -a /etc/fstab.ark-before-tmpfs /etc/fstab
    ark_warn "the new /etc/fstab lines did not verify, so they were taken back out (see /etc/fstab.ark-review)"
  fi
fi

# ---- the journal is not worth corrupting a card for ------------------------
# A persistent journal on a card the power keeps cutting is the one thing that can
# make journald refuse to start at boot, and its own record is the least valuable
# thing on the box: the two logs that matter (self-heal.log and net-watch.log) are
# plain files on the card, written and fsynced by the tools themselves.
ark_ensure_dir /etc/systemd/journald.conf.d 755 root:root
ark_write /etc/systemd/journald.conf.d/99-ark-volatile.conf 644 root:root <<'EOF'
# Project ARK — the journal is kept in memory only (phase 15-resilience).
# A cut cannot then leave a corrupt journal behind, and the two logs this box
# depends on live on the card: /srv/ark/logs/.
[Journal]
Storage=volatile
RuntimeMaxUse=64M
SystemMaxUse=64M
EOF
systemctl try-restart systemd-journald >/dev/null 2>&1 \
  || ark_warn "the journal will pick up its new settings at the next boot"
ark_ok "the journal is in memory; the repair and network logs stay on the card"

# ---- a watchdog that reboots a box which has stopped answering --------------
# The Pi's own watchdog, driven by systemd: if the kernel or systemd stops petting
# it for 15 s, the hardware resets the board.  That turns "the box is hung and the
# whole house is offline" into "the box rebooted by itself".
ark_ensure_dir /etc/systemd/system.conf.d 755 root:root
ark_write /etc/systemd/system.conf.d/99-ark-watchdog.conf 644 root:root <<'EOF'
# Project ARK — the hardware watchdog (phase 15-resilience).
[Manager]
RuntimeWatchdogSec=15s
RebootWatchdogSec=10min
ShutdownWatchdogSec=15min
EOF
systemctl daemon-reexec >/dev/null 2>&1 || ark_warn "the watchdog settings apply at the next boot"
if [[ -e /dev/watchdog ]]; then
  ark_ok "the hardware watchdog is armed (15 s)"
else
  ark_warn "this board has no /dev/watchdog — it will not reboot itself if it hangs"
fi

# ---- the filesystem check the cut demands ----------------------------------
# A card that was mid-write when the power went can come back with a filesystem
# that is "clean" only because nobody looked.  Forcing the check on the next boot
# (once) is what turns a damaged card into a repaired one instead of a box that
# boots into a shell nobody can reach over the network.
CMDLINE=""
for c in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  [[ -f "$c" ]] && { CMDLINE="$c"; break; }
done
if [[ -z "$CMDLINE" ]]; then
  ark_warn "no kernel command line file found — the filesystem check is left to the default"
else
  for opt in fsck.mode=force fsck.repair=yes panic=10; do
    if ! grep -qw -- "$opt" "$CMDLINE"; then
      cp -a "$CMDLINE" "$CMDLINE.ark-before-resilience"
      sed -i "s/[[:space:]]*$/ $opt/" "$CMDLINE"
      ark_info "added $opt to $CMDLINE"
    fi
  done
  if [[ "$(wc -l < "$CMDLINE")" -gt 1 ]]; then
    ark_die "$CMDLINE now has more than one line — the box would not boot.  Compare with $CMDLINE.ark-before-resilience"
  fi
  ark_ok "the next boot checks the card (one line, $(wc -w < "$CMDLINE") words)"
fi

# ---- every long-running service comes back by itself ------------------------
# `Restart=always` with no start limit: a service that dies because the power
# blipped is restarted three seconds later, however many times it takes.  The
# start limit is what usually stops this from working — systemd gives up after
# five restarts in ten seconds and leaves the box up with a dead portal.
LONG_RUNNING_UNITS=(ark-api ark-kiwix ark-calibre ark-zim-downloader ark-ai)
for u in "${LONG_RUNNING_UNITS[@]}"; do
  # Only units this box actually has: phase 58 may not have been run yet.
  [[ -f "/etc/systemd/system/$u.service" ]] || continue
  ark_ensure_dir "/etc/systemd/system/$u.service.d" 755 root:root
  ark_write "/etc/systemd/system/$u.service.d/10-ark-restart.conf" 644 root:root <<'EOF'
# Project ARK — come back by yourself (phase 15-resilience).
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=3
EOF
done
systemctl daemon-reload
ark_ok "the long-running services restart themselves, without a start limit"

# ---- the two databases outside the app get the same treatment ---------------
# Calibre's library and Calibre-Web's own database are written by a third party
# that defaults to a synchronous mode a cut can lose.  WAL + FULL is the pair that
# costs a few milliseconds per write and keeps committed rows.
if ark_have sqlite3; then
  for db in "$ARK_ROOT/calibre-library/metadata.db" "$ARK_ROOT/calibre-config/app.db"; do
    [[ -s "$db" ]] || { ark_info "no $(basename "$(dirname "$db")") database yet — nothing to harden"; continue; }
    if sudo -u "$ARK_PI_USER" sqlite3 "$db" \
         "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA quick_check;" 2>/dev/null | grep -qx ok; then
      ark_ok "$(basename "$db") is in WAL with a full sync, and reads back whole"
    else
      ark_warn "$(basename "$db") did not answer its own check — ark-self-heal will look again ($db)"
    fi
  done
else
  ark_warn "sqlite3 is missing, so the book databases keep their default sync mode"
fi

# ---- the unit, the timer, and one real pass --------------------------------
ark_install_unit ark-self-heal.service
ark_install_unit ark-self-heal.timer
# Both: the timer for the daily look, and the service so the boot pass actually
# happens.  A oneshot that is only *installed* never runs — `WantedBy` is a wish
# until `enable` makes the symlink.
systemctl enable ark-self-heal.service >/dev/null 2>&1 \
  || ark_die "cannot enable ark-self-heal.service (the boot pass would never run)"
systemctl enable ark-self-heal.timer >/dev/null 2>&1 || ark_die "cannot enable ark-self-heal.timer"
systemctl start ark-self-heal.timer >/dev/null 2>&1 || ark_warn "the daily pass could not be started (check: systemctl status ark-self-heal.timer)"
ark_ok "ark-self-heal runs before the portal, and once a day at 04:17"

# One real pass, now, so this phase is proved rather than installed.  It is safe
# on a healthy box: nothing to repair means every file is left exactly as it is.
if systemctl start ark-self-heal.service; then
  ark_ok "a real pass over this card finished (report: /srv/ark/logs/self-heal.json)"
else
  ark_warn "the pass has left something to look at — read /srv/ark/logs/self-heal.log"
fi

if /usr/local/sbin/ark-self-heal --dry-run --quiet; then
  ark_ok "a dry pass over this card found nothing it would have to repair"
else
  ark_warn "a dry pass would repair something — read /srv/ark/logs/self-heal.log"
fi

ark_ok "PHASE 1b done — a cut now costs a reboot, not a rebuild"
