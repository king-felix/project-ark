"""Ask the Ark — the helper that reads this card and answers in plain words.

Everything here is local.  The words never leave the box: the model is a file on the
card, the engine listens on the loopback address and nowhere else, and nginx is the
only thing that may pass a question to it.  A box with no model is a supported state —
``/ask`` says so in one sentence and the rest of the portal is untouched.

Four rules shape this module:

* **A question is never refused for being a question.**  The only refusal is "there is
  no brain on this card yet", and it names what is missing.
* **The brief is built, not remembered.**  ``ark-build-ai-brief`` writes what is really
  on the card (how many collections, books, guides, games, and the three addresses a
  household is told to try); the helper is given that, so it points at pages that
  exist instead of inventing them.
* **One answer at a time.**  A Pi 4 answering a question is using every core it has;
  a second visitor waits for a sentence, not for the box to crawl.
* **A number the box cannot measure is unknown, never zero** — the same rule as
  everywhere else in this repo.
"""

from __future__ import annotations

import html
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# The engine listens here and nowhere else: nginx proxies /api/ask/* to it, so no
# reader ever holds a connection to the model server directly.
AI_HOST = "127.0.0.1"
AI_PORT = 8089

# A question longer than this is refused with a sentence naming the limit (§5: never
# silently truncate what a person wrote).
MAX_QUESTION = 900

# How long one answer may take.  A 2B model on a Pi 4 CPU is slow on purpose: it is
# given a whole minute before the page gives up, and the streamed answer keeps
# arriving while it works.
ANSWER_SECONDS = 150
CONNECT_SECONDS = 5

# Answer length in tokens and in characters.  Both are ceilings, never targets.
MAX_TOKENS = 380
MAX_ANSWER = 6000

# Where the pack of words about this card lives.
BRIEF_NAME = "ark-system-brief.md"

# The house style, written once.  It is the only instruction the model gets about
# tone, and every rule in it comes from the portal's own rules (no jargon, no
# invented services, say when unsure).
HOUSE_RULES = (
    "You are the helper inside an offline box that a household uses when the internet "
    "is gone. Answer in plain English, in at most 120 words, in short sentences. "
    "Never use these words: Kiwix, ZIM, Calibre, OPDS, FastAPI, nginx, systemd, SQLite, "
    "APK, port, localhost. Never invent a page, an address or a phone number. If the box "
    "holds a page about it, name that page in the words the page itself uses. If you are "
    "not sure, say so in one sentence and say what the person should look at instead. "
    "Never give a dose of medicine, and never tell somebody to leave a wound or a burn "
    "untreated: point them at the first-aid page instead."
)

# The one sentence per kind of question.  ``route()`` picks one; the retrieval step
# uses the same word to decide what to put in front of the model.
ROUTES = (
    ("guide", "the manuals and guides on this box",
     ("first aid", "bleed", "bleeding", "burn", "cpr", "choke", "wound", "fever",
      "poison", "splint", "fracture", "shock", "water", "purif", "filter", "boil",
      "fire", "shelter", "warm", "cold", "power", "battery", "solar", "generator",
      "leak", "pipe", "tap", "toilet", "waste", "sanitation", "food", "store",
      "seed", "plant", "garden", "harvest", "pest", "repair", "glue", "wire",
      "fuse", "roof", "mould", "mold", "insect", "rat", "snake", "dog")),
    ("library", "the offline library and the books on this box",
     ("book", "books", "novel", "author", "read", "reading", "chapter", "story",
      "encyclopedia", "encyclopaedia", "wikipedia", "article", "history", "define",
      "definition", "who was", "what year")),
)


# ---------------------------------------------------------------------------
#  where things live — read at call time, so a test can point ARK_ROOT anywhere
# ---------------------------------------------------------------------------
def ark_root() -> Path:
    return Path(os.environ.get("ARK_ROOT", "/srv/ark"))


def ai_dir() -> Path:
    return Path(os.environ.get("ARK_AI_DIR", str(ark_root() / "ai")))


