#!/usr/bin/env bash
# =============================================================================
#  PROJECT ARK — which Wi-Fi is "home"?  (sourced, never executed directly)
#
#  "Home" is a network the Ark JOINS.  This box's own access point therefore can
#  never be home, however the radio happens to be configured while an installer
#  runs — a box cannot join its own broadcast.
#
#  Phase 7 used to answer that question by looking at whichever wireless profile
#  was up at the time, and on 31 Oct 2026 the box was broadcasting, so it wrote
#  "ark-hotspot" into /etc/ark/ark.env as the network to go home to.  Every
#  `ark-mode home` after that "joined the home network" by bringing the access
#  point back up: the box reported a home network it was never on, and it could
#  not be moved onto the house Wi-Fi at all — with nothing logged and nothing on
#  any page saying so (docs/IMPLEMENTATION-LOG.md §36).
#
#  Which network to join is a *setting*, not an observation:
#    * staging/firstrun.sh saves the build-time Wi-Fi as the profile "home-wifi";
#    * phase 2 writes HOME_WIFI_CONNECTION="home-wifi" into ark.env;
#    * the owner's Network page saves more profiles through ark-wifi-add.
#  So phase 7 keeps the configured name when it is a profile this box could
#  really join, repairs it when it is not, and never invents one from whatever
#  the radio happens to be doing.  Nothing here writes anything except ark.env,
#  and nothing here switches the radio.
#
#  Kept in lib/ rather than inline in the phase so the decision can be driven by
#  a test with a stand-in nmcli and no root: tests/test_network.py runs these
#  functions against a fake radio, and pi/steps/70-network.sh sources this file.
# =============================================================================

ARK_HOME_PROFILE="${ARK_HOME_PROFILE:-home-wifi}"   # the profile firstrun.sh saves
ARK_HOTSPOT_AP="${ARK_HOTSPOT_AP:-ark-hotspot}"     # the profile phase 7 creates

# What NetworkManager says a profile is for: "ap" is this box broadcasting,
# "infrastructure" is a network to join, nothing at all is a profile this box
# does not have.  A question, not an action — a missing nmcli answers "".
ark_home_mode() { # <profile>
  local profile="${1:-}"
  [[ -n "$profile" ]] || return 0
  nmcli -g 802-11-wireless.mode connection show "$profile" 2>/dev/null | head -n1 || true
}

# Would `ark-mode home` really be able to join it?  One rule, used by the phase
# and by the tests: the profile exists, it is a station, and it is not the Ark's
# own access point.  The name check comes first so this box's broadcast is
# refused even on a machine where nmcli cannot answer about its mode.
ark_home_joinable() { # <profile> [hotspot]
  local profile="${1:-}" hotspot="${2:-${HOTSPOT_CONNECTION:-$ARK_HOTSPOT_AP}}"
  [[ -n "$profile" ]] || return 1
  [[ "$profile" != "$hotspot" ]] || return 1
  [[ "$(ark_home_mode "$profile")" == "infrastructure" ]]
}

# The name worth recording: the one already configured when it can be joined,
# then the build-time profile, then any station profile that is up now (a box
# whose Wi-Fi profile was renamed still has the right network saved).  Prints one
# name, or nothing at all when this machine has nothing it could join — and an
# empty answer is a fact to say out loud, never a name to invent.
ark_home_choose() { # [configured] [hotspot]
  local configured="${1:-${HOME_WIFI_CONNECTION:-$ARK_HOME_PROFILE}}"
  local hotspot="${2:-${HOTSPOT_CONNECTION:-$ARK_HOTSPOT_AP}}" name
  if ark_home_joinable "$configured" "$hotspot"; then
    printf '%s' "$configured"
    return 0
  fi
  if ark_home_joinable "$ARK_HOME_PROFILE" "$hotspot"; then
    printf '%s' "$ARK_HOME_PROFILE"
    return 0
  fi
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    if ark_home_joinable "$name" "$hotspot"; then
      printf '%s' "$name"
      return 0
    fi
  done < <(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null \
             | awk -F: '$2 ~ /wireless/ {print $1}')
  return 0
}

# Record the answer in ark.env — the one place this file is written for it.  Sets
# two globals and prints nothing, so the caller owns every word a person reads:
#   ARK_HOME_RESULT = kept | recorded | none | no-file | unwritable
#   ARK_HOME_NAME   = the profile worth joining, or "".
#
# `none` is not a failure: a box with no saved Wi-Fi is a supported state (it
# keeps broadcasting), and the caller must say so rather than write a name that
# is not on the machine.  A repair is written to a temp file, read back, and only
# then moved into place, so a cut can never leave ark.env half-written
# (§8.8 rule 17) and a wrong answer is never believed before it is proved.
ark_home_record() { # <env-file> [configured] [hotspot]
  local file="${1:-}"
  local configured="${2:-${HOME_WIFI_CONNECTION:-$ARK_HOME_PROFILE}}"
  local hotspot="${3:-${HOTSPOT_CONNECTION:-$ARK_HOTSPOT_AP}}" chosen tmp mode
  ARK_HOME_RESULT="none"
  ARK_HOME_NAME=""
  [[ -n "$file" && -f "$file" ]] || { ARK_HOME_RESULT="no-file"; return 1; }
  chosen="$(ark_home_choose "$configured" "$hotspot")"
  ARK_HOME_NAME="$chosen"
  [[ -n "$chosen" ]] || return 0
  if grep -qx "HOME_WIFI_CONNECTION=\"$chosen\"" "$file"; then
    ARK_HOME_RESULT="kept"
    return 0
  fi
  tmp="$file.ark-home.$$"
  if grep -q '^HOME_WIFI_CONNECTION=' "$file"; then
    sed "s|^HOME_WIFI_CONNECTION=.*|HOME_WIFI_CONNECTION=\"$chosen\"|" "$file" >"$tmp"
  else
    {
      cat "$file"
      printf '\n# The Wi-Fi this box joins at home.  A station, never this box'"'"'s own access point.\n'
      printf 'HOME_WIFI_CONNECTION="%s"\n' "$chosen"
    } >"$tmp"
  fi
  if ! grep -qx "HOME_WIFI_CONNECTION=\"$chosen\"" "$tmp"; then
    # A name holding a character `sed` reads as syntax (`&`, `|`) lands here as a
    # refusal rather than as a corrupted line: nothing is moved into place unless
    # the temp file really carries the answer we asked for.
    rm -f "$tmp"
    ARK_HOME_RESULT="unwritable"
    return 1
  fi
  # A file written as root must not silently lose the mode the box reads it with.
  mode="$(stat -c %a "$file" 2>/dev/null || stat -f %Lp "$file" 2>/dev/null || true)"
  [[ -z "$mode" ]] || chmod "$mode" "$tmp" 2>/dev/null || true
  if ! mv -f "$tmp" "$file" 2>/dev/null; then
    rm -f "$tmp"
    ARK_HOME_RESULT="unwritable"
    return 1
  fi
  ARK_HOME_RESULT="recorded"
  return 0
}
