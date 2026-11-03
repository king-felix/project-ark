#!/usr/bin/env bash
# PHASE 6.2 — the Media shelf: one video reader, one worker, one folder tree
#
# A film is the only thing on this box that needs a tool it did not write.  This
# phase installs FFmpeg from the distribution (never a downloaded binary), puts
# the shelf's folders on the card, and starts the single background worker that
# prepares films one at a time.
#
# **A missing FFmpeg is a supported state, not a broken box.**  The phase warns
# and carries on: the portal, the shelf and every film already playable keep
# working, and the owner's page says in one sentence that this box cannot look
# at a new film yet.  Nothing here can stop the essential services.
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 6.2 — media shelf"

MEDIA="$ARK_ROOT/media"

# ---- folders ---------------------------------------------------------------
# `tmp/` is where a half-written conversion waits.  Nothing serves it, and the
# worker empties it on start-up, so a power cut leaves a stale file at worst.
for d in "$MEDIA" "$MEDIA/play" "$MEDIA/thumbs" "$MEDIA/subs" "$MEDIA/tmp"; do
  ark_ensure_dir "$d" 775 "$ARK_PI_USER:$ARK_PI_GROUP"
done
ark_ok "media folders ready under $MEDIA"

# ---- the tool --------------------------------------------------------------
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ark_info "ffmpeg already installed: $(ffmpeg -version 2>/dev/null | head -1)"
else
  ark_info "installing ffmpeg (the box's own copy of the video reader)"
  export DEBIAN_FRONTEND=noninteractive
  if ! apt-get install -y --no-install-recommends ffmpeg >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1 || true
    if ! apt-get install -y --no-install-recommends ffmpeg >/dev/null 2>&1; then
      ark_warn "ffmpeg could not be installed — films can still be watched if they"
      ark_warn "already play, but the box cannot look at or prepare a new one"
    fi
  fi
  if command -v ffmpeg >/dev/null 2>&1; then
    ark_ok "ffmpeg installed: $(ffmpeg -version 2>/dev/null | head -1)"
  fi
fi

# Prove the two tools answer, and that the build can write the one format the
# shelf promises.  A binary that starts but has no H.264 encoder would fail much
# later, on a film the owner had waited an hour for.
PROBE_OK=0
ENCODE_OK=0
if command -v ffprobe >/dev/null 2>&1; then PROBE_OK=1; fi
# Capture the list first: `ffmpeg … | grep -q` under `set -o pipefail` reports a
# false negative — grep exits on the first match, ffmpeg takes SIGPIPE, and the
# pipeline answers 141 for a build that has the encoder.
ENCODERS=""
if command -v ffmpeg >/dev/null 2>&1; then
  ENCODERS="$(ffmpeg -hide_banner -encoders 2>/dev/null || true)"
fi
if [[ "$ENCODERS" == *libx264* ]]; then ENCODE_OK=1; fi
if (( PROBE_OK && ENCODE_OK )); then
  ark_ok "the video tools work, and this build can make a browser-playable copy"
else
  ark_warn "video tools incomplete: ffprobe=$PROBE_OK h264=$ENCODE_OK"
fi

# ---- the reserve, said out loud in the one file a human edits --------------
if ! grep -q '^ARK_RESERVE_PERCENT=' "$ARK_ETC/ark.env" 2>/dev/null; then
  {
    printf '\n'
    printf '# How much of the card the box keeps back, as a percentage of its capacity.\n'
    printf '# The reserve is max(percent of the card, 2 GiB); below it the box refuses new\n'
    printf '# files and films rather than filling the card the portal lives on.  7 is the\n'
    printf '# shipped value.  This is the appliance limit, not a quota for one person.\n'
    printf 'ARK_RESERVE_PERCENT="7"\n'
  } >> "$ARK_ETC/ark.env"
  chmod 644 "$ARK_ETC/ark.env"
  ark_ok "reserve recorded in $ARK_ETC/ark.env (7%)"
fi

# ---- the worker ------------------------------------------------------------
ark_copy "$ARK_SRC/bin/ark-media-worker" /usr/local/sbin/ark-media-worker 755 root:root
ark_install_unit ark-media-worker.service
# Enabled on purpose and started now: a queued film waits for nobody.  It is
# `Restart=always`, so a crash is a pause, not a lost shelf.
ark_service_up ark-media-worker.service 20

# ---- prove it --------------------------------------------------------------
if sudo -u "$ARK_PI_USER" /usr/local/sbin/ark-media-worker --status 2>&1 | head -12; then
  ark_ok "the media worker answered"
else
  ark_warn "the media worker did not answer — check: journalctl -u ark-media-worker"
fi

ark_ok "phase 6.2 complete"