def models_dir() -> Path:
    return Path(os.environ.get("ARK_AI_MODELS", str(ai_dir() / "models")))


def brief_path() -> Path:
    return Path(os.environ.get("ARK_AI_BRIEF", str(ai_dir() / BRIEF_NAME)))


def engine_path() -> Path:
    """The engine binary the unit runs.  A path, not a name: ``systemd`` has no PATH."""
    return Path(os.environ.get("ARK_AI_ENGINE", "/usr/local/bin/llama-server"))


def base_url() -> str:
    return os.environ.get("ARK_AI_URL", f"http://{AI_HOST}:{AI_PORT}")


def model_path():
    """The newest ``.gguf`` on the card, or ``None``.

    A half-downloaded file is named ``.part`` and is therefore invisible: the helper
    must never try to open something that is still arriving.  An unreadable folder is
    ``None`` as well — "no model" and "cannot look" both mean "cannot answer", and
    neither of them is an error a reader should see.
    """
    try:
        found = [p for p in models_dir().iterdir()
                 if p.is_file() and p.suffix.lower() == ".gguf"]
    except OSError:
        return None
    if not found:
        return None
    try:
        return max(found, key=lambda p: p.stat().st_mtime)
    except OSError:
        return found[0]


def model_facts() -> dict:
    """Name and size of the model on the card — both unknown when there is none."""
    path = model_path()
    if path is None:
        return {"name": "", "bytes": None}
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return {"name": path.name, "bytes": size}


# ---------------------------------------------------------------------------
#  is anything answering? — the one probe, shared with ark_health
# ---------------------------------------------------------------------------
def answering(timeout: float = 0.8) -> bool:
    """True when the engine answered HTTP at all.

    Delegated to ``ark_health.answers()`` on purpose: one place decides what "up"
    means (any status code counts, a refused socket does not) and it is cached for
    ten seconds, so a phone left on ``/ask`` does not start a process every second.
    Imported here rather than at module top because ``ark_health`` asks this module
    the same question about the helper's own row.
    """
    import ark_health
    return ark_health.answers(base_url() + "/health", timeout=timeout)


def status() -> dict:
    """Everything a page needs to say the truth about the helper in one line."""
    model = model_facts()
    engine = engine_path().is_file()
    live = answering() if (engine and model["name"]) else False
    return {
        "engine": engine,
        "model": model["name"],
        "model_bytes": model["bytes"],
        "answering": live,
        "available": bool(engine and model["name"] and live),
        "address": base_url(),
        "max_question": MAX_QUESTION,
    }


# The sentence a reader sees when the helper cannot answer.  One entry per reason,
# in the words of somebody who has never heard of a model or an engine.
NOT_READY = {
    "engine": ("The helper is not fitted on this box", 
               "Nothing else has changed: every library, book, guide and game is "
               "still here and still works."),
    "model": ("The helper is waiting for its words",
              "The box has no language file on it yet, so it cannot answer questions. "
              "Everything else on the box still works."),
    "silent": ("The helper is not answering right now",
               "It may still be waking up. Everything else on the box still works."),
}


def not_ready_reason() -> str:
    """Which of the three it is, or ``""`` when the helper can answer."""
    facts = status()
    if not facts["engine"]:
        return "engine"
    if not facts["model"]:
        return "model"
    if not facts["answering"]:
        return "silent"
    return ""


def not_ready_sentence() -> tuple:
    """``(sentence, hint)`` for the page, or ``("", "")`` when the helper is up."""
    reason = not_ready_reason()
    if not reason:
        return ("", "")
    return NOT_READY[reason]


