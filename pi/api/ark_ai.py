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

# How fast this hardware really is.  Measured on the box itself (26 Oct 2026) with a
# 1.5B model in four bits on three of the Pi's four cores: 871 tokens of prompt were
# read in 128 s (6.8 tokens a second) and 80 tokens of answer were written in 25 s
# (3.2 a second), about six characters to a token.  These numbers are the budget
# below, so "let us helpfully include a bit more" can be refused by arithmetic
# instead of by a person waiting two and a half minutes for an answer.
CHARS_PER_TOKEN = 6
READ_TOKENS_PER_SECOND = 6.8
WRITE_TOKENS_PER_SECOND = 3.2

# How long one answer may take.  Long enough for the worst case below with room to
# spare on a busy box.  **nginx must wait longer than this** — a proxy that gives up
# first turns a slow answer into a 504, which is a lie about a box that is working
# (recorded defect: 120 s at the proxy, 150 s here, so every real question 504'd).
ANSWER_SECONDS = 200
CONNECT_SECONDS = 5

# Answer length in tokens and in characters.  Both are ceilings, never targets.
# 160 tokens is about 50 s of writing here and fits a plain-English answer; it was
# 380, which is over two minutes of writing on this hardware before a person sees
# the first useful sentence.
MAX_TOKENS = 160
MAX_ANSWER = 6000

# Everything the model is given for one question, in characters — reading is the
# expensive half on a Pi 4, so this is a budget and not a catalogue.  With the
# measured speeds above it is about 74 s of reading, and MAX_TOKENS adds about 50 s
# of writing: 124 s, inside ANSWER_SECONDS with a third of it to spare.
PROMPT_BUDGET = 3000

# Within that budget: the brief (what is on this card) keeps most of it, and a guide
# question brings **one** page, a few sentences of it.  Three whole pages cost 128 s
# of reading for a single answer, measured on the box.
BRIEF_BUDGET = 1400
GUIDE_PAGES = 1
GUIDE_EXCERPT = 520

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
      "poison", "splint", "fracture", "shock", "cut", "gash", "injur", "hurt",
      "sprain", "bandage", "antiseptic", "sick", "vomit", "diarrh", "rash", "tooth",
      "toilet", "sanitation", "water", "purif", "filter", "boil",
      "fire", "shelter", "warm", "cold", "power", "battery", "solar", "generator",
      "leak", "pipe", "tap", "waste", "food", "store",
      "seed", "plant", "garden", "harvest", "pest", "repair", "glue", "wire",
      "fuse", "roof", "mould", "mold", "insect", "rat", "snake", "dog",
      "fix", "broken", "mend", "crack", "stuck", "squeak", "loose", "hinge")),
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
STOPWORDS = {"about", "after", "again", "against", "all", "also", "and", "any", "are",
             "ask", "being", "but", "can", "could", "did", "does", "doing", "every",
             "first", "for", "from", "get", "got", "had", "has", "have", "help", "her",
             "here", "him", "his", "how", "into", "its", "just", "know", "let", "like",
             "make", "may", "might", "much", "must", "need", "new", "not", "now", "off",
             "old", "one", "only", "other", "our", "out", "own", "per", "put", "say",
             "see", "she", "should", "some", "that", "their", "them", "then", "there",
             "these", "they", "thing", "this", "those", "too", "two", "use", "used",
             "want", "was", "way", "were", "what", "when", "where", "which", "while",
             "who", "why", "with", "would", "yet", "you", "your"}

# A question's words, and what each match is worth.  Three letters is enough ("cut",
# "tap", "gas"), and a word in the page's title counts for far more than one in its
# body — see ``_score``.
MIN_WORD = 3
TITLE_WEIGHT = 4

# A word a person uses, and the page on this card that answers it.  This is a
# preference, never a filter: a hinted page that is not on the card simply is not
# preferred, and every other page is still ranked as before.  It exists because a tie
# between two pages that both mention "cut" was decided by the alphabet, and the model
# handed the wrong page invents one instead of answering — measured on the box,
# 26 Oct 2026, on "what do I do about a deep cut".
TOPIC_HINTS = (
    (("cut", "cuts", "gash", "bleed", "bleeding", "wound", "wounds", "bandage",
      "burn", "burns", "choke", "cpr", "sprain", "injur", "hurt"), "first-aid"),
    (("water", "boil", "purif", "filter", "thirst"), "water"),
    (("food", "fridge", "freezer", "spoil", "spills", "store"), "food"),
    (("fire", "smoke", "flame", "flames", "extinguish", "chimney"), "fire"),
    (("power", "electric", "electricity", "battery", "batteries", "fuse", "generator",
      "solar"), "power"),
    (("toilet", "flush", "waste", "sanitation", "sewage", "smell"), "sanitation"),
    (("cold", "warm", "warmth", "heat", "heating", "shelter", "blanket", "tent"),
     "shelter"),
    (("leak", "leaking", "pipe", "pipes", "tap", "drip", "dripping"), "plumbing"),
)


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


