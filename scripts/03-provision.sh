#!/usr/bin/env bash
# =============================================================================
#  PHASE 0.9 — Provision the Pi.
#
#  Copies this repo's pi/ tree to the Pi, runs the installer there in the
#  background (so a dropped Wi-Fi link cannot kill it), streams the log back,
#  and pulls the final receipt.
#
#  Usage: bash scripts/03-provision.sh [host] [--only 50-kiwix] [--skip 40]
#         ARK_HOST=192.168.1.42 bash scripts/03-provision.sh
# =============================================================================
set -Eeuo pipefail

ROOT="/Users/basestar/Survival-Pi"
KEY="$HOME/.ssh/id_ed25519"
LOG_DIR="$ROOT/logs"
TIMEOUT="${TIMEOUT:-14400}"          # 4 h: background collections keep going
STAGE="/home/pi/ark-src.stage"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o LogLevel=ERROR
          -o ServerAliveInterval=15 -o ServerAliveCountMax=6
          -i "$KEY" -p 22)
# scp does NOT accept -p <port> (that means "preserve times") — passing SSH_OPTS
# to scp made it treat "22" as a source file, so every pull failed silently and
# we never got the receipt. scp needs -P.
SCP_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o LogLevel=ERROR
          -i "$KEY" -P 22)

ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
info() { printf '\033[1;36m→\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---- argument parsing -------------------------------------------------------
HOST=""; PASS=()
while (( $# )); do
  case "$1" in
    --only|--skip|--from) [[ -n "${2:-}" ]] || die "$1 needs a value (e.g. $1 50-kiwix)"; PASS+=("$1" "$2"); shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1" ;;
    *)  HOST="$1"; shift ;;
  esac
done
# An empty array must produce an EMPTY string, not "''" — otherwise the remote
# `bash install-ark.sh ''` receives a stray empty argument and dies on it.
PASS_STR=""
if (( ${#PASS[@]} > 0 )); then PASS_STR="$(printf '%q ' "${PASS[@]}")"; fi

[[ -f "$KEY" ]] || die "missing SSH key $KEY"
mkdir -p "$LOG_DIR"

if [[ -z "$HOST" ]]; then
  if [[ -n "${ARK_HOST:-}" ]];          then HOST="$ARK_HOST"
  elif [[ -s "$LOG_DIR/ark_host.txt" ]]; then HOST="$(cat "$LOG_DIR/ark_host.txt")"
  else HOST="ark.local"; fi
fi
ok "target: pi@$HOST"

# ---- preflight ---------------------------------------------------------------
info "checking SSH, sudo and hardware"
ssh "${SSH_OPTS[@]}" "pi@$HOST" 'bash -s' <<'REMOTE' || die "cannot log in as pi@$HOST — run scripts/02-wait-for-pi.sh first"
set -Eeuo pipefail
printf '   model : %s\n' "$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
printf '   os    : %s\n' "$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-unknown}")"
printf '   kernel: %s\n' "$(uname -r)"
printf '   memory: %s MiB\n' "$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
printf '   root  : %s\n' "$(findmnt -no SOURCE,SIZE / 2>/dev/null || df -h / | tail -1)"
printf '   free  : %s\n' "$(df -h / | tail -1 | awk '{print $4}')"
printf '   uptime: %s\n' "$(uptime -p 2>/dev/null || uptime)"
sudo -n true 2>/dev/null || { echo "sudo needs a password — the image must ship passwordless sudo"; exit 1; }
REMOTE
ok "SSH and passwordless sudo work"

# ---- copy the source tree ----------------------------------------------------
info "copying pi/ to $STAGE (small — the collections are downloaded later)"
rsync -az --delete --exclude '.DS_Store' -e "ssh ${SSH_OPTS[*]}" \
      "$ROOT/pi/" "pi@$HOST:$STAGE/" || die "rsync failed"

ssh "${SSH_OPTS[@]}" "pi@$HOST" "sudo install -d -m 755 -o root -g root /srv/ark/src \
  && sudo rsync -a --delete --chown=root:root --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r $STAGE/ /srv/ark/src/ \
  && sudo find /srv/ark/src -name '*.sh' -exec chmod 755 {} + \
  && sudo find /srv/ark/src/bin -type f -exec chmod 755 {} + \
  && rm -rf $STAGE" || die "could not install the source tree into /srv/ark/src"
ok "source tree installed at /srv/ark/src"

# ---- run the installer, detached --------------------------------------------
# setsid + nohup: the install keeps running even if this Mac closes the lid.
# This block MUST come before the follow-loop below (a previous version had it
# after the exit paths, which made it unreachable and left the loop watching a
# log that was never created). Stale markers are cleared first, otherwise an old
# install.done makes the follower think a fresh run already finished.
ssh "${SSH_OPTS[@]}" "pi@$HOST" \
  "sudo bash -c 'install -d -m 755 -o pi -g pi /srv/ark/logs; \
     rm -f /srv/ark/logs/install.done /srv/ark/logs/install.log /srv/ark/logs/provision.log; \
     setsid nohup bash /srv/ark/src/install-ark.sh $PASS_STR > /srv/ark/logs/provision.log 2>&1 < /dev/null &'" \
  || die "could not start the installer on the Pi"
ok "installer started on the Pi in the background"

# ---- follow it ---------------------------------------------------------------
info "following /srv/ark/logs/provision.log — you can close this window, the Pi keeps going"
start=$(date +%s); seen=0; state="running"; last_note=0
while :; do
  now=$(date +%s); elapsed=$(( now - start ))
  (( elapsed > TIMEOUT )) && die "installer still running after ${TIMEOUT}s — check /srv/ark/logs/provision.log on the Pi"

  # NB: '[i]nstall-ark\.sh' — the bracket stops pgrep from matching the very
  # `bash -c` command line that carries the pattern. Without it the check
  # matched itself and reported "running" forever even though nothing ran.
  read -r state lines < <(ssh "${SSH_OPTS[@]}" "pi@$HOST" \
    'printf "%s %s\n" "$( [ -f /srv/ark/logs/install.done ] && echo done || ( pgrep -f "[i]nstall-ark\.sh" >/dev/null && echo running || echo gone ) )" \
       "$(wc -l < /srv/ark/logs/provision.log 2>/dev/null || echo 0)"' 2>/dev/null || echo "gone 0")
  lines="${lines:-0}"

  if (( lines > seen )); then
    ssh "${SSH_OPTS[@]}" "pi@$HOST" "sed -n '$((seen + 1)),${lines}p' /srv/ark/logs/provision.log" || true
    seen=$lines
  fi

  [[ "$state" == "done" || "$state" == "gone" ]] && break
  if (( elapsed - last_note >= 60 )); then
    last_note=$elapsed
    printf '\033[1;36m→\033[0m … %dm%02ds elapsed, still working\n' $((elapsed/60)) $((elapsed%60))
  fi
  sleep 10
done
echo

# ---- collect the receipt -----------------------------------------------------
scp -q "${SCP_OPTS[@]}" "pi@$HOST:/srv/ark/ark_status.json" "$LOG_DIR/ark_status.json" 2>/dev/null \
  || warn "no receipt yet (the final check may not have run)"
scp -q "${SCP_OPTS[@]}" "pi@$HOST:/srv/ark/logs/install.done" "$LOG_DIR/install.done" 2>/dev/null || true
scp -q "${SCP_OPTS[@]}" "pi@$HOST:/srv/ark/logs/install.log" "$LOG_DIR/pi-install.log" 2>/dev/null || true
scp -q "${SCP_OPTS[@]}" "pi@$HOST:/srv/ark/logs/provision.log" "$LOG_DIR/pi-provision.log" 2>/dev/null || true

# ---- verdict -----------------------------------------------------------------
if [[ "$state" == "gone" ]]; then
  warn "the installer disappeared before writing a completion marker (crash?)"
  echo "   last lines of the installer output:"
  sed -n "$(( seen > 30 ? seen - 30 : 1 )),\$p" "$LOG_DIR/pi-provision.log" 2>/dev/null | sed 's/^/     /' || true
  echo "   full log: $LOG_DIR/pi-provision.log"
  exit 1
fi

# install.done is written even when phases fail — surface which ones.
if ! jq -e '.ok == true' "$LOG_DIR/install.done" >/dev/null 2>&1; then
  warn "installer finished with failed phases: $(jq -r '.failed // "unknown"' "$LOG_DIR/install.done" 2>/dev/null || echo unknown)"
fi

if [[ -s "$LOG_DIR/ark_status.json" ]] && jq -e '.ok == true' "$LOG_DIR/ark_status.json" >/dev/null 2>&1; then
  ok "the Ark is up — receipt: $LOG_DIR/ark_status.json"
  jq -r '"   mode: \(.mode)  collections: \(.collections.files) (\(.collections.gib) GiB)  books: \(.books)  guides: \(.guides.pages)  free: \(.free_gib) GiB"' \
     "$LOG_DIR/ark_status.json" 2>/dev/null || true
  echo "   open http://ark.lan on any device joined to the same network"
  exit 0
fi

warn "installer finished but the receipt is missing or reports problems"
[[ -s "$LOG_DIR/ark_status.json" ]] && { jq -r '.problems // empty' "$LOG_DIR/ark_status.json" 2>/dev/null || true; }
echo "   full log: $LOG_DIR/pi-provision.log"
echo "   re-run a single phase with: bash scripts/03-provision.sh $HOST --only 60-portal"
exit 1