def advice() -> str:
    """The owner's side of the same fact: what to do about it, in one sentence."""
    reason = not_ready_reason()
    if reason == "engine":
        return ("Run: sudo bash /srv/ark/src/install-ark.sh --only 58-ai  (it copies the "
                "engine in from the folder it was built in)")
    if reason == "model":
        return ("Put a .gguf file in /srv/ark/ai/models (ark-ai-model-fetch can fetch one "
                "on a box that still has internet), then: sudo systemctl restart ark-ai")
    if reason == "silent":
        return ("Look at: journalctl -u ark-ai -n 40 --no-pager  (a model the engine "
                "cannot read is the usual reason)")
    return ""
# Words that say nothing about which page is wanted.  Kept small on purpose: a page
# that matches "water" is useful, a page that matches "what" is noise.
STOPWORDS = {"about", "after", "again", "against", "being", "could", "doing",
             "every", "first", "from", "have", "help", "here", "into", "just",
             "know", "like", "make", "much", "need", "only", "other", "should",
             "some", "that", "their", "them", "then", "there", "these", "they",
             "thing", "this", "those", "want", "what", "when", "where", "which",
             "while", "with", "would", "your"}


# ---------------------------------------------------------------------------
#  which shelf the question belongs on
# ---------------------------------------------------------------------------
GENERAL = ("general", "what the helper knows by itself")


def route(question: str) -> tuple:
    """``(kind, shelf)`` for a question.  Pure, cheap and testable.

    The keyword tables are deliberately blunt: a wrong guess costs one answer that is
    slightly less pointed, and a clever guess that puts a first-aid question on the
    book shelf costs the answer itself.
    """
    text = " " + re.sub(r"[^a-z0-9 ]+", " ", (question or "").lower()) + " "
    for kind, shelf, words in ROUTES:
        for word in words:
            if f" {word} " in text:
                return (kind, shelf)
    return GENERAL


def vault_dir() -> Path:
    return Path(os.environ.get("ARK_VAULT", str(ark_root() / "vault")))


# The pages the helper may read.  A short list of folders, not a walk of the card: a
# helper that reads everything takes minutes to answer a question about a tap.
READABLE_FOLDERS = ("manuals", "guides")


def _plain(markup: str) -> str:
    """Visible words of an HTML page, with the tags, the styles and the scripts gone."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    text = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def pages() -> list:
    """Every readable page on the card: ``(title, relative name, text)``.

    Read at call time.  An unreadable page is skipped — the helper answers from what
    it can see, never from a guess about what the page might have said.
    """
    out = []
    root = vault_dir()
    for folder in READABLE_FOLDERS:
        try:
            found = sorted((root / folder).glob("*.html"))
        except OSError:
            continue
        for path in found:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            title = ""
            found_title = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", raw)
            if found_title:
                title = _plain(found_title.group(1))
            if not title:
                title = path.stem.replace("-", " ").capitalize()
            out.append((title, f"{folder}/{path.name}", _plain(raw)))
    return out


def _score(question: str, text: str) -> int:
    """How many of the question's own words this page contains."""
    words = {w for w in re.findall(r"[a-z]{4,}", (question or "").lower())
             if w not in STOPWORDS}
    lowered = text.lower()
    return sum(1 for w in words if w in lowered)


def _sentences(text: str, wanted: int = 3, limit: int = 900) -> str:
    """The first few sentences of a page — enough to point at, short enough to fit."""
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 30]
    return " ".join(parts[:wanted])[:limit]


def context_for(question: str, kind: str) -> tuple:
    """``(context text, pages used)`` — what the model is allowed to read.

    Always the brief (what is on this card), and for a guide question the pages whose
    words overlap the question's words.  Nothing personal is ever included: the
    household's own answers are never part of an answer a visitor can ask for.
    """
    chunks, used = [], []
    try:
        brief = brief_path().read_text(encoding="utf-8")
    except OSError:
        brief = ""
    if brief:
        chunks.append("What is on this box:\n" + brief.strip()[:2000])
    if kind == "guide":
        scored = []
        for title, name, text in pages():
            score = _score(question, text)
            if score:
                scored.append((score, title, name, text))
        scored.sort(key=lambda row: -row[0])
        for score, title, name, text in scored[:3]:
            used.append({"title": title, "name": name})
            chunks.append("From the page called “" + title + "” (" + name + "):\n"
                          + _sentences(text))
    return ("\n\n".join(chunks), used)


