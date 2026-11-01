#!/usr/bin/env bash
# =============================================================================
#  PHASE 7 — the Ark's own Wi-Fi
#
#  Creates the "The-Ark-Offline" access point, makes every hostname resolve to
#  the portal while it is broadcasting, and installs the boot guard that brings
#  the box up broadcasting (BOOT_MODE=hotspot, the shipped default).  ark-net-watch
#  then asks the same question every minute, so a network that disappears after
#  boot is survived without a reboot.
#
#  NOTHING here switches the radio: you are most likely connected over that same
#  Wi-Fi right now.  Use "sudo ark-mode test 180" to try it deliberately.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 7/7 — broadcast and failsafe"

ENV_FILE="$ARK_ETC/ark.env"
# shellcheck disable=SC1090
. "$ENV_FILE"

HOTSPOT_CONNECTION="${HOTSPOT_CONNECTION:-ark-hotspot}"
WIFI_DEV="${WIFI_DEV:-wlan0}"

ark_apt_install network-manager dnsmasq-base iw

[[ -d "/sys/class/net/$WIFI_DEV" ]] || ark_die "no $WIFI_DEV on this machine — is the Wi-Fi chip present?"
rfkill unblock wifi >/dev/null 2>&1 || true          # a soft block survives reboots
ark_ok "wifi radio present and unblocked ($WIFI_DEV)"

# ---- the access point -------------------------------------------------------
if ! nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$HOTSPOT_CONNECTION"; then
  ark_info "creating the hotspot profile \"$HOTSPOT_CONNECTION\""
  nmcli connection add type wifi ifname "$WIFI_DEV" con-name "$HOTSPOT_CONNECTION" \
        ssid "$HOTSPOT_SSID" >/dev/null
fi

# Open network (no password) on purpose: the whole point is that a stranger with
# a dead phone can read the library.  autoconnect stays off — ark-mode decides.
nmcli connection modify "$HOTSPOT_CONNECTION" \
  802-11-wireless.mode      ap \
  802-11-wireless.ssid      "$HOTSPOT_SSID" \
  802-11-wireless.band      bg \
  802-11-wireless.channel   "$HOTSPOT_CHANNEL" \
  802-11-wireless.powersave 2 \
  ipv4.method               shared \
  ipv4.addresses            "$HOTSPOT_ADDRESS/24" \
  ipv4.never-default        yes \
  ipv6.method               ignore \
  connection.autoconnect    no
ark_ok "hotspot profile ready: ssid \"$HOTSPOT_SSID\", $HOTSPOT_ADDRESS/24, channel $HOTSPOT_CHANNEL"

# ---- remember which network this box joins at home --------------------------
# "Home" is a network the Ark JOINS, so it can never be the Ark's own access
# point.  This block used to store whichever wireless profile happened to be up
# while the phase ran — and on 31 Oct 2026 that was "ark-hotspot", because the
# box was broadcasting.  Every `ark-mode home` after that "joined the home
# network" by bringing the access point back up: the box reported a home network
# it was never on, and it could not be moved onto the house Wi-Fi at all —
# silently (docs/IMPLEMENTATION-LOG.md §36).  Which network to join is a
# *setting*, not an observation: staging/firstrun.sh saves the build-time Wi-Fi
# as "home-wifi", phase 2 writes that name into ark.env, and the owner's Network
# page saves more.  The decision itself lives in lib/home-network.sh so a test
# can drive it against a stand-in nmcli with no root and no radio.
# shellcheck source=../lib/home-network.sh
. "$ARK_SRC/lib/home-network.sh"

configured="${HOME_WIFI_CONNECTION:-$ARK_HOME_PROFILE}"
if ! ark_home_record "$ENV_FILE" "$configured" "$HOTSPOT_CONNECTION"; then
  ark_die "$ENV_FILE could not be written with the home network (${ARK_HOME_RESULT}) — fix HOME_WIFI_CONNECTION by hand before going on"
fi
case "$ARK_HOME_RESULT" in
  kept)
    HOME_WIFI_CONNECTION="$ARK_HOME_NAME"
    ark_ok "home network: \"$HOME_WIFI_CONNECTION\" (a network this box can join)" ;;
  recorded)
    HOME_WIFI_CONNECTION="$ARK_HOME_NAME"
    ark_warn "home network repaired in $ENV_FILE: was \"$configured\" (not a network this box can join — the Ark's own access point is never home), now \"$HOME_WIFI_CONNECTION\"" ;;
  *)
    HOME_WIFI_CONNECTION="$configured"
    ark_warn "no saved Wi-Fi this box could join — it keeps broadcasting until one is saved (https://ark.local:8443/admin/network, or: sudo ark-wifi-add --ssid NAME)" ;;
esac

