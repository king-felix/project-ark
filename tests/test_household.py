"""Stage 7 — the household page, and the pack a household can carry away.

Two different promises, in two files of tests (this one and ``test_essentials.py``):

* the household page is what somebody reads when they are frightened, so it holds
  what a person typed and nothing else: trimmed, never shortened, no invented
  answer, and a corrupt row is an *empty* page rather than a broken one;
* what a household writes down travels with the records, so it lives in one JSON
  row in ``settings`` and comes back out of a backup with everything else.

The page's own rules are tested here, not the rendering: the renderer is proved in
``test_household_page.py`` where there is a browser-shaped client to talk to.
"""

from __future__ import annotations

import json

import pytest

KEY = "household"


@pytest.fixture()
def house(ark):
    """The module, imported *after* the throwaway box exists (it binds ``ark_db``)."""
    import ark_household
    return ark_household


def _raw(key: str = KEY) -> str:
    import ark_db
    with ark_db.read() as conn:
        return ark_db.get_setting(conn, key, "")


def _put(value: str, key: str = KEY) -> None:
    import ark_db
    with ark_db.tx() as conn:
        ark_db.set_setting(conn, key, value)


# ---------------------------------------------------------------------------
#  what a page holds
# ---------------------------------------------------------------------------
def test_every_entry_has_a_label_a_hint_and_a_limit(house):
    seen = set()
    for key, label, hint, limit, lines in house.ENTRIES:
        assert key not in seen, f"{key} appears twice"
        seen.add(key)
        assert label and label[0].isupper(), label
        assert hint.endswith("."), hint
        assert 20 <= limit <= 4000, (key, limit)
        assert isinstance(lines, bool)
    assert house.KEY == KEY


def test_a_blank_page_is_blank_and_a_fresh_box_answers_nothing(ark, house):
    values = house.load()
    assert values == house.blank()
    assert house.updated() == ""
    assert house.filled(values) == []
    assert len(house.missing(values)) == len(house.ENTRIES)
    assert house.progress(values) == (0, len(house.ENTRIES))


def test_an_answer_is_trimmed_and_control_characters_are_dropped(house):
    kept = house.clean({"water": "  under the kitchen sink\x07  ", "people": "Ada\nand Bo"})
    assert kept["water"] == "under the kitchen sink"
    # A one-line entry is one line: a stray newline becomes a space.
    assert kept["people"] == "Ada and Bo"


def test_anything_else_keeps_its_lines_and_loses_a_wall_of_blanks(house):
    kept = house.clean({"notes": "first\n\n\n\nsecond\r\nthird"})
    assert kept["notes"] == "first\n\nsecond\nthird"


def test_an_answer_too_long_is_refused_with_the_limit_named_never_cut(ark, house):
    limit = dict((key, size) for key, _l, _h, size, _lines in house.ENTRIES)["power"]
    with pytest.raises(house.TooLong) as refused:
        house.clean({"power": "x" * (limit + 1)})
    assert "Where the power stops" in str(refused.value)
    assert str(limit) in str(refused.value)
    # ...and nothing was written, so the page still says what it said before.
    assert house.load()["power"] == ""


def test_an_answer_exactly_at_the_limit_is_kept(house):
    limit = dict((key, size) for key, _l, _h, size, _lines in house.ENTRIES)["meeting"]
    assert len(house.clean({"meeting": "y" * limit})["meeting"]) == limit


def test_a_key_nobody_asks_for_is_dropped_and_a_none_is_blank(house):
    kept = house.clean({"password": "hunter2", "water": None})
    assert "password" not in kept
    assert kept["water"] == ""
    assert set(kept) == {key for key, *_ in house.ENTRIES}


# ---------------------------------------------------------------------------
#  storing it: one row, so a backup carries it
# ---------------------------------------------------------------------------
def test_a_page_is_saved_as_one_row_and_read_back(ark, house):
    written = house.save({"people": "Ada and Bo", "contact": "Ring Nia: 555 0101"},
                         who="admin")
    assert house.load() == written
    stored = json.loads(_raw())
    assert stored["values"]["people"] == "Ada and Bo"
    assert stored["by"] == "admin"
    assert stored["updated"]
    assert house.updated() == stored["updated"]
    assert house.progress(house.load()) == (2, len(house.ENTRIES))


def test_the_page_keeps_a_name_in_another_script(ark, house):
    house.save({"people": "Ада и Бо — הכל בסדר 🏠"})
    assert house.load()["people"] == "Ада и Бо — הכל בסדר 🏠"
    assert "Ада" in _raw()       # stored as text, not as \u escapes


def test_a_row_nobody_can_read_is_an_empty_page_not_a_broken_one(ark, house):
    for junk in ("not json at all", "[1, 2, 3]", '{"values": 5}', "null"):
        _put(junk)
        assert house.load() == house.blank(), junk
        assert house.updated() == "", junk
        assert house.progress(house.load())[0] == 0


def test_a_row_written_before_a_shorter_limit_is_cut_not_lost(ark, house):
    _put(json.dumps({"values": {"water": "w" * 400}, "updated": "2026-10-01 10:00"}))
    values = house.load()
    assert len(values["water"]) == 300
    assert values["water"].startswith("www")
    assert house.updated() == "2026-10-01 10:00"


def test_filled_and_missing_follow_the_page_order(house):
    values = house.clean({"notes": "spare key with Nia", "people": "Ada"})
    assert [label for label, _text in house.filled(values)] == ["Who lives here", "Anything else"]
    assert [label for label, _hint in house.missing(values)][0] == "Where we meet"


def test_the_cards_name_a_way_in_and_when_to_use_it(house):
    assert len(house.CARDS) == 3
    for address, when, sentence in house.CARDS:
        assert address.startswith("http://")
        assert when and sentence.endswith(".")
    addresses = [card[0] for card in house.CARDS]
    assert "http://ark.lan" in addresses and "http://10.42.0.1" in addresses
