#!/usr/bin/env bash
# =============================================================================
#  PHASE 4 — survive vault
#
#  Installs the offline manuals and repair guides, builds a printable PDF of
#  every page, and writes a plain index so /vault/ is browsable on its own.
#  Everything here is static HTML: it must work with JavaScript disabled.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 4/7 — survive vault"

VAULT="$ARK_ROOT/vault"
SRC_VAULT="$ARK_SRC/vault"
LOG="$ARK_LOG_DIR/vault.log"

[[ -d "$SRC_VAULT" ]] || ark_die "missing $SRC_VAULT — re-run scripts/03-provision.sh first"

for d in "$VAULT" "$VAULT/manuals" "$VAULT/guides" "$VAULT/pdf" \
         "$VAULT/downloads" "$VAULT/apps"; do
  ark_ensure_dir "$d" 775 "$ARK_PI_USER:$ARK_PI_GROUP"
done

# ---- 1. the pages themselves ------------------------------------------------
ark_copy "$SRC_VAULT/manual.css"    "$VAULT/manual.css"    644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$SRC_VAULT/quick-card.html" "$VAULT/quick-card.html" 644 "$ARK_PI_USER:$ARK_PI_GROUP"

pages=0
for f in "$SRC_VAULT"/manuals/*.html "$SRC_VAULT"/guides/*.html; do
  [[ -f "$f" ]] || continue
  rel="${f#"$SRC_VAULT"/}"
  ark_copy "$f" "$VAULT/$rel" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
  pages=$(( pages + 1 ))
done
(( pages > 0 )) || ark_die "no manual pages found in $SRC_VAULT"
ark_ok "$pages manual page(s) installed"

# ---- 2. printable copies ----------------------------------------------------
# A phone is readable by candlelight; a printed sheet does not need charging.
ark_copy "$ARK_SRC/bin/ark-html2pdf" /usr/local/bin/ark-html2pdf 755 root:root

pdfs=0
for f in "$VAULT"/manuals/*.html "$VAULT"/guides/*.html "$VAULT"/quick-card.html; do
  [[ -f "$f" ]] || continue
  name="$(basename "$f" .html)"
  out="$VAULT/pdf/$name.pdf"
  if [[ -f "$out" && "$out" -nt "$f" ]]; then
    pdfs=$(( pdfs + 1 ))
    continue
  fi
  if python3 /usr/local/bin/ark-html2pdf "$f" "$out" >>"$LOG" 2>&1; then
    ark_info "printable: $name.pdf"
    pdfs=$(( pdfs + 1 ))
  else
    # a missing PDF must never block the rest of the install
    ark_warn "could not build $name.pdf (the web page still works)"
  fi
done
chown -R "$ARK_PI_USER:$ARK_PI_GROUP" "$VAULT/pdf"
ark_ok "$pdfs printable PDF(s) in $VAULT/pdf"

# ---- 2b. every page points at its own printable copy -------------------------
# The index lists the PDFs, but somebody already reading one page should not have
# to go back to the index to find that page on paper.  Idempotent: the marker
# makes a re-run a no-op.
linked=0
for f in "$VAULT"/manuals/*.html "$VAULT"/guides/*.html "$VAULT"/quick-card.html; do
  [[ -f "$f" ]] || continue
  name="$(basename "$f" .html)"
  [[ -f "$VAULT/pdf/$name.pdf" ]] || continue
  grep -q 'ark-print-link' "$f" && continue
  sed -i "s#</body>#<p class=\"ark-print-link\"><a href=\"/vault/pdf/$name.pdf\">Print this page (PDF)</a></p>\n</body>#" "$f"
  linked=$(( linked + 1 ))
done
(( linked > 0 )) && ark_info "printable link set on $linked page(s)" || true  # nothing to link


# ---- 3. the index -----------------------------------------------------------
# Titles and one-line descriptions are pulled straight from the pages so the
# index can never drift out of sync with what is actually installed.
entry() { # <relative-path> <section-class>
  local rel=$1 file="$VAULT/$1" title desc
  [[ -f "$file" ]] || return 0
  title="$(sed -n 's:.*<h1>\(.*\)</h1>.*:\1:p' "$file" | head -1)"
  desc="$(grep -o '<p class="lead">[^<]*' "$file" | head -1 | sed 's/<p class="lead">//')"
  title="$(printf '%s' "$title" | sed -e 's/<[^>]*>//g')"
  desc="$(printf '%s' "$desc" | sed -e 's/&nbsp;/ /g; s/&amp;/\&/g; s/&mdash;/-/g; s/&quot;/"/g')"
  if (( ${#desc} > 180 )); then
    desc="${desc:0:177}"
    desc="${desc% *}..."
  fi
  printf '  <li><a href="/vault/%s">%s</a><span class="d">%s</span></li>\n' \
         "$rel" "$title" "$desc"
}
IDX="$(mktemp)"
{
  cat <<'HEAD'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Manuals and guides — The Ark</title>
<link rel="stylesheet" href="/vault/manual.css">
<style>
  .group { margin: 26px 0 0; font-size: .78em; letter-spacing: .12em; text-transform: uppercase;
           color: var(--dim); border-bottom: 1px solid var(--line); padding-bottom: 8px; }
  ul.index { list-style: none; padding: 0; margin: 6px 0 0; }
  ul.index li { border-bottom: 1px solid var(--line); padding: 12px 0; }
  ul.index a { display: block; font-weight: 700; font-size: 1.02em; text-decoration: none; }
  ul.index .d { display: block; color: var(--dim); font-size: .9em; margin-top: 4px; }
</style>
</head>
<body>
<a class="back" href="/">← Back to The Ark</a>
<h1>Manuals and guides</h1>
<p class="lead">Written to be read while everything else is off. Every page works without JavaScript, prints cleanly, and has a PDF copy for the day the batteries are gone.</p>
HEAD

  printf '<p class="group" id="manuals">Manuals</p>\n<ul class="index">\n'
  for f in "$VAULT"/manuals/*.html; do
    [[ -f "$f" ]] && entry "manuals/$(basename "$f")"
  done
  printf '</ul>\n'

  printf '<p class="group" id="repair">Repair guides</p>\n<ul class="index">\n'
  for f in "$VAULT"/guides/*.html; do
    [[ -f "$f" ]] && entry "guides/$(basename "$f")"
  done
  printf '</ul>\n'

  printf '<p class="group" id="print">Print these</p>\n<ul class="index">\n'
  for f in "$VAULT"/quick-card.html "$VAULT"/pdf/*.pdf; do
    [[ -f "$f" ]] || continue
    rel="${f#"$VAULT"/}"
    label="$(basename "$rel")"
    label="${label%.html}"
    label="${label%.pdf}"
    label="$(printf '%s' "$label" | tr '-' ' ')"
    if [[ $rel == *.pdf ]]; then
      printf '  <li><a href="/vault/%s">%s — printable PDF</a><span class="d">Opens in your phone&rsquo;s reader. Print at 100%%, black and white is fine.</span></li>\n' \
             "$rel" "$label"
    else
      printf '  <li><a href="/vault/%s">%s — printable page</a><span class="d">Fits on one sheet. Read it on screen or send it to a printer.</span></li>\n' \
             "$rel" "$label"
    fi
  done
  printf '</ul>\n'

  cat <<'FOOT'
<p class="meta">These pages are stored on this device only. Nothing here contacts the internet, and nothing you read is logged.</p>
</body>
</html>
FOOT
} > "$IDX"

ark_write "$VAULT/index.html" 644 "$ARK_PI_USER:$ARK_PI_GROUP" < "$IDX"
rm -f "$IDX"
chown -R "$ARK_PI_USER:$ARK_PI_GROUP" "$VAULT"

# ---- 4. report --------------------------------------------------------------
ark_info "vault size: $(du -sh "$VAULT" | cut -f1)"
missing=0
for f in "$VAULT"/manuals/*.html "$VAULT"/guides/*.html; do
  [[ -f "$VAULT/pdf/$(basename "$f" .html).pdf" ]] || missing=$(( missing + 1 ))
done
(( missing == 0 )) || ark_warn "$missing page(s) have no PDF copy — the HTML still works"
ark_ok "phase 4 complete — open http://ark.lan/vault/"

