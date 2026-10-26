"""The QR codes on the household cards must be readable by a phone that has no internet.

``pi/api/ark_qr.py`` is a small encoder written for this box, because a card in a kitchen drawer
cannot depend on a package that is not installed and a network that is not there.  A hand-written
encoder is exactly the kind of thing that *looks* right and scans wrong, so this file proves it two
ways:

* structurally, with nothing installed — the finder patterns, the timing line, the quiet zone, the
  always-dark module, and both copies of the format block read back out of the finished matrix;
* against other people's code — ``qrcode`` (an independent encoder: every module of versions 1-4,
  all eight masks, nine payloads) and OpenCV's detector (an independent *decoder*: the picture is
  handed back the text it started with).

The last two groups skip when those packages are absent, which is the box's own situation: there
they prove the structure and the refusal, and the Mac proves the rest.  To run them:

    .venv/bin/pip install qrcode opencv-python-headless
"""

from __future__ import annotations

import pytest

import ark_qr

PAYLOADS = [
    "http://ark.lan",
    "http://ark.local",
    "http://10.42.0.1",
    "http://ark.lan/household",
    "https://ark.local:8445/arcade/",
    "A",
    "x" * 17,
    "x" * 32,
    "x" * 78,
]


def _bits(value: int, width: int) -> list:
    return [(value >> i) & 1 == 1 for i in range(width)]


def test_the_version_is_the_smallest_one_that_fits():
    # Byte mode, level L: 17, 32, 53 and 78 bytes are the exact capacities of versions 1 to 4.
    for length, expected in ((1, 1), (17, 1), (18, 2), (32, 2), (33, 3), (53, 3), (54, 4), (78, 4)):
        assert ark_qr.choose_version(b"x" * length) == expected, length
        assert len(ark_qr.matrix("x" * length)) == 4 * expected + 17


def test_an_address_too_long_for_a_card_is_refused_not_truncated():
    with pytest.raises(ark_qr.TooLong) as refused:
        ark_qr.choose_version(b"x" * (ark_qr.MAX_CHARS + 1))
    assert "79" in str(refused.value)
    # A refusal, not a shorter code: a truncated address would scan to the wrong place.
    with pytest.raises(ark_qr.TooLong):
        ark_qr.matrix("x" * (ark_qr.MAX_CHARS + 1))


def test_the_shape_is_a_qr_symbol_and_not_just_a_square_of_noise():
    for text in ("http://ark.lan", "http://ark.local"):
        rows = ark_qr.matrix(text)
        size = len(rows)
        assert size == 4 * 1 + 17
        # The three finder patterns: a dark 7x7 ring with a 3x3 dark centre.
        for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
            for dy in range(7):
                for dx in range(7):
                    dist = max(abs(dy - 3), abs(dx - 3))
                    assert rows[top + dy][left + dx] is (dist != 2), (top + dy, left + dx)
        # The separator around each finder is light.
        assert not any(rows[7][col] for col in range(8))
        assert not any(rows[row][7] for row in range(8))
        # The timing line alternates, starting and ending dark.
        for i in range(8, size - 8):
            assert rows[6][i] is (i % 2 == 0)
            assert rows[i][6] is (i % 2 == 0)
        # The module the specification always lights, so a scanner can orient the symbol.
        assert rows[size - 8][8] is True


def test_both_copies_of_the_format_block_agree_with_the_mask_that_was_used():
    for mask in range(8):
        rows = ark_qr.matrix("http://ark.lan", mask=mask)
        size = len(rows)
        expected = _bits(ark_qr._format_bits(mask, "L"), 15)
        around_top_left = (
            [rows[i][8] for i in range(6)]
            + [rows[7][8], rows[8][8], rows[8][7]]
            + [rows[8][14 - i] for i in range(9, 15)]
        )
        along_the_edges = [rows[8][size - 1 - i] for i in range(8)] + [
            rows[size - 15 + i][8] for i in range(8, 15)
        ]
        assert around_top_left == expected, mask
        assert along_the_edges == expected, mask


