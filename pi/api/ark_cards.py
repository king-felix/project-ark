"""The cards to stick on the wall: one address, one square, one sentence.

A phone camera is the only way into the box when nobody has typed the address yet, so
the card has to carry a square a camera can read *and* the address in letters, because
a dead phone cannot scan anything and a guest should never need to guess.

Three addresses are worth a card: the box's own network name, the home-network name, and
the number that always works while the box is broadcasting.  The squares are drawn by
:mod:`ark_qr` — an inline ``<svg>`` with one path, so a printed card needs no image
file, no font, no script and no network.
"""

from __future__ import annotations

import ark_household
import ark_qr

# How big one module of the square is drawn on paper (millimetres come out of the CSS;
# this is pixels in the page, and 6 keeps a version-2 square above 2 cm on a phone).
SCALE = 6


def cards() -> list:
    """Every wall card the box can honestly print, in the order they matter."""
    out = []
    for address, when, sentence in ark_household.CARDS:
        if not ark_qr.payload_is_safe(address):
            continue            # a card nobody could read is not a card
        try:
            picture = ark_qr.svg(address, scale=SCALE, label=address)
        except ark_qr.TooLong:
            continue            # too long for the square; the page still shows the words
        out.append({"address": address, "when": when, "sentence": sentence,
                    "svg": picture})
    return out


def addresses() -> list:
    """Just the addresses, for a page that lists them as words."""
    return [card["address"] for card in cards()]