def messages(question: str, kind: str = "") -> list:
    """The exact conversation sent to the engine, built in one place."""
    kind = kind or route(question)[0]
    shelf = dict((k, s) for k, s, _ in ROUTES).get(kind, GENERAL[1])
    context, _used = context_for(question, kind)
    system = HOUSE_RULES + " This question is about " + shelf + "."
    user = (question or "").strip()
    if context:
        user = context + "\n\nQuestion: " + user
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]




# ---------------------------------------------------------------------------
#  one answer at a time — the box has four cores and no spare ones
# ---------------------------------------------------------------------------
_SLOT = threading.Lock()

BUSY_SENTENCE = ("Somebody else is asking the helper right now. Try again in a minute — "
                 "everything else on the box still works while you wait.")
SORRY_SENTENCE = ("The helper could not put an answer together. Everything else on the box "
                  "still works; the guides and the library are the place to look.")


def busy() -> bool:
    """True while another question is being answered."""
    return _SLOT.locked()


def clean_question(text) -> tuple:
    """``(question, problem)`` — trimmed, bounded, refused with the limit named."""
    question = re.sub(r"\s+", " ", str(text or "")).strip()
    if not question:
        return ("", "Type a question first.")
    if len(question) > MAX_QUESTION:
        return ("", "That question is too long. Please keep it under "
                    + str(MAX_QUESTION) + " characters and ask the most important part.")
    return (question, "")


def _payload(question: str, stream: bool) -> bytes:
    body = {
        "messages": messages(question),
        "max_tokens": MAX_TOKENS,
        "temperature": 0.3,
        "top_p": 0.9,
        "stream": bool(stream),
        "cache_prompt": True,
    }
    return json.dumps(body).encode("utf-8")


def _request(question: str, stream: bool):
    req = urllib.request.Request(
        base_url() + "/v1/chat/completions", data=_payload(question, stream),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=ANSWER_SECONDS)


def ask(question: str) -> tuple:
    """``(answer, problem)``.  Never raises: a helper that cannot answer says so."""
    question, problem = clean_question(question)
    if problem:
        return ("", problem)
    reason = not_ready_reason()
    if reason:
        return ("", NOT_READY[reason][0])
    if not _SLOT.acquire(blocking=False):
        return ("", BUSY_SENTENCE)
    try:
        with _request(question, stream=False) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
        choices = data.get("choices") or []
        text = ""
        if choices:
            text = (choices[0].get("message") or {}).get("content") or ""
        text = (text or "").strip()
        if not text:
            return ("", SORRY_SENTENCE)
        return (text[:MAX_ANSWER], "")
    except (urllib.error.URLError, OSError, ValueError, KeyError,
            http.client.HTTPException) as exc:
        # The reason stays in the journal; the reader gets one honest sentence.
        print(f"ark-ai: could not answer ({exc})", flush=True)
        return ("", SORRY_SENTENCE)
    finally:
        _SLOT.release()


def _delta(raw: bytes) -> str:
    """One streaming line from the engine, as the words it carries (or ``""``)."""
    line = raw.decode("utf-8", "replace").strip()
    if not line.startswith("data:"):
        return ""
    body = line[5:].strip()
    if not body or body == "[DONE]":
        return ""
    try:
        chunk = json.loads(body)
    except ValueError:
        return ""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    piece = (first.get("delta") or {}).get("content")
    if piece is None:
        # Some builds answer in the older shape; both are read the same way.
        piece = first.get("text") or ""
    return piece or ""


