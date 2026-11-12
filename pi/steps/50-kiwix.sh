#!/usr/bin/env bash
# =============================================================================
#  PHASE 5 — offline library (encyclopedias, manuals, Q&A archives)
#
#  Installs the resolver, the background downloader and kiwix-serve.  The full
#  catalogue is tens of gigabytes, so nothing here waits for it: the queue is
#  pinned, the downloader starts in the background, and the library begins
#  serving the moment the first bundle lands.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 5/7 — offline library"

ZIM_DIR="$ARK_ROOT/kiwix"
STATE="$ZIM_DIR/state"
PROFILE_FILE="$ARK_ETC/zim-profile"
QUEUE="$ARK_ETC/zim-queue.tsv"
CATALOG=/usr/local/share/ark/catalog.tsv
RESOLVE=/usr/local/bin/ark-zim-resolve

ark_ensure_dir "$ZIM_DIR" 755 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_ensure_dir "$STATE" 755 "$ARK_PI_USER:$ARK_PI_GROUP"

for b in ark-zim-resolve ark-zim-download ark-zim-checksum ark-kiwix-library; do
  ark_copy "$ARK_SRC/bin/$b" "/usr/local/bin/$b" 755 root:root
done
ark_ensure_dir /usr/local/share/ark 755 root:root
ark_copy "$ARK_SRC/kiwix/catalog.tsv" "$CATALOG" 644 root:root

# ---- how much of the catalogue to install -----------------------------------
# core  = the survival spine, ~98 GiB.  It fitted a 128 GB card with room to
#         spare until 3 Nov 2026; the prepper film shelves and the home/vehicle
#         Q&A archives added here push it past that, so a 128 GB card now wants
#         `mini` (or the owner's own trim of the catalogue).  A 256 GB card holds
#         it with ~90 GiB to spare.
# mini  = essentials only, ~9 GiB (slow links, small cards)
# full  = core + photo Wikipedia + the electronics/technology extras, ~235 GiB.
#         On a 256 GB card it can only live there alone; a 512 GB card holds it
#         *and* the books, films and games, with roughly 200 GiB to spare after
#         the 7 % reserve (docs/CARD-MIGRATION.md).
if [[ ! -s "$PROFILE_FILE" ]]; then
  printf '%s\n' "${ARK_ZIM_PROFILE:-core}" | ark_write "$PROFILE_FILE" 644 root:root
fi
PROFILE="$(tr -d '[:space:]' < "$PROFILE_FILE")"
ark_info "library profile: $PROFILE (change: echo mini > $PROFILE_FILE)"

# ---- the owner's exceptions to the profile -------------------------------
# A profile is a guess about a card size.  It cannot know that this box has room
# for the photo edition of Wikipedia, or that its owner would rather not spend
# 52 GiB on the photo-less one beside it.  That is what this file is for: two
# verbs, applied by the resolver every time it rebuilds the queue, so a reinstall
# never quietly overwrites the owner's choice with the profile's guess.
PREFS_FILE="$ARK_ETC/zim-prefs"
if [[ ! -e "$PREFS_FILE" ]]; then
  ark_write "$PREFS_FILE" 644 root:root <<'PREFS'
# Project ARK — collections this box carries, or leaves out, on top of its profile.
#
#   + <name>  <flavour>    carry it even if the profile would not choose it
#   - <name>  <flavour>    leave it out even if the profile would
#
# <name> and <flavour> must name a row in /usr/local/share/ark/catalog.tsv; the
# catalogue stays the only definition of a collection's size, shelf and label.
# Restart nothing: the downloader picks the queue up on its next cycle.
#
# The example below is the one this box needed on 12 Nov 2026 — the photo edition
# of Wikipedia in place of the photo-less one, once the card could hold it:
#
#   + wikipedia_en_all  maxi
#   - wikipedia_en_all  nopic
PREFS
fi
ark_info "owner's collection preferences: $PREFS_FILE"

# ---- pin the queue ----------------------------------------------------------
ark_info "asking the catalogue for the newest edition of every bundle"
if "$RESOLVE" --catalog "$CATALOG" --queue "$QUEUE" \
              --zim-dir "$ZIM_DIR" --profile "$PROFILE" --prefs "$PREFS_FILE"; then
  ark_ok "download queue pinned: $QUEUE"
else
  # offline at install time is survivable — the downloader retries the lookup
  ark_warn "could not pin the queue now; the downloader will retry when online"
fi

# ---- services ---------------------------------------------------------------
ark_install_unit ark-kiwix.service
ark_install_unit ark-zim-downloader.service
ark_service_up ark-zim-downloader.service 20

# ---- wait (briefly) for the first bundle so the service can be proven -------
WAIT="${ARK_KIWIX_WAIT:-600}"
ark_info "waiting up to ${WAIT}s for the first bundle to arrive"
deadline=$(( $(date +%s) + WAIT ))
while (( $(date +%s) < deadline )); do
  compgen -G "$ZIM_DIR/*.zim" >/dev/null && break
  sleep 5
done

if compgen -G "$ZIM_DIR/*.zim" >/dev/null; then
  ark_service_up ark-kiwix.service 30
  # kiwix-serve needs a moment to index the library after a restart; a single
  # probe produced spurious "answered HTTP 000" warnings.
  code="$(ark_wait_http "http://127.0.0.1:8081/kiwix/" 45 200)" || true
  if [[ "$code" == "200" ]]; then
    ark_ok "library is answering on :8081/kiwix/ ($(ls -1 "$ZIM_DIR"/*.zim | wc -l) bundle(s))"
  else
    ark_warn "kiwix answered HTTP $code — check journalctl -u ark-kiwix"
  fi
else
  ark_warn "no bundle yet — the library page appears on its own when the first download finishes"
fi

have="$(awk -F'\t' '$1=="have"{n++} END{print n+0}' "$QUEUE" 2>/dev/null || echo 0)"
todo="$(awk -F'\t' '$1=="todo"{n++} END{print n+0}' "$QUEUE" 2>/dev/null || echo 0)"
printf '   bundles on disk : %s\n   still queued    : %s\n   free space      : %s\n' \
  "$have" "$todo" "$(df -h "$ZIM_DIR" | tail -1 | awk '{print $4}')"
ark_info "progress file: $STATE/status.json   log: $ARK_LOG_DIR/zim-download.log"
ark_ok "phase 5 complete — the library keeps filling itself in the background"