# ---- every name points at the Ark while broadcasting ------------------------
# dnsmasq (started by NetworkManager in "shared" mode) reads this directory, so
# http://anything.at.all on a phone lands on the portal instead of an error.
ark_ensure_dir /etc/NetworkManager/dnsmasq-shared.d 755 root:root
ark_write /etc/NetworkManager/dnsmasq-shared.d/ark-wildcard.conf 644 root:root <<EOF
# Project ARK — while the hotspot is broadcast, resolve every name to the portal.
# Only read by the dnsmasq instance NetworkManager starts for "ipv4.method shared".
address=/#/$HOTSPOT_ADDRESS
EOF

# ---- what a cold boot should do ---------------------------------------------
# The Ark's own broadcast is the default: a box in a cupboard, with no router and
# nobody to type a command, must come up broadcasting.  "auto" restores the older
# habit of trying the home network first.  Written once; an existing answer is
# never overwritten.
BOOT_MODE="${BOOT_MODE:-hotspot}"
if grep -q '^BOOT_MODE=' "$ENV_FILE"; then
  ark_info "boot choice kept from $ENV_FILE: $BOOT_MODE"
else
  printf '\n# What a cold boot should do: "hotspot" (broadcast and stay) or "auto".\nBOOT_MODE="%s"\n' \
    "$BOOT_MODE" >> "$ENV_FILE"
  ark_ok "boot choice recorded in $ENV_FILE: BOOT_MODE=\"$BOOT_MODE\""
fi

# ---- scripts and the boot guard --------------------------------------------
for b in ark-mode ark-boot-guard ark-net-watch; do
  ark_copy "$ARK_SRC/bin/$b" "/usr/local/bin/$b" 755 root:root
done
# The owner's Network page (phase 60) saves a Wi-Fi network through this helper.
# It lives in sbin because it writes a NetworkManager profile, it reads the
# passphrase from **stdin** (never argv, which every process can read), and it
# writes nothing but that one profile.  The page reaches it with `sudo -n` as the
# portal's own account — proved at the end of this phase, because a door nobody
# has knocked on is not an installed door.
ark_copy "$ARK_SRC/bin/ark-wifi-add" /usr/local/sbin/ark-wifi-add 755 root:root
ark_ok "installed /usr/local/sbin/ark-wifi-add — how a network is saved from the owner's page"
ark_install_unit ark-boot-guard.service
systemctl enable ark-boot-guard.service >/dev/null 2>&1 \
  || ark_warn "could not enable ark-boot-guard.service"
# Every unit must be enabled AND running, so start it now — but with the network
# step suppressed: "ark-mode auto" must not drop the SSH session we run over.
systemctl set-environment GUARD_SKIP_NETWORK=1
if systemctl start ark-boot-guard.service; then
  systemctl unset-environment GUARD_SKIP_NETWORK
  if [[ "$(systemctl is-active ark-boot-guard.service 2>/dev/null)" == "active" ]]; then
    ark_ok "boot guard enabled + started (network step suppressed for this run)"
  else
    ark_warn "boot guard ran but is not active — journalctl -u ark-boot-guard"
  fi
else
  systemctl unset-environment GUARD_SKIP_NETWORK
  ark_warn "boot guard did not start — journalctl -u ark-boot-guard"
fi

# ---- the network after boot -------------------------------------------------
# ark-boot-guard decides once, on the way up.  This asks the same question every
# minute for the rest of the day: if the home Wi-Fi goes away, the hotspot comes
# up on its own; if it comes back while nobody is using the hotspot, the box
# returns by itself.  Installing it changes nothing now — it only looks.
ark_install_unit ark-net-watch.service
ark_install_unit ark-net-watch.timer
if systemctl enable --now ark-net-watch.timer >/dev/null 2>&1; then
  ark_ok "network watchdog enabled + started (checks every minute)"
else
  ark_warn "could not enable ark-net-watch.timer — the box fails over at boot only"
fi
if watch_view="$(/usr/local/bin/ark-net-watch --dry-run 2>&1)"; then
  ark_info "watchdog sees now: $watch_view"
else
  ark_warn "the watchdog could not read the network: $watch_view"
fi

# ---- prove the pieces are in place (without changing the radio) -------------
mode_shown="$(nmcli -g 802-11-wireless.mode connection show "$HOTSPOT_CONNECTION" 2>/dev/null || true)"
method_shown="$(nmcli -g ipv4.method connection show "$HOTSPOT_CONNECTION" 2>/dev/null || true)"
printf '   profile %-12s mode=%s  ipv4=%s\n' "$HOTSPOT_CONNECTION" "${mode_shown:-?}" "${method_shown:-?}"
printf '   boot choice   %s (BOOT_MODE in %s)\n' "${BOOT_MODE:-hotspot}" "$ENV_FILE"
case "${BOOT_MODE:-hotspot}" in
  hotspot|auto|home) ;;
  *) ark_warn "BOOT_MODE=\"${BOOT_MODE:-}\" is not hotspot, auto or home — a boot would broadcast instead" ;;
