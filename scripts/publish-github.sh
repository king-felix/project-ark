#!/usr/bin/env bash
# =============================================================================
#  PROJECT ARK — publish this repo to a public GitHub repository.
#
#  The token is read from a file (default ~/.config/ark/gh.token, chmod 600) or
#  from $GITHUB_TOKEN. It is handed to git through a one-shot credential helper
#  on the command line, so it never lands in .git/config, in a remote URL, or in
#  the reflog. Nothing here is committed, and the file never leaves the Mac.
#
#  Usage:
#    bash scripts/publish-github.sh                    # owner from the token
#    GITHUB_OWNER=me REPO_NAME=project-ark bash scripts/publish-github.sh
# =============================================================================
set -Eeuo pipefail

ROOT="/Users/basestar/Survival-Pi"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/ark/gh.token}"
REPO_NAME="${REPO_NAME:-project-ark}"
DESC="Project ARK — an offline survival computer on a Raspberry Pi: 20 offline collections, 1290 books, 11 printable guides, captive-portal library."

say()  { printf '\033[1;36m→\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

TOKEN="${GITHUB_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  [[ -r "$TOKEN_FILE" ]] || die "no token: set GITHUB_TOKEN or write a PAT to $TOKEN_FILE (chmod 600)"
  TOKEN="$(tr -d '[:space:]' <"$TOKEN_FILE")"
fi
[[ -n "$TOKEN" ]] || die "token is empty"

api() {  # api METHOD PATH [BODY]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sS -X "$method" -H "Authorization: Bearer $TOKEN" \
      -H 'Accept: application/vnd.github+json' -d "$body" "https://api.github.com$path"
  else
    curl -sS -X "$method" -H "Authorization: Bearer $TOKEN" \
      -H 'Accept: application/vnd.github+json' "https://api.github.com$path"
  fi
}

json() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1],""))' "$1"; }

OWNER="${GITHUB_OWNER:-}"
if [[ -z "$OWNER" ]]; then
  OWNER="$(api GET /user | json login)"
  [[ -n "$OWNER" ]] || die "token is not valid (GET /user failed) — check the PAT and its scopes"
fi
say "account: $OWNER · repo: $REPO_NAME"

code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
        "https://api.github.com/repos/$OWNER/$REPO_NAME")"
if [[ "$code" == "404" ]]; then
  say "creating public repository"
  created="$(api POST /user/repos "{\"name\":\"$REPO_NAME\",\"private\":false,\"description\":\"$DESC\",\"has_issues\":true,\"has_wiki\":false}")"
  echo "$created" | grep -q '"full_name"' || { echo "$created" | head -20 >&2; die "repository creation failed"; }
  ok "created $(echo "$created" | json full_name)"
elif [[ "$code" == "200" ]]; then
  say "repository already exists — pushing to it"
else
  die "GitHub answered HTTP $code for /repos/$OWNER/$REPO_NAME"
fi

cd "$ROOT"
git remote remove origin 2>/dev/null || true   # plain URL only, no token in it
git remote add origin "https://github.com/$OWNER/$REPO_NAME.git"

say "pushing main + tags"
export ARK_GH_TOKEN="$TOKEN"   # read by the one-shot helper below, never printed
git -c credential.helper= \
    -c credential.helper='!f() { echo username=x-access-token; echo "password=$ARK_GH_TOKEN"; }; f' \
    -c http.extraheader= \
    push origin main --tags 2>&1 | sed 's/^/   /' || die "push failed (see above)"

# Proof, not a claim: ask GitHub what it actually holds now.
sleep 2
remote_tags="$(git ls-remote --tags origin | wc -l | tr -d ' ')"
remote_head="$(git ls-remote origin refs/heads/main | cut -c1-7)"
local_head="$(git rev-parse --short HEAD)"
[[ "$remote_head" == "$local_head" ]] || die "GitHub main is $remote_head but local is $local_head"
ok "GitHub main = $remote_head, $remote_tags tag refs, branch+tags pushed"
ok "public at https://github.com/$OWNER/$REPO_NAME"