def test_the_picture_has_the_white_margin_the_scanner_needs():
    rows = ark_qr.matrix("http://ark.lan")
    size = len(rows)
    scale = 6
    svg = ark_qr.svg("http://ark.lan", scale=scale)
    span = (size + ark_qr.QUIET * 2) * scale
    assert f'width="{span}" height="{span}"' in svg
    assert f'viewBox="0 0 {span} {span}"' in svg
    # The matrix itself carries no margin — the margin is part of the picture.
    assert len(rows) == size


def test_the_picture_describes_itself_and_points_at_nothing_else():
    svg = ark_qr.svg("http://ark.lan", label='ark "quoted" & <here>')
    assert 'role="img"' in svg
    assert 'aria-label="ark &quot;quoted&quot; &amp; &lt;here&gt;"' in svg
    assert "<script" not in svg and "<image" not in svg and "href=" not in svg
    assert svg.count("<path ") == 1
    # The only http:// in the picture is the SVG namespace, which is a name and not a fetch.
    assert svg.count("http://") == 1 and "http://www.w3.org/2000/svg" in svg


def test_only_a_short_plain_address_may_be_printed():
    assert ark_qr.payload_is_safe("http://ark.lan")
    assert not ark_qr.payload_is_safe("")
    assert not ark_qr.payload_is_safe("http://ark.lan\n`rm -rf /`")
    assert not ark_qr.payload_is_safe("http://ark.lan/" + "y" * 80)
    assert not ark_qr.payload_is_safe("http://ärk.lan")
    assert not ark_qr.payload_is_safe("http://ark.lan\x00")


def test_the_cards_are_de_duplicated_and_keep_their_order():
    hosts = ["http://ark.lan", "", "http://ark.local", "http://ark.lan", "  ",
             "http://10.42.0.1", "http://ark.local"]
    assert ark_qr.printable_addresses(hosts) == [
        "http://ark.lan", "http://ark.local", "http://10.42.0.1"
    ]
    # A payload that is not a plain address is dropped rather than printed as a card.
    assert ark_qr.printable_addresses(["bad\naddress"]) == []


def test_an_impossible_request_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        ark_qr.matrix("http://ark.lan", level="X")
    with pytest.raises(ValueError):
        ark_qr.matrix("http://ark.lan", mask=8)
    with pytest.raises(ark_qr.TooLong):
        ark_qr.matrix("http://ark.lan", version=7)


def test_every_module_matches_an_independent_encoder():
    qrcode = pytest.importorskip("qrcode", reason="cross-check needs `pip install qrcode`")
    from qrcode.constants import ERROR_CORRECT_L

    for text in PAYLOADS:
        version = ark_qr.choose_version(text.encode())
        for mask in range(8):
            mine = [[1 if cell else 0 for cell in row]
                    for row in ark_qr.matrix(text, version=version, mask=mask)]
            theirs = qrcode.QRCode(version=version, error_correction=ERROR_CORRECT_L,
                                   box_size=1, border=0, mask_pattern=mask)
            theirs.add_data(qrcode.util.QRData(text.encode(), mode=qrcode.util.MODE_8BIT_BYTE),
                            optimize=0)
            theirs.make(fit=False)
            expected = [[1 if cell else 0 for cell in row] for row in theirs.get_matrix()]
            assert mine == expected, (text, version, mask)


def test_a_real_decoder_reads_the_text_back():
    cv2 = pytest.importorskip("cv2", reason="cross-check needs `pip install opencv-python-headless`")
    numpy = pytest.importorskip("numpy")
    detector = cv2.QRCodeDetector()
    scale = 8
    for text in PAYLOADS:
        rows = ark_qr.matrix(text)
        span = (len(rows) + ark_qr.QUIET * 2) * scale
        picture = numpy.full((span, span), 255, dtype=numpy.uint8)
        for row, cells in enumerate(rows):
            for col, dark in enumerate(cells):
                if dark:
                    y, x = (ark_qr.QUIET + row) * scale, (ark_qr.QUIET + col) * scale
                    picture[y:y + scale, x:x + scale] = 0
        read, _points, _straight = detector.detectAndDecode(picture)
        assert read == text, text
