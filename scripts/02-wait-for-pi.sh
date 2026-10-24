#!/usr/bin/env bash
# =============================================================================
#  PHASE 0.8b — Wait for the Pi to finish its first boot and answer over SSH.
#  Usage: bash scripts/02-wait-for-pi.sh [timeout_seconds]
#  Exit 0 + prints "ARK_HOST=<resolved host>" on success.
# =============================================================================
set -Eeuo pipefail

TIMEOUT="${1:-1500}"          # 25 minutes: first boot + apt-less provisioning
KEY="$HOME/.ssh/id_ed25519"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o LogLevel=ERROR
          -i "$KEY" -p 22)

die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
info(){ printf '\033[1;36m→\033[0m %s\n' "$*"; }

[[ -f "$KEY" ]] || die "Missing SSH private key $KEY"

# Raspberry Pi OUI prefixes — used only to *discover* an IP, never to access blindly.
PI_OUIS=("b8:27:eb" "dc:a6:32" "e4:5f:01" "d8:3a:dd" "2c:cf:67" "28:cd:c1")

try_host() { # $1 = hostname or ip
  local h=$1
  ssh "${SSH_OPTS[@]}" "pi@${h}" "echo ARK_ONLINE" 2>/dev/null | grep -q ARK_ONLINE
}

discover_pi_ip() {
  # 1) mDNS lookup with a hard 2 s bound (bounded `ping` — NOT `dns-sd -G`,
  #    which never exits when there is no answer and would hang this loop).
  local ip=""
  ip=$(ping -c 1 -t 2 ark.local 2>/dev/null \
       | sed -nE 's/^PING [^(]*\(([0-9.]+)\).*/\1/p' | head -1 || true)
  if [[ -n "$ip" ]]; then echo "$ip"; return 0; fi
  # 2) ARP table scan for Raspberry Pi OUIs
  local mac oui
  while read -r ip mac; do
    [[ -z "$ip" || -z "$mac" ]] && continue
    for oui in "${PI_OUIS[@]}"; do
      if [[ "$mac" == "$oui"* ]]; then echo "$ip"; return 0; fi
    done
  done < <(arp -an 2>/dev/null | sed -nE 's/.*\(([0-9.]+)\) at ([0-9a-f:]+).*/\1 \2/p')
  return 1
}

echo "==============================================================="
echo "  Waiting for the Pi to come online (timeout ${TIMEOUT}s)"
echo "  Insert the SD card into the Pi and plug in power now."
echo "==============================================================="
start=$(date +%s)
last_report=0
resolved=""

while :; do
  now=$(date +%s); elapsed=$(( now - start ))
  (( elapsed > TIMEOUT )) && die "Timed out after ${TIMEOUT}s. Is the Pi powered, and is it on the same Wi-Fi network as this Mac?"

  for host in ark.local ark.lan 10.42.0.1; do
    if try_host "$host"; then resolved="$host"; break; fi
  done
  if [[ -z "$resolved" ]]; then
    ip=$(discover_pi_ip || true)
    if [[ -n "$ip" ]] && try_host "$ip"; then resolved="$ip"; fi
  fi
  [[ -n "$resolved" ]] && break

  if (( elapsed - last_report >= 15 )); then
    last_report=$elapsed
    printf '   … %3ds elapsed (still booting; first boot usually needs 60–120 s)\n' "$elapsed"
  fi
  sleep 5
done

ok "Pi is online and answering on SSH as pi@${resolved} (after ${elapsed}s)"

# Confirm the first-run script actually ran (proves hostname/user/key injection)
info "Verifying first-boot provisioning state…"
ssh "${SSH_OPTS[@]}" "pi@${resolved}" '
  echo "hostname : $(hostname)"
  echo "user     : $(id -un) groups=$(id -Gn)"
  echo "sudo     : $(sudo -n true 2>/dev/null && echo passwordless-OK || echo NEEDS-PASSWORD)"
  echo "arch     : $(uname -m)"
  echo "os       : $(. /etc/os-release; echo "$PRETTY_NAME")"
  echo "firstrun : $([ -f /var/log/ark-firstrun.log ] && echo log-present || echo NO-LOG)"
  echo "wifi     : $(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | tr "\n" " ")"
  echo "inet     : $(hostname -I)"
' || die "SSH worked once but the verification command failed"

echo
echo "ARK_HOST=${resolved}"
echo "$resolved" > /Users/basestar/Survival-Pi/logs/ark_host.txt
ok "Host saved to /Users/basestar/Survival-Pi/logs/ark_host.txt — now run 03-provision.sh"
