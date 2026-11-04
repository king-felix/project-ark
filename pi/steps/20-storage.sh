#!/usr/bin/env bash
# PHASE 2 — directory tree, environment files, seed Calibre library
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 2/7 — storage layout"

# ---- directory tree --------------------------------------------------------
#  `tmp/` is the one place a half-written upload is allowed to wait: the API's TMPDIR
#  and nginx's client_body_temp_path both point here (see ark-api.service and the three
#  vhosts), because /tmp is a 256 MB tmpfs since phase 15-resilience and a 2 GiB File Box
#  upload parked in RAM is an upload that fails.  Nothing serves this folder.
for d in \
  "$ARK_ROOT" \
  "$ARK_ROOT/logs" \
  "$ARK_ROOT/src" \
  "$ARK_ROOT/portal" \
  "$ARK_ROOT/api" \
  "$ARK_ROOT/api/data" \
  "$ARK_ROOT/bin" \
  "$ARK_ROOT/vault" \
  "$ARK_ROOT/vault/manuals" \
  "$ARK_ROOT/vault/guides" \
  "$ARK_ROOT/vault/apps" \
  "$ARK_ROOT/vault/downloads" \
  "$ARK_ROOT/kiwix" \
  "$ARK_ROOT/kiwix/zim" \
  "$ARK_ROOT/kiwix/state" \
  "$ARK_ROOT/calibre-library" \
  "$ARK_ROOT/calibre-config" \
  "$ARK_ROOT/calibre-incoming" \
  "$ARK_ROOT/drop" \
  "$ARK_ROOT/drop/files" \
  "$ARK_ROOT/drop/tmp" \
  "$ARK_ROOT/media" \
  "$ARK_ROOT/media/play" \
  "$ARK_ROOT/media/thumbs" \
  "$ARK_ROOT/media/subs" \
  "$ARK_ROOT/media/tmp" \
  "$ARK_ROOT/tmp" \
  "$ARK_ROOT/maps" \
  "$ARK_ROOT/maps/packs" \
  "$ARK_ROOT/maps/incoming" \
  "$ARK_ROOT/maps/tmp" \
  "$ARK_ROOT/maps/web" \
  "$ARK_ROOT/scripts" \
  "$ARK_ROOT/backups"
do
  ark_ensure_dir "$d" 775 "$ARK_PI_USER:$ARK_PI_GROUP"
done
ark_ok "directory tree ready under $ARK_ROOT"
ark_ensure_dir "$ARK_ETC" 755 root:root

# ---- /etc/ark/ark.env (single place a human may want to edit) --------------
if [[ ! -f "$ARK_ETC/ark.env" ]]; then
  ark_write "$ARK_ETC/ark.env" 644 root:root <<'ENV'
# ---------------------------------------------------------------------------
# PROJECT ARK — operator settings.  Re-run the affected phase after editing.
# ---------------------------------------------------------------------------
# Name of the open Wi-Fi network the Pi broadcasts when it has no home network.
HOTSPOT_SSID="The-Ark-Offline"
# 2.4 GHz channel; 1/6/11 are the only non-overlapping choices.
HOTSPOT_CHANNEL="6"
HOTSPOT_ADDRESS="10.42.0.1"
# NetworkManager profile that carries the access point (created by phase 7).
HOTSPOT_CONNECTION="ark-hotspot"
# Wi-Fi interface on the Pi 4.
WIFI_DEV="wlan0"
# NetworkManager connection profile of the home network (created on first boot).
HOME_WIFI_CONNECTION="home-wifi"
# How long ark-mode auto waits for the home network before broadcasting.
HOME_WIFI_TIMEOUT="75"
# ZIM download tiers to fetch in the background: 1 2 3 4  (space separated).
#   1 = life support (~1 GiB)   2 = survival core (~4 GiB)
#   3 = deep reference (~18 GiB) 4 = full encyclopaedia (~119 GiB)
ZIM_TIERS="1 2 3 4"
# 1 = be gentle with the household link: cap the whole download at
# ZIM_RATE_LIMIT (default 4M/s).  0 = full speed, nobody else is using it.
ZIM_POLITE="1"
ZIM_RATE_LIMIT="4M"
# The space the box keeps back is not set here: it is ARK_RESERVE_PERCENT, one
# policy for every path that grows the card (see pi/api/ark_space.py and
# /usr/local/sbin/ark-space).  The orderlies write it into this file at phase 6.2.
ENV
else
  ark_info "keeping existing $ARK_ETC/ark.env"
