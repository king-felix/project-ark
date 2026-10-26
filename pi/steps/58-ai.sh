#!/usr/bin/env bash
# =============================================================================
#  PHASE 5b — Ask the Ark: the helper that reads this card and answers
#
#  Three things and no more: an engine, one model file, and a page of facts the
#  helper is allowed to read.  All three live on the card; the engine listens on
#  the loopback address only, so a question reaches it through nginx and the
#  portal and in no other way.
#
#  **A box with no model is a supported state.**  /ask still answers, in one
#  sentence, saying what is missing — and every other page is untouched.  This
#  phase therefore never fails for a missing engine or a missing model: it says
#  what is absent and what to do about it.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 5b/7 — Ask the Ark (a helper on the card, no internet)"

AI_DIR="$ARK_ROOT/ai"
MODELS="$AI_DIR/models"
ENGINE=/usr/local/bin/llama-server

# The pages run as pi and read the brief; the engine runs as pi and reads the model.
ark_ensure_dir "$AI_DIR" 755 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_ensure_dir "$MODELS" 755 "$ARK_PI_USER:$ARK_PI_GROUP"

for b in ark-ai-serve ark-ai-model-fetch ark-build-ai-brief; do
  ark_copy "$ARK_SRC/bin/$b" "/usr/local/sbin/$b" 755 root:root
done
ark_ok "the helper's three tools are installed"

# ---- the engine -------------------------------------------------------------
# It is a binary, not a package: nothing on the box can build it, and the box this
# runs on has no internet.  A carried copy in the source tree is installed; a copy
# already on the machine is left alone.
if [[ -x "$ARK_SRC/ai/llama-server" ]]; then
  ark_copy "$ARK_SRC/ai/llama-server" "$ENGINE" 755 root:root
  ark_ok "the engine was carried in and is installed ($ENGINE)"
elif [[ -x "$ENGINE" ]]; then
  ark_ok "the engine is already on the box ($ENGINE)"
else
  ark_warn "no engine at $ENGINE — /ask will say so in one sentence and every other"
  ark_warn "page works.  Carry one to \$ARK_SRC/ai/llama-server (arm64) and re-run this phase."
fi

# ---- the model --------------------------------------------------------------
model=""
if compgen -G "$MODELS/*.gguf" >/dev/null; then
  model="$(ls -t "$MODELS"/*.gguf | head -n 1)"
  ark_ok "a model is on the card: $(basename "$model"), $(ark_gib "$(stat -c %s "$model")") GiB"
else
  ark_info "no model yet — the box still works; /ask says it is waiting for its words"
  ark_info "carry one:  sudo ark-ai-model-fetch --from /media/usb/<model>.gguf"
  ark_info "fetch one:  sudo ark-ai-model-fetch          (needs internet, ~1 GB)"
fi

# ---- the brief: what is on this card, written for the helper ----------------
# Built as pi, from the source tree, so the file belongs to the account the pages
# run as.  A root-written file inside a pi folder is the fault the owner page once
# could not open a backup over.  ARK_SRC/api is used, not $ARK_ROOT/api, because
# this phase runs before 60-portal has installed the modules.
if runuser -u "$ARK_PI_USER" -- env \
     ARK_ROOT="$ARK_ROOT" ARK_API_DIR="$ARK_SRC/api" PYTHONPATH="$ARK_SRC/api" \
     /usr/local/sbin/ark-build-ai-brief --write; then
  ark_ok "the helper has been told what is on this card"
else
  ark_warn "the brief could not be written — the helper still answers, from the guides alone"
fi

# ---- the service ------------------------------------------------------------
ark_install_unit ark-ai.service
systemctl enable ark-ai.service >/dev/null 2>&1 || ark_die "cannot enable ark-ai.service"

answering=0
if [[ -x "$ENGINE" ]]; then
  # A helper that will not start is not a broken portal, so this is a warning.
  systemctl restart ark-ai.service || ark_warn "ark-ai did not restart (journalctl -u ark-ai -n 40)"
  if [[ -n "$model" ]]; then
    for _ in $(seq 1 45); do          # 90 s: the engine reads the model in first
      if [[ "$(ark_http_code http://127.0.0.1:8089/health)" == "200" ]]; then
        answering=1; break
      fi
      sleep 2
    done
  fi
  if (( answering )); then
    ark_ok "the helper is answering on 127.0.0.1:8089 ($(basename "$model"))"
  elif [[ -z "$model" ]]; then
    ark_info "the helper is waiting for its words — /ask says so in one sentence"
  else
    ark_warn "the helper has not started answering yet; it may still be reading the"
    ark_warn "model into memory.  Check: journalctl -u ark-ai -n 40 --no-pager"
  fi
else
  ark_info "ark-ai stays inactive until an engine is on the card; nothing else is affected"
fi

# ---- what the owner can do about it ----------------------------------------
cat <<EOF

  The helper, from a console (no sign-in needed on the page itself):
    curl -s http://127.0.0.1/ask | grep -c 'Questions other people ask'   # the page is there
    sudo ark-ai-model-fetch --status                                       # is there a model
    sudo ark-ai-model-fetch --from /media/usb/<model>.gguf                 # carry one in
    sudo ark-build-ai-brief --write                                        # re-describe this card
    journalctl -u ark-ai -n 40 --no-pager                                  # what it said
  The switch "Anyone nearby can ask the helper a question" is on the owner's
  Community page (https://<the box>:8443/admin/community).
EOF

ark_ok "phase 5b done — the helper never writes to the card, never keeps a question,"
ark_ok "and never reaches the internet: the model is a file beside it."
