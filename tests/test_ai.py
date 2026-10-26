"""Ask the Ark — a question in a person's own words, answered on this box.

The promise these tests pin is narrow on purpose:

* the page answers **honestly when there is no engine and no model** — a helper
  that is not fitted must never look like a helper that is thinking;
* a question is **escaped** wherever it is echoed and **refused with the limit
  named**, never silently cut;
* the answer is made from what is on the card (the brief, the guide pages) and
  **never** from the household's own answers;
* an answer that is being written arrives a piece at a time through
  ``/api/ask/stream``, and one answer is written at a time;
* the helper is reported on the owner's page and **never blocks readiness**.

A loopback ``http.server`` stands in for the engine, so "answering" and "refused"
are two states a test can produce — the same discipline ``test_health.py`` uses,
because a helper that cannot be tested down is a helper nobody can trust up.
"""

from __future__ import annotations

import http.server
import json
import os
import pathlib
import stat
import subprocess
import threading
import time


# ---------------------------------------------------------------------------
#  a stand-in for the language engine
# ---------------------------------------------------------------------------
ANSWER_WORDS = ("Boiling water hard ", "for one whole minute ", "makes it safe to drink.")


class _Engine(http.server.BaseHTTPRequestHandler):
    """Answers ``/health``, and answers a question in one piece or many."""

    def do_GET(self):                                   # noqa: N802 — stdlib name
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                  # noqa: N802 — stdlib name
        length = int(self.headers.get("Content-Length") or 0)
        asked = json.loads(self.rfile.read(length) or b"{}")
        if asked.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ANSWER_WORDS:
                chunk = json.dumps({"choices": [{"delta": {"content": piece}}]})
                self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        body = json.dumps({"choices": [{"message": {"content": "".join(ANSWER_WORDS)}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):                       # tests should stay quiet
        pass


class Engine:
    """A loopback engine that answers while the test is inside the ``with``."""

    def __init__(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

def _fit(ark, monkeypatch, engine=None):
    """Put an engine and a model on the throwaway box, as phase 58-ai would."""
    ai = ark.root / "ai"
    (ai / "models").mkdir(parents=True, exist_ok=True)
    (ai / "llama-server").write_bytes(b"#!/bin/sh\n")
    (ai / "models" / "tiny.gguf").write_bytes(b"GGUF" + b"\0" * 64)
    monkeypatch.setenv("ARK_AI_URL", engine.base if engine else "http://127.0.0.1:9")
    return ai




# ---------------------------------------------------------------------------
#  no engine, no model — and the page says exactly that
# ---------------------------------------------------------------------------
def test_a_helper_that_is_not_fitted_says_so_and_offers_the_form(public):
    page = public.client.get("/ask")
    assert page.status_code == 200
    assert "The helper is not fitted on this box" in page.text
    assert "Nothing else has changed" in page.text
    assert 'id="ask-form"' in page.text
    assert 'action="/ask"' in page.text
    # nothing was answered, so there is no answer panel in the served page
    assert 'id="ask-answer"' not in page.text


def test_a_question_with_no_helper_behind_it_is_not_answered(public):
    page = public.client.get("/ask", params={"q": "how do I store water"})
    assert page.status_code == 200
    assert "The helper is not fitted on this box" in page.text
    assert "makes it safe to drink" not in page.text


def test_a_fitted_helper_that_is_not_answering_says_that_instead(public, monkeypatch):
    """The engine file and the words are there; nothing is listening on the address."""
    _fit(public.ark, monkeypatch, engine=None)
    page = public.client.get("/ask", params={"q": "how do I store water"})
    assert "The helper is not answering right now" in page.text


# ---------------------------------------------------------------------------
#  a question is a person's words: escaped, bounded, never silently cut
# ---------------------------------------------------------------------------
def test_a_question_is_escaped_wherever_it_is_echoed(public):
    asked = "<script>alert(1)</script>"
    page = public.client.get("/ask", params={"q": asked})
    assert page.status_code == 200
    assert "<script>alert(1)" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text


def test_a_question_that_is_too_long_is_refused_with_the_limit_named(public):
    import ark_ai

    page = public.client.get("/ask", params={"q": "x" * (ark_ai.MAX_QUESTION + 1)})
    assert f"under {ark_ai.MAX_QUESTION} characters" in page.text
    assert 'maxlength="%d"' % ark_ai.MAX_QUESTION in page.text


def test_the_cleaner_trims_and_names_the_limit_rather_than_cutting():
    import ark_ai

    assert ark_ai.clean_question("  how do I  store\nwater  ") == ("how do I store water", "")
    assert ark_ai.clean_question("") == ("", "Type a question first.")
    question, problem = ark_ai.clean_question("y" * (ark_ai.MAX_QUESTION + 5))
    assert question == "", "what a person typed is never silently shortened"
    assert str(ark_ai.MAX_QUESTION) in problem


def test_an_answer_is_escaped_and_broken_into_paragraphs(public):
    markup = public.app._ask_paragraphs("<b>raw</b> first\n\nsecond & last")
    assert "&lt;b&gt;raw&lt;/b&gt;" in markup
    assert "&amp;" in markup
    assert markup.count("<p>") == 2


# ---------------------------------------------------------------------------
#  what the helper is allowed to read
# ---------------------------------------------------------------------------
def test_a_water_question_is_sent_to_the_guides_and_a_book_question_to_the_library():
    import ark_ai

    assert ark_ai.route("how do I make water safe to drink")[0] == "guide"
    assert ark_ai.route("what books can I read about history")[0] == "library"
    assert ark_ai.route("how are you today") == ark_ai.GENERAL


def test_the_helper_reads_the_pages_and_never_the_households_own_answers(public):
    import ark_ai

    vault = public.ark.root / "vault"
    (vault / "manuals").mkdir(parents=True, exist_ok=True)
    (vault / "manuals" / "water.html").write_text(
        "<html><body><h1>Safe water</h1><p>Boiling water for one whole minute makes it "
        "safe to drink. Let it cool covered before you store it in a clean container."
        "</p></body></html>", encoding="utf-8")
    (vault / "private.html").write_text("<h1>Not a readable page</h1>", encoding="utf-8")

    found = ark_ai.pages()
    assert [name for _title, name, _text in found] == ["manuals/water.html"]
    context, used = ark_ai.context_for("how do I make water safe", "guide")
    assert "Boiling water for one whole minute" in context
    assert used and used[0]["name"] == "manuals/water.html"
    assert "Not a readable page" not in context, "only the manuals and guides are read"


def test_one_question_is_a_prompt_this_box_can_read(public):
    """Reading is the expensive half: about six characters a token, 6.8 tokens a second.

    Measured on the box itself, 26 Oct 2026: 871 tokens of prompt took **128 s** to
    read, so the first real question was answered at 152.6 s — past the page's own
    ceiling, and long past the proxy's 120 s.  Three whole guide pages were most of
    it.  This pins the size of one prompt, so a later "include a bit more" cannot
    quietly make every answer late again.
    """
    import ark_ai

    vault = public.ark.root / "vault"
    (vault / "manuals").mkdir(parents=True, exist_ok=True)
    for n in range(6):
        (vault / "manuals" / f"water-{n}.html").write_text(
            "<html><body><h1>Safe water " + str(n) + "</h1><p>"
            + ("Boiling water for one whole minute makes it safe to drink. " * 60)
            + "</p></body></html>", encoding="utf-8")
    (public.ark.root / "ai").mkdir(parents=True, exist_ok=True)
    (public.ark.root / "ai" / ark_ai.BRIEF_NAME).write_text("brief " * 900, encoding="utf-8")

    conversation = ark_ai.messages("how do I make water safe to drink")
    prompt = sum(len(turn["content"]) for turn in conversation)
    assert prompt <= ark_ai.PROMPT_BUDGET, (
        f"{prompt} characters is about {prompt // ark_ai.CHARS_PER_TOKEN} tokens — "
        f"{prompt / ark_ai.CHARS_PER_TOKEN / ark_ai.READ_TOKENS_PER_SECOND:.0f} s of "
        "reading on this box before the model writes a word")

    # the page that matches best, and only that one: six pages mention water
    context, used = ark_ai.context_for("how do I make water safe to drink", "guide")
    assert len(used) == ark_ai.GUIDE_PAGES == 1
    assert len(context) <= ark_ai.BRIEF_BUDGET + ark_ai.GUIDE_EXCERPT + 200


def test_the_worst_case_answer_fits_in_the_time_the_page_waits():
    """Three numbers that disagreed in the field, held together by arithmetic."""
    import ark_ai

    read_seconds = (ark_ai.PROMPT_BUDGET / ark_ai.CHARS_PER_TOKEN
                    / ark_ai.READ_TOKENS_PER_SECOND)
    write_seconds = ark_ai.MAX_TOKENS / ark_ai.WRITE_TOKENS_PER_SECOND
    assert read_seconds + write_seconds < ark_ai.ANSWER_SECONDS, (
        f"a full prompt plus a full answer is {read_seconds + write_seconds:.0f} s, "
        f"which the page — waiting {ark_ai.ANSWER_SECONDS} s — would give up on")
    assert ark_ai.MAX_TOKENS <= 200, "a longer answer is minutes of writing on a Pi 4"


def test_a_run_of_numbered_steps_is_broken_into_one_line_each(public):
    """The first real answer on the box came back as one unreadable run-on line.

    *"…follow these steps:1. Boil water.2. Filter water through a clean cloth…"* — the
    model wrote the list without line breaks and the page rendered it as a single
    paragraph.  Steps a person cannot read are not an answer (measured on the box,
    26 Oct 2026, log §31).
    """
    markup = public.app._ask_paragraphs(
        "To make water safe to drink, follow these steps:1. Boil water."
        "2. Filter water through a clean cloth.3. Let it cool before drinking.")
    assert markup.count("<p>") == 4
    assert ">1. Boil water.<" in markup
    assert ">3. Let it cool before drinking.<" in markup

    # ordinary prose keeps its shape: a decimal is not a numbered list
    assert public.app._ask_paragraphs("Boil it for 1.5 minutes.").count("<p>") == 1


def test_a_plain_word_like_cut_finds_the_first_aid_page(public):
    """A person says "deep cut", not "wound" — and the box must find the page.

    The first real answer to a first-aid question had **no first-aid page in front of
    it**: "cut" was not one of the routing words, so the question went to the general
    answer and the model invented a page called ``/firstaid/`` (measured on the box,
    26 Oct 2026).  With the old scoring the page titled *First aid* also tied with the
    sanitation page and lost the tie, because ties were broken by name in reverse.
    """
    import ark_ai

    assert ark_ai.route("what do I do about a deep cut")[0] == "guide"

    vault = public.ark.root / "vault"
    (vault / "manuals").mkdir(parents=True, exist_ok=True)
    (vault / "manuals" / "sanitation.html").write_text(
        "<html><body><h1>Sanitation and hygiene</h1><p>Keep waste away from the water. "
        "Wash your hands often.</p></body></html>", encoding="utf-8")
    (vault / "manuals" / "first-aid.html").write_text(
        "<html><body><h1>First aid: the first ten minutes</h1><p>Press firmly on a cut "
        "with a clean cloth and keep pressing.</p></body></html>", encoding="utf-8")

    context, used = ark_ai.context_for("what do I do about a deep cut", "guide")
    assert used[0]["name"] == "manuals/first-aid.html"
    assert "Press firmly on a cut" in context


def test_a_hint_prefers_a_page_and_never_hides_one(public):
    """A preference, not a filter: no first-aid page here, and the answer still comes."""
    import ark_ai

    vault = public.ark.root / "vault"
    (vault / "guides").mkdir(parents=True, exist_ok=True)
    (vault / "guides" / "household-repairs.html").write_text(
        "<html><body><h1>Everyday fixes</h1><p>A cut in a hose can be taped until you "
        "can buy another.</p></body></html>", encoding="utf-8")

    assert ark_ai._hinted("what do I do about a deep cut") == [], (
        "a hint must name only pages that are really on the card")
    context, used = ark_ai.context_for("what do I do about a deep cut", "guide")
    assert used and used[0]["name"] == "guides/household-repairs.html"
    assert "A cut in a hose" in context


def test_a_word_in_a_title_is_worth_more_than_one_in_the_body():
    import ark_ai

    assert ark_ai._score("how do I fix a leaking tap", "a tap", "") > 0, "three letters count"
    assert (ark_ai._score("water", "nothing here", "Water: find it, store it")
            > ark_ai._score("water", "water", "")), "a title says what a page is for"


def test_the_brief_never_lends_the_model_the_households_own_words(public):
    """It is a file on the card, so it says out loud what must never be in an answer."""
    import ark_ai

    wrote = ark_ai.build_brief()
    assert wrote["problem"] == ""
    text = wrote["text"]
    assert "never part of an answer" in text
    assert "Message Board" in text and "File Box" in text
    on_disk = (public.ark.root / "ai" / ark_ai.BRIEF_NAME).read_text(encoding="utf-8")
    assert on_disk == text


def test_the_brief_is_published_by_rename_and_a_dry_run_writes_nothing(public):
    import ark_ai

    wanted = public.ark.root / "ai" / ark_ai.BRIEF_NAME
    ark_ai.build_brief(dry_run=True)
    assert not wanted.exists(), "a dry run must not leave a file on the card"
    ark_ai.build_brief()
    assert wanted.is_file()
    leftovers = [p.name for p in wanted.parent.iterdir() if p.name.endswith(".partial")]
    assert leftovers == [], "a half-written brief must never sit on the card"



# ---------------------------------------------------------------------------
#  an answer, in one piece and as it is written
# ---------------------------------------------------------------------------
def test_a_real_engine_answers_the_page_in_plain_words(public, monkeypatch):
    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        page = public.client.get("/ask", params={"q": "how do I make water safe"})
    assert page.status_code == 200
    assert "Boiling water hard" in page.text
    assert "It can be wrong" in page.text, "an answer must say the guides are the truth"
    assert 'id="ask-answer"' in page.text


def test_the_answer_streams_to_the_page_a_piece_at_a_time(public, monkeypatch):
    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        streamed = public.client.get("/api/ask/stream",
                                     params={"q": "how do I make water safe"})
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/plain")
    assert streamed.headers.get("x-accel-buffering") == "no"
    assert streamed.text == "".join(ANSWER_WORDS), "every piece, in the order written"


def test_the_stream_says_why_there_is_no_answer_rather_than_answering_nothing(public):
    streamed = public.client.get("/api/ask/stream", params={"q": "how do I store water"})
    assert streamed.status_code == 200
    assert "not fitted on this box" in streamed.text


def test_the_switch_closes_both_doors_and_not_only_the_page(public, monkeypatch):
    """Switched off means switched off — the address must not be a way around it.

    The page and the stream are two doors onto the same engine.  A switch that
    only turned away the button would leave a visitor who typed
    ``/api/ask/stream?q=…`` with a working helper, and the owner with a switch
    that does not do what it says.
    """
    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        assert public.client.get("/api/ask/stream", params={"q": "water"}).status_code == 200
        with public.ark.db.tx() as conn:
            public.ark.db.set_setting(conn, "public_ask", "0")
        page = public.client.get("/ask")
        streamed = public.client.get("/api/ask/stream", params={"q": "how do I store water"})
        assert page.status_code == 403
        assert "switched off" in page.text
        assert streamed.status_code == 403
        assert "switched off" in streamed.text
        assert "".join(ANSWER_WORDS) not in streamed.text
        with public.ark.db.tx() as conn:
            public.ark.db.set_setting(conn, "public_ask", "1")
        assert public.client.get("/ask").status_code == 200


def test_one_answer_is_written_at_a_time(public, monkeypatch):
    import ark_ai

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        token, left = ark_ai.begin("test")
        assert token and left == 0, "the slot should have been free"
        try:
            assert ark_ai.busy() is True
            assert ark_ai.ask("how do I store water") == ("", ark_ai.BUSY_SENTENCE)
            assert "".join(ark_ai.ask_stream("how do I store water")) == ark_ai.BUSY_SENTENCE
            page = public.client.get("/ask", params={"q": "how do I store water"})
            assert "Somebody else is asking" in page.text
        finally:
            ark_ai.end(token)
    assert ark_ai.busy() is False


def test_the_slot_is_a_lease_that_names_who_holds_it(public, monkeypatch):
    """A late answer must never free the slot of the question that came after it."""
    import ark_ai

    first, _left = ark_ai.begin("test")
    assert first
    try:
        assert ark_ai.end(first + 1) is False, "a token that is not the holder frees nothing"
        assert ark_ai.busy() is True
        assert ark_ai.end(first) is True
    finally:
        ark_ai.end(first)
    assert ark_ai.busy() is False
    assert ark_ai.end(first) is False, "giving the slot back twice is not an error"


def test_the_wait_is_named_in_seconds_and_not_just_in_minutes(public, monkeypatch):
    """A person told *wait* deserves to be told how long — and it is a real figure."""
    import ark_ai

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        token, _left = ark_ai.begin("test")
        try:
            with ark_ai._lease:
                ark_ai._holder["since"] = time.monotonic() - (ark_ai.LEASE_SECONDS - 40)
            assert 34 <= ark_ai.busy_for() <= 46
            problem = ark_ai.ask("how do I store water")[1]
            assert problem.startswith("Somebody else is asking")
            assert "in about" in problem and "seconds" in problem
            assert ark_ai.busy_sentence(600) == ark_ai.BUSY_SENTENCE
        finally:
            ark_ai.end(token)


def test_a_slot_left_behind_is_taken_back_rather_than_refusing_for_ever(public, monkeypatch,
                                                                      capsys):
    """The half of §32 that no amount of politeness can fix on its own.

    A question that outlives its own ceiling is not going to be collected by anybody.
    The next person must be answered, not told to come back for ever — and the journal
    must say what happened, because a takeover that leaves no trace is a takeover nobody
    can diagnose a month later.
    """
    import ark_ai

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        token, _left = ark_ai.begin("test")
        with ark_ai._lease:
            ark_ai._holder["since"] -= ark_ai.LEASE_SECONDS + 1
        answer, problem = ark_ai.ask("how do I store water")
        assert problem == ""
        assert answer == "".join(ANSWER_WORDS)
        assert ark_ai.busy() is False
        assert "took the slot back" in capsys.readouterr().out
        assert ark_ai.end(token) is False, "the slot it took over is somebody else's now"


def test_the_engine_is_unhooked_from_the_moment_the_reader_goes(public, monkeypatch):
    """Cancelling closes the engine's response — that is what stops the reading.

    A flag alone cannot do it: the worker thread is already blocked inside the engine's
    socket, so the connection is what has to go.  Closing it is also how the engine is
    told to stop writing, which gives one of four cores back.
    """
    import ark_ai

    closed = []

    class SlowEngine:
        def __iter__(self):
            return self

        def __next__(self):
            chunk = json.dumps({"choices": [{"delta": {"content": "Half "}}]})
            return f"data: {chunk}\n".encode("utf-8")

        def close(self):
            closed.append(True)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        monkeypatch.setattr(ark_ai, "_request", lambda *_a, **_k: SlowEngine())
        token, pieces = ark_ai.stream("how do I store water")
        try:
            assert next(pieces) == "Half "
            assert ark_ai.busy() is True
            assert ark_ai.cancel(token) is True
            assert closed == [True], "the engine must be told the reader has gone"
            assert ark_ai.busy() is False, "and the next question must not be refused"
            newer, _left = ark_ai.begin("test")
            try:
                pieces.close()
                assert closed == [True]
                assert ark_ai.busy() is True, "an old answer must not free a new slot"
            finally:
                ark_ai.end(newer)
        finally:
            pieces.close()
    assert ark_ai._live == {}, "nothing may be left hooked to the engine"


def test_an_engine_that_dies_mid_answer_keeps_what_it_wrote(public, monkeypatch):
    """The words already on the screen are not thrown away when the engine stops.

    A socket that closes early does not always raise, so the failure is produced
    where it really lives: the engine's line reader.  What matters is that the
    half answer survives and the page is told, in plain words, that it stopped.
    """
    import contextlib

    import ark_ai

    @contextlib.contextmanager
    def half_then_gone(_question, stream=False):        # noqa: ARG001 — same shape
        def lines():
            chunk = json.dumps({"choices": [{"delta": {"content": "Half an answer"}}]})
            yield f"data: {chunk}\n".encode("utf-8")
            raise OSError("the engine went away")
        yield lines()

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        monkeypatch.setattr(ark_ai, "_request", half_then_gone)
        written = "".join(ark_ai.ask_stream("how do I store water"))
        page = public.client.get("/api/ask/stream", params={"q": "how do I store water"})
    assert written.startswith("Half an answer")
    assert "stopped answering" in written
    assert page.status_code == 200, "an engine that dies must never 500 the page"
    assert page.text.startswith("The helper could not put an answer together") or \
        "Half an answer" in page.text


def test_a_truncated_answer_is_one_honest_sentence_and_not_a_broken_page(public, monkeypatch):
    """An engine killed while writing sends a short body under a long promise."""
    class Truncated(_Engine):
        def do_POST(self):                              # noqa: N802 — stdlib name
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "4000")   # more was promised
            self.end_headers()
            self.wfile.write(b'{"choices":')
            self.wfile.flush()
            self.close_connection = True

    class TruncatedEngine(Engine):
        def __init__(self):
            Engine.__init__(self)
            self.server.RequestHandlerClass = Truncated

    with TruncatedEngine() as engine:
        _fit(public.ark, monkeypatch, engine)
        page = public.client.get("/ask", params={"q": "how do I store water"})
    assert page.status_code == 200, "a helper that breaks must never break the page"
    assert "The helper could not put an answer together" in page.text


# ---------------------------------------------------------------------------
#  the owner's side, and the one thing it must never do
# ---------------------------------------------------------------------------
def test_the_page_gives_the_slot_back_the_moment_its_reader_goes(public, monkeypatch):
    """The route, driven the way Starlette drives it: read one piece, then a close.

    This is the whole defect in one test.  Before the fix, the client going away left
    the slot taken until the engine finished writing words nobody would read; now the
    close reaches the ``finally`` and the next person is answered straight away.
    """
    import anyio

    import ark_ai
    import main as portal

    class NobodyThere:
        async def is_disconnected(self):
            return False

    async def read_one_then_close():
        response = await portal.ask_stream(NobodyThere(), q="how do I store water")
        body = response.body_iterator
        first = await body.__anext__()
        assert ark_ai.busy() is True, "the slot is held while the words are being written"
        await body.aclose()                     # what Starlette does when the tab closes
        return first

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        assert anyio.run(read_one_then_close) == ANSWER_WORDS[0]
        assert ark_ai.busy() is False, "a reader who has gone must not block the next question"


def test_nothing_is_left_hooked_or_held_after_an_answer(public, monkeypatch):
    import ark_ai

    with Engine() as engine:
        _fit(public.ark, monkeypatch, engine)
        assert "".join(ark_ai.ask_stream("how do I store water")) == "".join(ANSWER_WORDS)
        assert ark_ai._live == {}
        assert ark_ai.busy() is False
        assert public.client.get("/ask", params={"q": "how do I store water"}).status_code == 200
        assert ark_ai._live == {}, "the plain page must not leave a response hooked"
        assert ark_ai.busy() is False, "and must give the slot back too"


def test_the_page_asks_one_question_and_never_a_second_behind_the_first(public):
    """The other half of §32: the page's own way of asking twice.

    A browser that cannot take a stream never got an answer from the script, so the
    script handed the question to the form *after* the first question had already left —
    two questions at once, and the second one told to wait.  Streaming is checked before
    the form is taken over now, and a failure is a sentence rather than another question.
    """
    page = public.client.get("/ask").text
    assert "'body' in window.Response.prototype" in page, \
        "the script must know it can stream before it takes the form over"
    assert "form.submit()" not in page, "a failed stream must never send a second question"
    assert "asking = false" in page, "one question at a time, in the page too"


def test_the_helper_is_reported_on_the_owner_page_and_never_blocks_readiness(public):
    import ark_health

    body = public.client.get("/api/ready").json()
    row = next(s for s in body["sections"] if s["key"] == "ask")
    assert row["label"] == "Ask the Ark"
    assert row["ready"] is False, "nothing is fitted, so the row is honest"
    assert "not fitted" in row["reason"]
    assert "58-ai" in row["action"], "a gap always comes with the next step"
    assert row["content"] is False, (
        "nothing is fitted, so there is nothing on the card to serve yet — a box with "
        "no helper is 'still filling up', not 'broken' (the gate reads content: "
        "90-verify failed a healthy card over this, 26 Oct 2026)")
    assert "ask" not in ark_health.BLOCKING_SECTIONS, (
        "a box with no helper is a whole box: the helper must never block readiness")
    assert "Ask the Ark" not in (body.get("blocking") or [])


def test_a_model_that_is_here_but_silent_is_the_one_case_worth_a_failure(monkeypatch):
    """Words on the card that do not answer are content the box cannot serve."""
    import ark_ai
    import ark_health

    monkeypatch.setattr(ark_ai, "status", lambda: {
        "engine": True, "model": "good-words.gguf", "model_bytes": 4096,
        "answering": False, "available": False,
        "address": "http://127.0.0.1:8089", "max_question": 500})

    row = ark_health.ask_section()
    assert row["ready"] is False
    assert row["content"] is True, "there are words here and they are not answering"
    assert "not answering" in row["reason"]


def test_the_status_endpoint_answers_in_one_sentence(public):
    status = public.client.get("/api/ask/status").json()
    assert status["available"] is False
    assert status["sentence"] == "The helper is not fitted on this box"
    assert status["max_question"] > 0
    assert status["advice"]


# ---------------------------------------------------------------------------
#  the two console tools, and the phase that installs them
#
#  The page can only be as honest as the files it reads.  These tests drive the
#  shipped scripts the way an owner does — a model copied off a stick, a
#  fingerprint that does not match, no model at all — because "the fetch tool
#  checks a fingerprint" is the kind of claim that quietly stops being true.
# ---------------------------------------------------------------------------
SRC = pathlib.Path(__file__).resolve().parents[1] / "pi"
FETCH = SRC / "bin" / "ark-ai-model-fetch"
SERVE = SRC / "bin" / "ark-ai-serve"
PHASE = SRC / "steps" / "58-ai.sh"
UNIT = SRC / "systemd" / "ark-ai.service"


def _tool(tool, *args, env=None, timeout=60):
    return subprocess.run(
        ["bash", str(tool), *[str(a) for a in args]],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def _models_dir(tmp_path):
    folder = tmp_path / "ai" / "models"
    folder.mkdir(parents=True)
    return folder


def _a_model(tmp_path, name="tiny.gguf", words=b"not really a model, but it is a file"):
    stick = tmp_path / "stick"
    stick.mkdir(exist_ok=True)
    there = stick / name
    there.write_bytes(words)
    return there


def test_the_fetch_tool_says_how_to_fit_one_to_an_empty_box(tmp_path):
    """No model is a state, not a fault — and the two ways out are both named."""
    models = _models_dir(tmp_path)
    done = _tool(FETCH, "--status", env={"ARK_AI_MODELS": str(models)})
    assert done.returncode == 1, "an empty folder is not a success"
    assert "No model on the card yet" in done.stdout
    assert "--from /media/stick/model.gguf" in done.stdout, (
        "the box in a house has no internet; carrying one in is the normal way")


def test_a_model_is_copied_in_from_a_stick_and_nothing_is_left_half_arrived(tmp_path):
    models = _models_dir(tmp_path)
    there = _a_model(tmp_path)
    done = _tool(FETCH, "--from", there, env={"ARK_AI_MODELS": str(models)})
    assert done.returncode == 0, done.stderr
    kept = models / "tiny.gguf"
    assert kept.read_bytes() == there.read_bytes()
    assert stat.S_IMODE(kept.stat().st_mode) == 0o644, "the service must be able to open it"
    assert not list(models.glob(".*part")), "a finished copy leaves no part file behind"
    assert "Kept tiny.gguf" in done.stdout


def test_a_fingerprint_that_does_not_match_keeps_nothing_at_all(tmp_path):
    """A checksum is only worth having if a wrong file is thrown away, not renamed."""
    models = _models_dir(tmp_path)
    there = _a_model(tmp_path)
    done = _tool(FETCH, "--from", there, "--sha256", "0" * 64,
                 env={"ARK_AI_MODELS": str(models)})
    assert done.returncode != 0
    assert "not the one asked for" in done.stderr
    assert not list(models.glob("*.gguf")), "a refused file must not end up on the card"
    assert not list(models.glob(".*part"))


def test_the_fingerprint_is_compared_without_caring_about_case(tmp_path):
    """A fingerprint copied out of a browser is often upper case; it is the same file."""
    import hashlib

    models = _models_dir(tmp_path)
    there = _a_model(tmp_path)
    digest = hashlib.sha256(there.read_bytes()).hexdigest().upper()
    done = _tool(FETCH, "--from", there, "--sha256", digest,
                 env={"ARK_AI_MODELS": str(models)})
    assert done.returncode == 0, done.stderr
    assert (models / "tiny.gguf").exists()


def test_a_download_still_arriving_is_invisible_to_the_helper(tmp_path):
    """The part file is dot-named for a reason: half a model must never answer."""
    models = _models_dir(tmp_path)
    (models / ".big.gguf.part").write_bytes(b"half")
    (models / "whole.gguf").write_bytes(b"whole")
    listed = _tool(FETCH, "--list", env={"ARK_AI_MODELS": str(models)})
    status = _tool(FETCH, "--status", env={"ARK_AI_MODELS": str(models)})
    assert "whole.gguf" in listed.stdout
    assert "big" not in listed.stdout
    assert "whole.gguf" in status.stdout
    assert "there are 2" not in status.stdout, "a part file is not a second model"


def test_the_service_refuses_to_run_with_no_engine_instead_of_restarting_for_ever(tmp_path):
    models = _models_dir(tmp_path)
    (models / "tiny.gguf").write_bytes(b"words")
    done = _tool(SERVE, env={"ARK_AI_MODELS": str(models),
                             "ARK_AI_ENGINE": str(tmp_path / "no-such-engine")})
    assert done.returncode == 78, "EX_CONFIG: the unit must stop, not loop"
    assert "58-ai" in done.stdout, "the way to fix it is named"


def test_the_engine_is_started_on_the_loopback_address_with_one_slot(tmp_path):
    """Nothing a visitor can reach, one answer at a time, and the newest model wins."""
    models = _models_dir(tmp_path)
    older = models / "old.gguf"
    older.write_bytes(b"old")
    newer = models / "new.gguf"
    newer.write_bytes(b"new")
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    engine = tmp_path / "fake-engine"
    recorded = tmp_path / "args.txt"
    engine.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {recorded}\n')
    engine.chmod(0o755)
    done = _tool(SERVE, env={"ARK_AI_MODELS": str(models), "ARK_AI_ENGINE": str(engine),
                             "ARK_AI_PORT": "18089", "ARK_AI_THREADS": "2"})
    assert done.returncode == 0, done.stderr
    lines = recorded.read_text().split()
    assert lines[:2] == ["--model", str(newer)], "the newest model is the one used"
    assert "--host" in lines and "127.0.0.1" in lines, "loopback only, never 0.0.0.0"
    assert "--parallel" in lines and lines[lines.index("--parallel") + 1] == "1"
    assert "18089" in lines


def test_the_helper_waits_for_words_and_starts_by_itself_when_they_arrive(tmp_path):
    """A model dropped on the card while the box is up must not need a person."""
    models = _models_dir(tmp_path)
    engine = tmp_path / "fake-engine"
    recorded = tmp_path / "args.txt"
    engine.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {recorded}\n')
    engine.chmod(0o755)
    started = subprocess.Popen(
        ["bash", str(SERVE)],
        env={**os.environ, "ARK_AI_MODELS": str(models), "ARK_AI_ENGINE": str(engine),
             "ARK_AI_POLL": "1", "ARK_AI_PORT": "18090"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        time.sleep(0.5)
        assert not recorded.exists(), "it must wait, not start on nothing"
        (models / "arrived.gguf").write_bytes(b"words")
        deadline = time.time() + 20
        while time.time() < deadline and not recorded.exists():
            time.sleep(0.2)
        assert recorded.exists(), "a model that lands must be picked up on the next look"
        assert "arrived.gguf" in recorded.read_text()
    finally:
        started.terminate()
        out = started.communicate(timeout=10)[0]
    assert "no model yet" in out, "waiting is said out loud, never left to be guessed"


def test_the_phase_installs_the_helper_and_the_brief_that_feeds_it():
    """A fresh box has no engine and no model: phase 58 must still finish."""
    done = subprocess.run(["bash", "-n", str(PHASE)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    text = PHASE.read_text(encoding="utf-8")
    assert "ark_install_unit ark-ai.service" in text
    assert "ark-ai-model-fetch" in text and "ark-build-ai-brief" in text
    assert "systemctl enable ark-ai.service" in text, "enabled, so a cold boot is turnkey"
    installer = (SRC / "install-ark.sh").read_text(encoding="utf-8")
    assert "58-ai" in installer, "a phase nothing runs is a phase that does not exist"


def test_the_unit_stops_cleanly_on_a_box_with_no_engine_and_cannot_grow_without_limit():
    text = UNIT.read_text(encoding="utf-8")
    assert "ConditionPathExists=" in text and "llama-server" in text
    assert "ConditionPathIsDirectory=" in text
    assert "MemoryMax=" in text, "the box has 8 GB and other services to keep alive"
    assert "Nice=" in text and "CPUWeight=" in text
    assert "NoNewPrivileges=" in text
    assert "WantedBy=multi-user.target" in text, "enabled, so a cold boot is turnkey"