fi

# ---- the one storage policy a shell script can ask -------------------------
# Seven percent of the card (or 2 GiB) is kept back for the box itself.  Every
# Python path asks ark_space directly; the shell paths (a collection, a model, a
# backup) ask this command, so there is still only one policy on the box.
ark_copy "$ARK_SRC/bin/ark-space" /usr/local/sbin/ark-space 755 root:root
ark_ok "storage policy helper installed: /usr/local/sbin/ark-space"

# ---- the read-only card report --------------------------------------------
# After a card is replaced this is the one command that separates the physical
# card's size from the partition's, the filesystem's, the free space and the
# reserve above (docs/CARD-MIGRATION.md).  It writes nothing and needs no root.
ark_copy "$ARK_SRC/bin/ark-card-report" /usr/local/sbin/ark-card-report 755 root:root
ark_ok "card report installed: /usr/local/sbin/ark-card-report (read-only)"

# ---- current network mode --------------------------------------------------
if [[ ! -f "$ARK_ETC/mode" ]]; then
  printf 'home\n' > "$ARK_ETC/mode"
fi
chmod 644 "$ARK_ETC/mode"
ark_info "network mode: $(cat "$ARK_ETC/mode")"

# ---- Kiwix library index ---------------------------------------------------
if [[ ! -f "$ARK_ROOT/kiwix/library.xml" ]]; then
  cat > "$ARK_ROOT/kiwix/library.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<library version="20110515">
</library>
XML
  chown "$ARK_PI_USER:$ARK_PI_GROUP" "$ARK_ROOT/kiwix/library.xml"
  ark_ok "created empty kiwix library index"
fi

# ---- seed Calibre library --------------------------------------------------
# The Mac builds a genuine Calibre database (scripts/build-calibre-seed.sh) and
# rsyncs it to $ARK_SRC/calibre-library.  Never overwrite books already added.
if [[ ! -f "$ARK_ROOT/calibre-library/metadata.db" ]]; then
  if [[ -f "$ARK_SRC/calibre-library/metadata.db" ]]; then
    ark_info "installing seed book library"
    rsync -a --no-owner --no-group "$ARK_SRC/calibre-library/" "$ARK_ROOT/calibre-library/"
    chown -R "$ARK_PI_USER:$ARK_PI_GROUP" "$ARK_ROOT/calibre-library"
    ark_ok "seed library installed ($(du -sh "$ARK_ROOT/calibre-library" | cut -f1))"
  else
    ark_warn "no seed library found at $ARK_SRC/calibre-library — the Book section stays empty"
    ark_warn "run scripts/build-calibre-seed.sh on the Mac, re-rsync, then re-run --only 20-storage,30-calibre"
  fi
else
  ark_info "existing book library left untouched"
fi

# Collections land here so Calibre-Web can find them immediately.
ark_ensure_dir "$ARK_ROOT/calibre-library/.calnotes" 775 "$ARK_PI_USER:$ARK_PI_GROUP"

# ---- log rotation (logs must never fill the card) --------------------------
ark_write /etc/logrotate.d/ark 644 root:root <<'LOGROTATE'
/srv/ark/logs/*.log {
    weekly
    rotate 4
    size 20M
    missingok
    notifempty
    copytruncate
    compress
    delaycompress
}
LOGROTATE

# ---- record what this Pi is ------------------------------------------------
{
  printf 'installed=%s\n' "$(ark_ts)"
  printf 'model=%s\n' "$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
  printf 'os=%s\n' "$( . /etc/os-release; printf '%s' "$PRETTY_NAME" )"
  printf 'kernel=%s\n' "$(uname -r)"
  printf 'source=%s\n' "$ARK_SRC"
} > "$ARK_ROOT/.ark-release"
chown "$ARK_PI_USER:$ARK_PI_GROUP" "$ARK_ROOT/.ark-release"

df -h "$ARK_ROOT"
ark_ok "phase 2 complete"