def ask_stream(question: str):
    """Yield an answer as it is written.  Always yields *something*.

    The first thing yielded is either the answer's first words or one plain sentence
    explaining why there are none.  "Somebody else is asking" is decided before the
    first byte, so the page can say *wait* instead of showing an empty box.
    """
    question, problem = clean_question(question)
    if problem:
        yield problem
        return
    reason = not_ready_reason()
    if reason:
        yield NOT_READY[reason][0] + ". " + NOT_READY[reason][1]
        return
    if not _SLOT.acquire(blocking=False):
        yield BUSY_SENTENCE
        return
    written = 0
    try:
        with _request(question, stream=True) as resp:
            for raw in resp:
                piece = _delta(raw)
                if not piece:
                    continue
                written += len(piece)
                if written > MAX_ANSWER:
                    break
                yield piece
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        print(f"ark-ai: stream stopped ({exc})", flush=True)
        if written:
            yield "\n\n(The helper stopped answering there. What it wrote is above.)"
    finally:
        _SLOT.release()
    if not written:
        yield SORRY_SENTENCE


# ---------------------------------------------------------------------------
#  the brief — what is on this card, written down for the model
# ---------------------------------------------------------------------------
def build_brief(dry_run: bool = False) -> dict:
    """Write ``ark-system-brief.md``.  Returns what was written, and where.

    Every number comes from the same readers the pages use (``ark_health``), so the
    brief cannot claim a shelf the portal does not show.  A number the box cannot
    measure is left out rather than printed as zero (the ``None`` rule).
    """
    import ark_arcade
    import ark_cards
    import ark_db
    import ark_essentials
    import ark_health

    lines = ["This is a box in a house, with no internet. It answers on its own Wi-Fi.",
             ""]
    try:
        collections = ark_health.library_books()
    except Exception:
        collections = []
    if collections:
        names = [str(b.get("title") or "") for b in collections[:6]]
        lines.append("Offline Library: " + str(len(collections)) + " collections to read "
                     "in a browser — " + ", ".join(names)
                     + ("." if len(collections) <= 6
                        else ", and " + str(len(collections) - 6) + " more."))
    size = ark_health.library_bytes()
    if size:
        lines.append("The library takes " + ark_health.human_size(size) + " on the card.")
    books = ark_health.book_count()
    if books:
        lines.append("Books: " + str(books) + " full-length books on a shelf (/books/).")
    guides = ark_health.guide_pages()
    if guides:
        names = sorted(p.stem.replace("-", " ") for p in guides)
        lines.append("Manuals and Guides: " + str(len(guides)) + " short pages (/vault/), "
                     "including " + ", ".join(names[:10]) + ".")
    try:
        with ark_db.read() as conn:
            games = ark_arcade.published_games(conn)
    except Exception:
        games = []
    if games:
        names = [str(g.get("title") or "") for g in games[:6]]
        lines.append("Games: " + str(len(games)) + " small games that play in the browser "
                     "(/arcade/) — " + ", ".join(names)
                     + ("." if len(games) <= 6 else ", and more."))
    lines.append("Message Board (/board/): notes for the next person.")
    lines.append("File Box (/files/): files shared on this Wi-Fi.")
    lines.append("Household (/household/): who lives here, and what to do when something "
                 "goes wrong. Its answers belong to the household and are never part of an "
                 "answer you give.")
    try:
        pack = ark_essentials.status(ark_essentials.pack_dir())
    except Exception:
        pack = {}
    if pack.get("present"):
        lines.append("A pack of the pages can be downloaded from /essentials.zip and opened "
                     "on any computer with no network.")
    lines.append("")
    lines.append("The addresses a person can type: "
                 + ", ".join(card["address"] for card in ark_cards.cards()) + ".")
    lines.append("")
    lines.append("When a question is about a person's own house, say which page on the box "
                 "answers it. Never invent a page, a phone number or a dose.")
    text = "\n".join(lines) + "\n"
    path = brief_path()
    if not dry_run:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".partial")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            return {"written": False, "path": str(path), "text": text,
                    "problem": "could not write the brief (" + str(exc) + ")"}
    return {"written": not dry_run, "path": str(path), "text": text, "problem": ""}

