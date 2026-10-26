"""A tiny QR-code writer, because a card with a QR on it must work with no internet.

The box has to print cards a phone can read: the portal's addresses, on paper, in a kitchen drawer,
next to a dead screen.  There is no QR library in the box's Python and no route to PyPI from the
Pi, so the encoder lives here — byte mode only, error correction level L, versions 1 to 4.  That is
enough for ``http://ark.lan`` and a short household address, and it is deliberately *not* a general
QR library: anything longer is refused with a sentence rather than quietly truncated.

This module is the only place that knows the QR specification.  It draws no pixels: :func:`svg`
returns one inline ``<svg>`` with a single ``<path>``, so a card needs no image file, no font and
no network.  ``tests/test_qr.py`` compares every module of versions 1-4, all eight masks, against
``qrcode`` (an independent encoder) and reads the picture back with OpenCV's detector when those
packages happen to be installed on a development machine; on the box it proves
the structure (finders, timing, quiet zone, format bits) and the refusal.

Specification used: ISO/IEC 18004, byte mode (0100), ECC L, mask chosen by the standard four
penalty rules.  No version information block (that starts at version 7), no structured append,
no kanji mode, no micro QR.
"""

from __future__ import annotations

import html
from typing import Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
#  the tables for the only versions this box draws
# ---------------------------------------------------------------------------
# version -> (data codewords, ecc codewords per block, blocks)
SPEC = {1: (19, 7, 1), 2: (34, 10, 1), 3: (55, 15, 1), 4: (80, 20, 1)}
# Alignment-pattern centres per version.  Version 1 has none.
ALIGN = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26)}
# The two-bit code each error-correction level carries inside the format block.
ECC_FORMAT = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

MAX_CHARS = 78            # version 4, level L, byte mode — the brightest card this box prints
QUIET = 4                 # modules of white margin, as the specification requires


class TooLong(ValueError):
    """The text does not fit a version this box prints.  A refusal, never a truncation."""


# ---------------------------------------------------------------------------
#  GF(256) arithmetic, primitive polynomial 0x11d
# ---------------------------------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256