def _score(question: str, text: str, title: str = "") -> int:
    """How strongly one page answers one question.  ``0`` is "not at all".

    A three-letter word counts — "cut", "tap", "gas" are how a person asks — and a
    word in the page's **title** is worth far more than one buried in its body: the
    page called *First aid* answers a question about a cut even when the plumbing page
    is the one that mentions cutting a pipe.  Measured on the box, 26 Oct 2026: without
    the title weight, "how do I fix a leaking tap" chose the food page.
    """
    words = {w for w in re.findall(r"[a-z]{%d,}" % MIN_WORD, (question or "").lower())
             if w not in STOPWORDS}
    lowered = text.lower()
    head = (title or "").lower()
    return sum(TITLE_WEIGHT if w in head else (1 if w in lowered else 0) for w in words)


def _sentences(text: str, wanted: int = 3, limit: int = 900) -> str:
    """The first few sentences of a page — enough to point at, short enough to fit."""
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 30]
    return " ".join(parts[:wanted])[:limit]


def _hinted(question: str) -> list:
    """The pages a plain word in the question points at, best first.

    Only pages that are really on the card, and only ever a *preference*: the ranking
    below still puts every other page after them by score, so a hint can never hide
    the right page — it can only break a tie the alphabet would otherwise decide.
    """
    text = " " + re.sub(r"[^a-z0-9]+", " ", (question or "").lower()) + " "
    wanted = [stem for words, stem in TOPIC_HINTS if any(f" {w} " in text for w in words)]
    if not wanted:
        return []
    found = []
    for stem in wanted:
        for _title, name, _text in pages():
            if stem in name and name not in found:
                found.append(name)
    return found


def context_for(question: str, kind: str) -> tuple:
    """``(context text, pages used)`` — what the model is allowed to read.

    Always the brief (what is on this card), and for a guide question the page whose
    words overlap the question's words most.  **One** page, a few sentences of it:
    reading is the slow half on this box, and three whole pages were 128 s of it.
    Nothing personal is ever included: the household's own answers are never part of
    an answer a visitor can ask for.
    """
    chunks, used = [], []
    try:
        brief = brief_path().read_text(encoding="utf-8")
    except OSError:
        brief = ""
    if brief:
        chunks.append("What is on this box:\n" + brief.strip()[:BRIEF_BUDGET])
    if kind == "guide":
        scored = []
        for title, name, text in pages():
            score = _score(question, text, title)
            if score:
                scored.append((score, title, name, text))
        # by score, then by name — but a page a plain word in the question points at
        # comes first: a tie is otherwise a coin toss that depends on the alphabet, and
        # it once handed a question about a cut the household-repairs page.
        hinted = _hinted(question)
        scored.sort(key=lambda row: (0 if row[2] in hinted else 1, -row[0], row[2]))
        for score, title, name, text in scored[:GUIDE_PAGES]:
            used.append({"title": title, "name": name})
            chunks.append("From the page called “" + title + "” (" + name + "):\n"
                          + _sentences(text, limit=GUIDE_EXCERPT))
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
# The slot is a **lease**, not a lock: it names whoever holds it and the moment they
# took it, so a question whose reader has gone can never leave the box saying
# "somebody else is asking" for ever.  A bare `threading.Lock` did exactly that
# (defect, log §32) — the abandoned question held it for as long as the engine kept
# writing a full answer, and a wedged one held it until the service was restarted,
# because nothing ever gave it back.
LEASE_SECONDS = ANSWER_SECONDS + 60

_lease = threading.Lock()                 # guards the two lines below
_holder = {"token": 0, "since": 0.0, "kind": ""}
_last_token = 0

_live_lock = threading.Lock()
_live = {}                                # token -> the engine's response, so it can be unhooked

BUSY_SENTENCE = ("Somebody else is asking the helper right now. Try again in a minute — "
                 "everything else on the box still works while you wait.")
SORRY_SENTENCE = ("The helper could not put an answer together. Everything else on the box "
                  "still works; the guides and the library are the place to look.")


def _now() -> float:
    return time.monotonic()


def busy_sentence(seconds: int = 0) -> str:
    """One plain sentence about the wait, with the number when the box knows it."""
    if 0 < seconds < 90:
        return ("Somebody else is asking the helper right now. Try again in about "
                + str(int(seconds)) + " seconds — everything else on the box still "
                "works while you wait.")
    return BUSY_SENTENCE


