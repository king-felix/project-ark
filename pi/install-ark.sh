#!/usr/bin/env bash
# =============================================================================
#  PROJECT ARK — Pi-side installer (runs as root on the Pi)
#
#  Usage:
#     sudo bash /srv/ark/src/install-ark.sh                # all phases
#     sudo bash /srv/ark/src/install-ark.sh --only 50-kiwix
#     sudo bash /srv/ark/src/install-ark.sh --from 60-portal
#     sudo bash /srv/ark/src/install-ark.sh --skip 40-vault
#
#  Idempotent: every phase can be re-run safely.  Progress is written to
#  /srv/ark/logs/install.log and the marker /srv/ark/logs/install.done.
# =============================================================================
set -Eeuo pipefail

ARK_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ARK_SRC
# shellcheck source=lib/common.sh
source "$ARK_SRC/lib/common.sh"
ark_require_root

ALL_STEPS=(10-base 20-storage 30-calibre 40-vault 50-kiwix 60-portal 65-admin 70-network 90-verify)
ONLY="" ; SKIP="" ; FROM=""
LOG="$ARK_LOG_DIR/install.log"
DONE_FILE="$ARK_LOG_DIR/install.done"

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

while (( $# )); do
  case "$1" in
    --only)  ONLY="${2:-}"; shift 2 ;;
    --skip)  SKIP="${2:-}"; shift 2 ;;
    --from)  FROM="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) ark_die "unknown argument: $1 (try --help)" ;;
  esac
done

ark_ensure_dir "$ARK_LOG_DIR" 755 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_ensure_dir "$ARK_ETC" 755 root:root
exec > >(tee -a "$LOG") 2>&1
rm -f "$DONE_FILE"

printf '\033[1;35m'
cat <<BANNER
╔══════════════════════════════════════════════════════════════════════╗
║   P R O J E C T   A R K   —   installer                              ║
║   $(ark_ts)                                              ║
╚══════════════════════════════════════════════════════════════════════╝
BANNER
printf '\033[0m'
printf '   source : %s\n   root   : %s\n   log    : %s\n' "$ARK_SRC" "$ARK_ROOT" "$LOG"

# ---- resolve which phases to run -------------------------------------------
SELECTED=()
if [[ -n "$ONLY" ]]; then
  IFS=',' read -r -a wanted <<< "$ONLY"
  for w in "${wanted[@]}"; do
    found=""
    for s in "${ALL_STEPS[@]}"; do
      [[ "$s" == "$w" || "${s%%-*}" == "$w" ]] && { SELECTED+=("$s"); found=1; break; }
    done
    [[ -n "$found" ]] || ark_die "unknown phase '$w' (valid: ${ALL_STEPS[*]})"
  done
else
  start_idx=0
  if [[ -n "$FROM" ]]; then
    for i in "${!ALL_STEPS[@]}"; do
      [[ "${ALL_STEPS[$i]}" == "$FROM" || "${ALL_STEPS[$i]%%-*}" == "$FROM" ]] && { start_idx=$i; break; }
    done
  fi
  SELECTED=("${ALL_STEPS[@]:$start_idx}")
fi

if [[ -n "$SKIP" ]]; then
  IFS=',' read -r -a skips <<< "$SKIP"
  filtered=()
  for s in "${SELECTED[@]}"; do
    keep=1
    for k in "${skips[@]}"; do
      if [[ "$s" == "$k" || "${s%%-*}" == "$k" ]]; then keep=0; fi
    done
    (( keep == 1 )) && filtered+=("$s")
  done
  SELECTED=("${filtered[@]}")
fi

[[ ${#SELECTED[@]} -gt 0 ]] || ark_die "no phases selected"
ark_info "phases: ${SELECTED[*]}"
START_TS=$(date +%s)
declare -a RUN_OK=() RUN_FAIL=()

for step in "${SELECTED[@]}"; do
  script="$ARK_SRC/steps/$step.sh"
  [[ -f "$script" ]] || ark_die "missing phase script: $script"
  ark_step "running $step"
  if bash "$script"; then
    RUN_OK+=("$step")
    date -Is > "$ARK_LOG_DIR/.ok-$step"
  else
    rc=$?
    RUN_FAIL+=("$step")
    ark_warn "phase $step FAILED (rc=$rc) — see $LOG"
  fi
done

ELAPSED=$(( $(date +%s) - START_TS ))

printf '\n\033[1;35m━━━ INSTALL SUMMARY ━━━\033[0m\n'
printf '   elapsed : %dm%02ds\n' $((ELAPSED/60)) $((ELAPSED%60))
printf '   ok      : %s\n' "${RUN_OK[*]:-none}"
printf '   failed  : %s\n' "${RUN_FAIL[*]:-none}"

if (( ${#RUN_FAIL[@]} == 0 )); then
  {
    printf '{"finished":"%s","ok":true,"elapsed_s":%d,"phases":"%s"}\n' \
      "$(ark_ts)" "$ELAPSED" "${RUN_OK[*]}"
  } > "$DONE_FILE"
  chown "$ARK_PI_USER:$ARK_PI_GROUP" "$DONE_FILE" 2>/dev/null || true
  printf '\n\033[1;32m✓ ARK_INSTALL_COMPLETE\033[0m  (every phase succeeded)\n'
  exit 0
fi

{
  printf '{"finished":"%s","ok":false,"elapsed_s":%d,"failed":"%s"}\n' \
    "$(ark_ts)" "$ELAPSED" "${RUN_FAIL[*]}"
} > "$DONE_FILE"
printf '\n\033[1;31m✗ ARK_INSTALL_INCOMPLETE\033[0m  re-run: sudo bash %s --only %s\n' \
  "$ARK_SRC/install-ark.sh" "$(IFS=,; echo "${RUN_FAIL[*]}")"
exit 1