def _build_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_build_tables()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> List[int]:
    """The generator polynomial for ``degree`` error-correction codewords."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef                      # multiply by x
            nxt[j + 1] ^= _mul(coef, _EXP[i])   # ... and by alpha**i
        poly = nxt
    return poly


def _rs_remainder(data: Sequence[int], degree: int) -> List[int]:
    """The error-correction bytes for ``data``, as Reed-Solomon over GF(256)."""
    gen = _rs_generator(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for i, coef in enumerate(gen[1:]):
                rem[i] ^= _mul(coef, factor)
    return rem


# ---------------------------------------------------------------------------
#  data
# ---------------------------------------------------------------------------
def _fits(payload: bytes, version: int) -> bool:
    data_codewords, _ecc, _blocks = SPEC[version]
    # 4 bits of mode + 8 bits of length (versions 1-9) + the bytes themselves.
    return 4 + 8 + 8 * len(payload) <= data_codewords * 8


def choose_version(payload: bytes) -> int:
    for version in sorted(SPEC):
        if _fits(payload, version):
            return version
    raise TooLong(f"that address is longer than this card can carry ({len(payload)} bytes)")


def _codewords(payload: bytes, version: int) -> List[int]:
    if not _fits(payload, version):
        raise TooLong(f"{len(payload)} bytes do not fit version {version}")
    data_codewords, ecc, _blocks = SPEC[version]
    bits: List[int] = []

    def push(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)              # byte mode
    push(len(payload), 8)        # length in bytes (versions 1-9)
    for byte in payload:
        push(byte, 8)
    capacity = data_codewords * 8
    push(0, min(4, capacity - len(bits)))     # terminator
    while len(bits) % 8:
        bits.append(0)
    data = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    # Pad to the data capacity with the two bytes the specification names.
    for i in range(data_codewords - len(data)):
        data.append(0xEC if i % 2 == 0 else 0x11)
    return data + _rs_remainder(data, ecc)


# ---------------------------------------------------------------------------
#  the matrix
# ---------------------------------------------------------------------------
def _blank(size: int) -> List[List[Optional[bool]]]:
    return [[None] * size for _ in range(size)]


def _set(m, fn, row: int, col: int, dark: bool) -> None:
    if 0 <= row < len(m) and 0 <= col < len(m):
        m[row][col] = bool(dark)
        fn[row][col] = True


def _finder(m, fn, size: int, cx: int, cy: int) -> None:
    """A 7x7 finder with its one-module white separator, centred on (cx, cy)."""
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            dist = max(abs(dx), abs(dy))
            _set(m, fn, cy + dy, cx + dx, dist != 2 and dist != 4)


def _alignment(m, fn, size: int, cx: int, cy: int) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            _set(m, fn, cy + dy, cx + dx, max(abs(dx), abs(dy)) != 1)


def _function_patterns(size: int, version: int):
    m = _blank(size)
    fn = [[False] * size for _ in range(size)]
    for i in range(8, size - 8):
        _set(m, fn, 6, i, i % 2 == 0)          # horizontal timing
        _set(m, fn, i, 6, i % 2 == 0)          # vertical timing
    _finder(m, fn, size, 3, 3)
    _finder(m, fn, size, size - 4, 3)
    _finder(m, fn, size, 3, size - 4)
    centres = ALIGN[version]
    for i, cy in enumerate(centres):
        for j, cx in enumerate(centres):
            corner = (i, j) in ((0, 0), (0, len(centres) - 1), (len(centres) - 1, 0))
            if corner:
                continue
            _alignment(m, fn, size, cx, cy)
    # The format blocks and the always-dark module are function modules *before* the codewords
    # are laid down: they are drawn with a placeholder here purely to reserve their squares, and
    # drawn again with the real mask afterwards.  Without this the zigzag walks into them and
    # every module after that point is shifted.
    _draw_format(m, fn, size, 0, "L")
    return m, fn


def _format_bits(mask: int, level: str = "L") -> int:
    data = (ECC_FORMAT[level] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _draw_format(m, fn, size: int, mask: int, level: str) -> None:
    bits = _format_bits(mask, level)

    def bit(i: int) -> bool:
        return (bits >> i) & 1 == 1

    for i in range(6):                                  # row i, column 8
        _set(m, fn, i, 8, bit(i))
    _set(m, fn, 7, 8, bit(6))
    _set(m, fn, 8, 8, bit(7))
    _set(m, fn, 8, 7, bit(8))
    for i in range(9, 15):                              # row 8, columns 5..0
        _set(m, fn, 8, 14 - i, bit(i))
    for i in range(8):                                  # row 8, right-hand side
        _set(m, fn, 8, size - 1 - i, bit(i))
    for i in range(8, 15):                              # column 8, bottom
        _set(m, fn, size - 15 + i, 8, bit(i))
    _set(m, fn, size - 8, 8, True)                      # the always-dark module


def _place_data(m, fn, size: int, codewords: Sequence[int]) -> None:
    """The two-column zigzag from the bottom right, skipping the vertical timing column."""
    total = len(codewords) * 8
    index = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                col = right - j
                upward = (right + 1) & 2 == 0
                row = size - 1 - vert if upward else vert
                if fn[row][col] or index >= total:
                    continue
                byte = codewords[index >> 3]
                m[row][col] = (byte >> (7 - (index & 7))) & 1 == 1
                index += 1
        right -= 2


def _mask_bit(mask: int, row: int, col: int) -> bool:
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _apply_mask(m, fn, size: int, mask: int) -> None:
    for row in range(size):
        for col in range(size):
            if not fn[row][col] and _mask_bit(mask, row, col):
                m[row][col] = not m[row][col]


def _penalty(m, size: int) -> int:
    """The four standard penalty rules; a lower score is a better-looking code."""
    score = 0
    # Rule 1: runs of five or more identical modules, in rows and in columns.
    for line in list(m) + [list(col) for col in zip(*m)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                run = 1
    # Rule 2: every 2x2 block of one colour.
    for row in range(size - 1):
        for col in range(size - 1):
            a = m[row][col]
            if a == m[row][col + 1] == m[row + 1][col] == m[row + 1][col + 1]:
                score += 3
    # Rule 3: 1:1:3:1:1 patterns with four light modules on one side.
    pattern_a = [True, False, True, True, True, False, True, False, False, False, False]
    pattern_b = list(reversed(pattern_a))
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            window = [bool(x) for x in line[i:i + 11]]
            if window == pattern_a or window == pattern_b:
                score += 40
    # Rule 4: how far the dark modules are from half.
    dark = sum(1 for row in m for cell in row if cell)
    percent = dark * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


def matrix(text: str, *, level: str = "L", version: Optional[int] = None,
           mask: Optional[int] = None) -> List[List[bool]]:
    """The finished QR matrix, row-major, ``True`` meaning a dark module.

    ``version`` and ``mask`` are for the tests: left alone, the smallest version that fits is
    used and the mask with the lowest penalty is chosen.
    """
    if level not in ECC_FORMAT:
        raise ValueError(f"unknown error-correction level: {level!r}")
    payload = str(text).encode("utf-8")
    if version is None:
        version = choose_version(payload)
    if version not in SPEC:
        raise TooLong(f"version {version} is not one this box prints (1-4)")
    codewords = _codewords(payload, version)
    size = 4 * version + 17
    base, fn = _function_patterns(size, version)
    _place_data(base, fn, size, codewords)
    zeroes = [row[:] for row in base]
    best_score, best = None, None
    for candidate in range(8):
        trial = [row[:] for row in zeroes]
        _apply_mask(trial, fn, size, candidate)
        _draw_format(trial, fn, size, candidate, level)
        if mask is not None:
            if candidate == mask:
                return [[bool(cell) for cell in row] for row in trial]
            continue
        score = _penalty(trial, size)
        if best_score is None or score < best_score:
            best_score, best = score, trial
    if mask is not None:
        raise ValueError("mask must be 0..7")
    return [[bool(cell) for cell in row] for row in best]


# ---------------------------------------------------------------------------
#  the picture
# ---------------------------------------------------------------------------
def svg(text: str, *, scale: int = 4, dark: str = "#000000", light: str = "#ffffff",
        label: str = "") -> str:
    """One inline SVG for ``text``.  A white card behind it, because scanners need contrast."""
    rows = matrix(text)
    size = len(rows)
    span = (size + QUIET * 2) * scale
    parts = []
    for row, cells in enumerate(rows):
        col = 0
        while col < size:
            if not cells[col]:
                col += 1
                continue
            start = col
            while col < size and cells[col]:
                col += 1
            x = (QUIET + start) * scale
            y = (QUIET + row) * scale
            parts.append(f"M{x} {y}h{(col - start) * scale}v{scale}h-{(col - start) * scale}z")
    caption = label or str(text)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="ark-qr" width="{span}" height="{span}" '
        f'viewBox="0 0 {span} {span}" role="img" aria-label="{html.escape(caption, quote=True)}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{span}" height="{span}" fill="{light}"/>'
        f'<path d="{"".join(parts)}" fill="{dark}"/>'
        f'</svg>'
    )


def payload_is_safe(text: str) -> bool:
    """True for the short, plain addresses this box prints — nothing that could smuggle a script."""
    value = str(text or "")
    return 0 < len(value) <= MAX_CHARS and all(32 <= ord(ch) < 127 for ch in value)


def printable_addresses(hosts: Iterable[str]) -> List[str]:
    """De-duplicated, ordered addresses for the cards; unsafe or duplicate entries are dropped."""
    seen = set()
    out = []
    for host in hosts:
        value = str(host or "").strip()
        if value and value not in seen and payload_is_safe(value):
            seen.add(value)
            out.append(value)
    return out