esac
# Which network this box joins is a setting, so prove what is *written* — not
# what this run believes it wrote.  A home network the box cannot join is a fault
# worth saying at install time: the only other place it shows is
# `ark-mode status`, and it cost a day of the box being stuck on its own hotspot
# with nothing logged and no page saying so (docs/IMPLEMENTATION-LOG.md §36).
recorded_home="$(sed -n 's|^HOME_WIFI_CONNECTION="\(.*\)"$|\1|p' "$ENV_FILE" 2>/dev/null | head -n1 || true)"
printf '   home network  %s (HOME_WIFI_CONNECTION in %s)\n' "${recorded_home:-none}" "$ENV_FILE"
if [[ -z "$recorded_home" ]]; then
  ark_warn "$ENV_FILE records no home network — this box keeps broadcasting until one is saved"
elif ! ark_home_joinable "$recorded_home" "$HOTSPOT_CONNECTION"; then
  ark_warn "\"$recorded_home\" is not a network this box can join — it will broadcast instead (the Ark's own access point is never home)"
fi
[[ "${mode_shown:-}" == "ap" ]] || ark_warn "hotspot profile is not in access-point mode"
[[ "${method_shown:-}" == "shared" ]] || ark_warn "hotspot profile is not sharing its connection"
[[ -f /etc/NetworkManager/dnsmasq-shared.d/ark-wildcard.conf ]] || ark_warn "wildcard DNS file is missing"
[[ "$(systemctl is-enabled ark-boot-guard.service 2>/dev/null || true)" == "enabled" ]] \
  || ark_warn "boot guard is not enabled"
[[ "$(systemctl is-enabled ark-net-watch.timer 2>/dev/null || true)" == "enabled" ]] \
  || ark_warn "network watchdog timer is not enabled"
[[ "$(systemctl is-active ark-net-watch.timer 2>/dev/null || true)" == "active" ]] \
  || ark_warn "network watchdog timer is not running"

# ---- the owner's Network page, knocked on as the account that uses it -------
# Neither probe changes the network or leaves a file behind: `--check` says what
# it *would* write, and a request for zero minutes is refused before anything is
# written.  What is proved is the door — that the account the portal runs as can
# reach both helpers with no password prompt, which is the one thing that would
# otherwise only be discovered by the owner, an hour later, with the page saying
# "The box would not do that."
# The rehearsal name has to be one the helper would ACCEPT, or the proof proves
# the wrong thing.  It used to be "A network this box has never joined" — 35
# characters, while `ark-wifi-add` refuses anything over 32 — so on 1 Nov 2026
# this line announced "cannot reach ark-wifi-add as pi" about a helper that had
# just answered in its own words.  And a refusal in the helper's own words is
# still proof the account reached it: a door nobody can knock on says
# "sudo: a password is required" or "No such file", never "Not done:".
wifi_check=""; wifi_rc=0
wifi_check="$(runuser -u "$ARK_PI_USER" -- \
      sudo -n /usr/local/sbin/ark-wifi-add --ssid "ark rehearsal network" --check \
      <<<"ark-rehearsal-only-psk" 2>&1)" || wifi_rc=$?
if (( wifi_rc == 0 )); then
  ark_ok "the Network page can save a Wi-Fi network as $ARK_PI_USER (${wifi_check:-no words})"
elif grep -q 'Not done:' <<<"$wifi_check"; then
  ark_warn "the Network page reached ark-wifi-add as $ARK_PI_USER but the rehearsal itself was refused (the door works): ${wifi_check:-no words}"
else
  ark_warn "the Network page cannot reach ark-wifi-add as $ARK_PI_USER — saving a network will fail: ${wifi_check:-no output}"
fi
lan_check=""
if lan_check="$(runuser -u "$ARK_PI_USER" -- \
      sudo -n /usr/local/bin/ark-mode request-lan --minutes 0 2>&1)"; then
  ark_warn "the Network page asked for zero minutes and the helper accepted it — that would be honoured at the next boot: $lan_check"
elif grep -q 'greater than zero' <<<"$lan_check"; then
  ark_ok "the Network page can ask for one restart on a saved network as $ARK_PI_USER (nothing was written)"
else
  ark_warn "the Network page cannot reach ark-mode request-lan as $ARK_PI_USER — the restart button will fail: ${lan_check:-no output}"
fi

ark_info "try it on purpose:  sudo ark-mode test 180   (SSH drops while it runs)"
ark_info "hotspot on/off:     sudo ark-mode hotspot | sudo ark-mode auto"
ark_info "boot choice:        BOOT_MODE in $ENV_FILE (currently ${BOOT_MODE:-hotspot})"
ark_info "what is happening:  sudo ark-mode status"
ark_info "network decisions:  tail -n 20 $ARK_ROOT/logs/net-watch.log"
ark_ok "phase 7 complete — a cold boot comes up broadcasting, and the box stays reachable"