def begin(kind: str = "answer"):
    """Take the one slot: ``(token, 0)`` — or ``(0, seconds_left)`` if it is taken.

    A lease that has outlived its own ceiling is **taken back** rather than honoured
    for ever: the answer it belonged to is not going to be collected by anybody, and a
    helper that refuses for the rest of its life is worse than one that says so.
    """
    global _last_token
    with _lease:
        held = _holder["token"]
        if held:
            left = LEASE_SECONDS - (_now() - _holder["since"])
            if left > 0:
                return (0, max(1, int(round(left))))
            print("ark-ai: took the slot back from an answer that outlived "
                  + str(int(LEASE_SECONDS)) + " seconds", flush=True)
        _last_token += 1
        token = _last_token
        _holder.update(token=token, since=_now(), kind=kind)
    cancel(held)                          # stop the answer nobody is collecting
    return (token, 0)


def end(token) -> bool:
    """Give the slot back — only if it is still ours.

    A late answer must never free the slot of the question that came after it.
    """
    with _lease:
        if token and _holder["token"] == token:
            _holder.update(token=0, since=0.0, kind="")
            return True
        return False


def busy_for() -> int:
    """Seconds the next person would wait; ``0`` when nobody is asking at all."""
    with _lease:
        if not _holder["token"]:
            return 0
        left = LEASE_SECONDS - (_now() - _holder["since"])
    return max(0, int(round(left)))


def busy() -> bool:
    """True while another question is really being answered."""
    return busy_for() > 0


def _hook(token, response) -> None:
    with _live_lock:
        _live[token] = response


def _unhook(token):
    with _live_lock:
        return _live.pop(token, None)


def cancel(token) -> bool:
    """The person who asked has gone: unhook the engine and free the slot at once.

    Closing the response is what interrupts a read that is already waiting inside a
    worker thread, and dropping the connection is how the engine is told to stop
    writing — one of the four cores stops burning on words nobody will read.
    """
    response = _unhook(token)
    if response is not None:
        try:
            response.close()
        except Exception:                 # a socket that is already gone needs no closing
            pass
    end(token)
    return response is not None


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
    token, left = begin("answer")
    if not token:
        return ("", busy_sentence(left))
    try:
        return _answer_once(token, question)
    finally:
        end(token)


def _answer_once(token, question: str) -> tuple:
    """One whole answer, with the slot already held (and given back by the caller)."""
    try:
        with _request(question, stream=False) as resp:
            _hook(token, resp)
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
        _unhook(token)


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


def stream(question: str):
    """``(token, pieces)`` — the answer as it is written.

    The slot is taken **now**, before the first byte, so the page can say *wait*
    instead of showing an empty box; ``cancel(token)`` is what the page calls when the
    person in front of it goes away.  ``token`` is ``0`` for an answer that never needed
    the slot (a refusal), and cancelling that is a no-op.
    """
    question, problem = clean_question(question)
    if problem:
        return (0, iter((problem,)))
    reason = not_ready_reason()
    if reason:
        return (0, iter((NOT_READY[reason][0] + ". " + NOT_READY[reason][1],)))
    token, left = begin("stream")
    if not token:
        return (0, iter((busy_sentence(left),)))
    return (token, _pieces(token, question))


def _pieces(token, question: str):
    """The words one at a time, from a slot this generator gives back."""
    written = 0
    try:
        with _request(question, stream=True) as resp:
            _hook(token, resp)
            for raw in resp:
                piece = _delta(raw)
                if not piece:
                    continue
                written += len(piece)
                if written > MAX_ANSWER:
                    break
                yield piece
    except Exception as exc:
        # Broad on purpose.  A response closed by `cancel()` from another thread is the
        # ordinary way this ends when a person walks away, and it arrives as whichever
        # error the socket library felt like raising.
        print(f"ark-ai: stream stopped ({exc})", flush=True)
        if written:
            yield "\n\n(The helper stopped answering there. What it wrote is above.)"
    finally:
        _unhook(token)
        end(token)
    if not written:
        yield SORRY_SENTENCE


def ask_stream(question: str):
    """Yield an answer as it is written.  Always yields *something*.

    The first thing yielded is either the answer's first words or one plain sentence
    explaining why there are none.  "Somebody else is asking" is decided before the
    first byte, so the page can say *wait* instead of showing an empty box.

    ``cancel`` in the ``finally`` is what makes an abandoned answer harmless: a caller
    that stops reading half way (the page did, because the person closed it) still
    gives the slot back, and stops the engine writing to nobody.
    """
    token, pieces = stream(question)
    try:
        for piece in pieces:
            yield piece
    finally:
        cancel(token)


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
    # No instruction goes in the brief.  The rules live in ``HOUSE_RULES``, and a second
    # copy here came straight back out of a small model's mouth as narration — the first
    # real answer on the box (26 Oct 2026) began by reciting "When a question is about a
    # person's own house, say which page on the box answers it".  The brief is facts.
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

