"""PROJECT ARK — what a region is, once it has been folded small enough to hold.

A *map pack* is one folder on the card:

    manifest.json    what it is, where the bytes came from, every fingerprint
     graph.json      how to read graph.bin (counts, names, profiles, turns)
     graph.bin       the routing graph: a header, node points, an index, edges
   places.json       what can be searched for: names, kinds, points
    base-major.geojson / base-minor.geojson / water.geojson / forest.geojson /
    boundary.geojson / buildings.geojson
     attribution.txt one line that must stay on the screen

Everything here is a *format*: this module writes and reads those files and knows
nothing about OSM downloaders, browsers, nginx or the card.  The host-side
builder (`scripts/ark-map-pack`, which needs pyosmium) reads an .osm.pbf and calls
`build_pack()`; the browser reads the same bytes with
`pi/maps/web/router.js`, which knows `GRAPH_LAYOUT` by heart.

Three rules this file keeps:

* **A pack says where it came from.**  `manifest.json` carries the source URL, the
  source file's sha256 and sha256 of the source bytes, the date of the data, the
  licence (ODbL-1.0 for OpenStreetMap data) and `attribution.txt` is the sentence
  that has to stay visible.  A pack without any of it is a pack nobody may
  publish.
* **A pack says what it can do.**  The manifest names the routing profiles the
  graph actually supports, whether turn restrictions were carried, whether the
  search index has addresses, and the zoom range the drawing layers were built
  for.  The browser refuses a pack whose engine or application version it does
  not understand instead of opening half of it.
* **Nothing here decides what runs.**  No service, no path on the live box, no
  certificate: this is data and the arithmetic that reads it.

The graph is version 2.  Version 1 (the first prototype) had one edge per
direction with no way id and no mode mask, which made a one-way street one-way
for a walker and made a turn restriction impossible to express.  Version 2
adds the two fields that fix both, and a reader refuses a version it does not
know rather than guessing.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import struct
import time
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# -----------------------------------------------------------------------------
# The graph, byte for byte
# -----------------------------------------------------------------------------
#
#   0  magic       4s   b"ARKG"
#   4  version     H    2
#   6  flags       H    0 (reserved)
#   8  nodeCount   I
#  12  edgeCount   I    directed
#  16  nameCount   I    street names, in the order graph.json lists them
#  20  reserved    I    0
#                        -> 24 bytes of header
#  24  nodes       nodeCount  * 8   int32 lat_e6, int32 lon_e6
#      offsets     (nodeCount+1) * 4 uint32, ascending: node i owns
#                                    edges[offsets[i]:offsets[i+1]]
#      edges       edgeCount  * 18  uint32 to, float32 metres, uint16 class,
#                                    uint16 name, uint32 way, uint16 modes
#
# A node's edges are its *outgoing* edges.  A street usable in both directions is
# two edges, one from each end, and that is on purpose: direction is stored, not
# inferred at query time.  `modes` is a bit mask (car=1, bike=2, foot=4), so a
# one-way street can be one-way for a car and still walkable back up the hill.
# `way` is the OpenStreetMap way id, which is what a turn restriction names.
GRAPH_MAGIC = b"ARKG"
GRAPH_VERSION = 2
GRAPH_HEADER = struct.Struct("<4sHHIIII")
_NODE = struct.Struct("<ii")
_OFFSET = struct.Struct("<I")
_EDGE = struct.Struct("<IfHHIH")
NO_NAME = 0xFFFF
GRAPH_LAYOUT = (
    "header 24B (magic ARKG, version, flags, nodes, edges, names, reserved); "
    "nodes nodeCount*8B (int32 lat*1e6, int32 lon*1e6); "
    "offsets (nodeCount+1)*4B; edges edgeCount*18B (uint32 to, float32 metres, "
    "uint16 class, uint16 name, uint32 way, uint16 modes)"
)

#: How many bytes one edge occupies.  The browser and the tests read this number.
EDGE_BYTES = _EDGE.size

# -----------------------------------------------------------------------------
# Profiles and access — who may use an edge
# -----------------------------------------------------------------------------
MODE_CAR = 1
MODE_BIKE = 2
MODE_FOOT = 4
ALL_MODES = MODE_CAR | MODE_BIKE | MODE_FOOT
MODE_BITS: Dict[str, int] = {"car": MODE_CAR, "bike": MODE_BIKE, "foot": MODE_FOOT}

# The road classes, frozen and append-only: the number is written into every edge
# and the browser turns it into a width and a speed.  A class we do not know goes
# on the end; a pack built by an older builder keeps working in a newer browser.
ROAD_CLASSES = (
    "path",         # 0  footway, steps, bridleway, pedestrian
    "track",        # 1  track, byway, service
    "residential",  # 2  residential, unclassified, living_street
    "tertiary",     # 3
    "secondary",    # 4
    "primary",      # 5
    "trunk",        # 6
    "motorway",     # 7
)

HIGHWAY_CLASS: Dict[str, str] = {
    "motorway": "motorway",
    "motorway_link": "motorway",
    "trunk": "trunk",
    "trunk_link": "trunk",
    "primary": "primary",
    "primary_link": "primary",
    "secondary": "secondary",
    "secondary_link": "secondary",
    "tertiary": "tertiary",
    "tertiary_link": "tertiary",
    "residential": "residential",
    "unclassified": "residential",
    "living_street": "residential",
    "road": "residential",
    "service": "track",
    "track": "track",
    "byway": "track",
    "path": "path",
    "footway": "path",
    "pedestrian": "path",
    "steps": "path",
    "bridleway": "path",
    "cycleway": "path",
    "corridor": "path",
}

# The classes drawn on the "major" layer; everything else goes on "minor".
MAJOR_CLASSES = frozenset(("primary", "trunk", "motorway"))

# Metres per second at the class's own limit — a planning number, not a promise.
# `manifest.json` records that these are class defaults, not a measured survey.
SPEEDS_KMH: Dict[str, float] = {
    "motorway": 110.0,
    "trunk": 90.0,
    "primary": 70.0,
    "secondary": 60.0,
    "tertiary": 50.0,
    "residential": 30.0,
    "track": 16.0,
    "path": 5.0,
}

# Which classes each profile may use at all.  Access tags narrow this further.
PROFILE_CLASSES: Dict[str, Tuple[str, ...]] = {
    "car": ("motorway", "trunk", "primary", "secondary", "tertiary",
            "residential", "track"),
    "bike": ("primary", "secondary", "tertiary", "residential", "track", "path"),
    "foot": ("primary", "secondary", "tertiary", "residential", "track", "path"),
}

# A speed factor per class, so one table serves every profile: a bicycle on a
# primary road does not do 70.  Feet are the exception — walking is walking, and
# the class decides *whether* you may go, not how fast.
PROFILE_FACTOR: Dict[str, Dict[str, float]] = {
    "car": {"motorway": 1.0, "trunk": 1.0, "primary": 1.0, "secondary": 1.0,
            "tertiary": 1.0, "residential": 1.0, "track": 0.6},
    "bike": {"primary": 0.35, "secondary": 0.4, "tertiary": 0.45,
             "residential": 0.55, "track": 0.55, "path": 0.5},
    "foot": {},
}

FOOT_KMH = 4.8

# What each way of travelling is called on the screen, and whether a one-way rule
# applies to it at all.  A walker ignores `oneway=yes`; a bicycle obeys it unless
# the way says `oneway:bicycle=no`.
PROFILES: Dict[str, Dict[str, object]] = {
    "car": {
        "label": "By car",
        "classes": PROFILE_CLASSES["car"],
        "factor": PROFILE_FACTOR["car"],
        "oneway": True,
        "speed_kmh": SPEEDS_KMH["motorway"],
    },
    "bike": {
        "label": "By bicycle",
        "classes": PROFILE_CLASSES["bike"],
        "factor": PROFILE_FACTOR["bike"],
        "oneway": True,
        "speed_kmh": SPEEDS_KMH["residential"],
    },
    "foot": {
        "label": "On foot",
        "classes": PROFILE_CLASSES["foot"],
        "factor": PROFILE_FACTOR["foot"],
        "oneway": False,
        "speed_kmh": FOOT_KMH,
    },
}
DEFAULT_PROFILE = "car"

# A turn costs time even when the two streets are the same length.  Without it a
# router happily zig-zags through a grid because every zig saves a metre; with it
# the answer reads like something a person would drive.  Seconds, per turn kind.
TURN_PENALTY_S = 4.0
TURN_PENALTY_UTURN_S = 20.0

# Restriction kinds written into graph.json.
TURN_NO = 0      # may not go from way A to way B through this node
TURN_ONLY = 1    # the only way out of this node from way A is way B


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres.  The one measurement in this module."""
    radius = 6371008.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * radius * math.asin(math.sqrt(a))


# -----------------------------------------------------------------------------
# Reading one way's tags
# -----------------------------------------------------------------------------
#
# These are pure functions of a tag dict, so a test can drive them without a PBF
# and the browser's build path can be checked against them.  They never invent a
# tag: a missing `highway` means the way is not drawn on the road layers and not
# routable, full stop.
_ROUTABLE_HIGHWAY = frozenset(HIGHWAY_CLASS)

# `highway=construction` / `proposed` / `abandoned` and friends are not roads yet.
_REFUSE_HIGHWAY = frozenset(("construction", "proposed", "abandoned", "razed",
                             "disused", "platform", "elevator", "raceway"))

# Values of an access tag that mean "keep out".  `destination` is deliberately
# *not* here: a street signed "residents only" is still a street a visitor may
# walk down, and refusing it would cut a village in half.
_ACCESS_NO = frozenset((
    "no", "private", "agricultural", "forestry", "military", "customers",
))

# Values that mean "yes, you may" even after a broader `access=no`.
_ACCESS_YES = frozenset((
    "yes", "designated", "official", "permissive", "destination", "customers",
))


def _yes_no(raw: Optional[str]) -> Optional[bool]:
    """`True`/`False` for an access value, `None` when the tag says nothing."""
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in _ACCESS_NO:
        return False
    if value in _ACCESS_YES:
        return True
    return None


def _base_modes(tags: Dict[str, str]) -> int:
    """Modes allowed by the highway class alone, before access tags."""
    highway = (tags.get("highway") or "").strip().lower()
    if not highway or highway not in _ROUTABLE_HIGHWAY:
        return 0
    if highway in _REFUSE_HIGHWAY:
        return 0
    if (tags.get("area") or "").strip().lower() == "yes":
        return 0
    if (tags.get("service") or "").strip().lower() == "parking_aisle":
        # A car park's aisles are a maze, not a network: keeping them adds edges
        # that are never the right answer and make the graph read as denser than
        # the streets really are.
        return 0
    klass = HIGHWAY_CLASS[highway]
    modes = 0
    for profile, classes in PROFILE_CLASSES.items():
        if klass in classes:
            modes |= MODE_BITS[profile]
    if not modes:
        return 0
    # A footway is for feet unless it says otherwise; a bicycle on it is a
    # decision the survey made, not one we may guess.  `path` and `cycleway`
    # keep their bicycles.
    if highway in ("footway", "steps", "pedestrian", "corridor"):
        if _yes_no(tags.get("bicycle")) is not True:
            modes &= ~MODE_BIKE
    # A motorway is not for bicycles or feet, whatever the class table says.
    if klass in ("motorway", "trunk"):
        modes &= ~(MODE_BIKE | MODE_FOOT)
    return modes


def access_modes(tags: Dict[str, str]) -> int:
    """The modes that may use this way in *some* direction, as a bit mask.

    `access=no` closes the way to everything, then a specific `foot=yes` (or
    `bicycle=yes`) opens that one mode again — which is exactly how OSM reads.
    A way we leave out is a missing street on a map; a way we wrongly include
    sends somebody through a locked gate, so the refusal wins ties.
    """
    modes = _base_modes(tags)
    if not modes:
        return 0
    general = _yes_no(tags.get("access"))
    if general is False:
        modes = 0
    # `vehicle` covers car and bicycle, never feet.
    vehicle = _yes_no(tags.get("vehicle"))
    if vehicle is not None:
        if vehicle:
            modes |= _base_modes(tags) & (MODE_CAR | MODE_BIKE)
        else:
            modes &= ~(MODE_CAR | MODE_BIKE)
    for key, mask in (("motor_vehicle", MODE_CAR), ("motorcar", MODE_CAR),
                      ("bicycle", MODE_BIKE), ("foot", MODE_FOOT)):
        allowed = _yes_no(tags.get(key))
        if allowed is True:
            modes |= _base_modes(tags) & mask
        elif allowed is False:
            modes &= ~mask
    return modes


def is_routable(tags: Dict[str, str]) -> bool:
    """Is this way something the graph should carry?"""
    return access_modes(tags) != 0


def road_class(tags: Dict[str, str]) -> str:
    """The class name for a way, or `''` when the way is not routable."""
    if not is_routable(tags):
        return ""
    highway = (tags.get("highway") or "").strip().lower()
    return HIGHWAY_CLASS.get(highway, "residential")


def class_id(name: str) -> int:
    """The number written into an edge; unknown names land on `residential`."""
    try:
        return ROAD_CLASSES.index(name)
    except ValueError:
        return ROAD_CLASSES.index("residential")


def class_name(number: int) -> str:
    """The class for an edge number a future builder wrote (never raises)."""
    if 0 <= number < len(ROAD_CLASSES):
        return ROAD_CLASSES[number]
    return "residential"


def oneway(tags: Dict[str, str]) -> int:
    """Direction of travel for vehicles: `1` forward, `-1` backward, `0` both.

    A roundabout is one-way unless it says otherwise, and that is the one
    implicit case worth honouring — a two-way roundabout is a mistake in the
    data and driving it both ways is a mistake on the road.  Feet ignore this
    number entirely; `direction_modes()` is where that is applied.
    """
    raw = (tags.get("oneway") or "").strip().lower()
    if raw in ("yes", "true", "1"):
        return 1
    if raw in ("-1", "reverse"):
        return -1
    if raw in ("no", "false", "0"):
        return 0
    if (tags.get("junction") or "").strip().lower() == "roundabout":
        return 1
    if (tags.get("highway") or "").strip().lower() in ("motorway", "motorway_link"):
        return 1
    return 0


def direction_modes(tags: Dict[str, str]) -> Tuple[int, int]:
    """`(forward_modes, backward_modes)` for one way.

    A one-way street is one-way for a car and a bicycle (unless the survey says
    `oneway:bicycle=no`), and two-way for a walker.  Storing the two masks on
    the edges is what lets the router be honest without a second graph.
    """
    modes = access_modes(tags)
    if not modes:
        return 0, 0
    forward = modes
    backward = modes
    direction = oneway(tags)
    vehicles = modes & (MODE_CAR | MODE_BIKE)
    if direction == 1:
        backward &= ~vehicles
    elif direction == -1:
        forward &= ~vehicles
    # A contraflow bicycle is common and explicitly surveyed.
    if direction in (1, -1) and (modes & MODE_BIKE):
        bicycle = _yes_no(tags.get("oneway:bicycle"))
        opposite = (tags.get("cycleway") or "").strip().lower().startswith("opposite")
        if bicycle is False or opposite:
            if direction == 1:
                backward |= MODE_BIKE
            else:
                forward |= MODE_BIKE
    return forward, backward


def speed_ms(class_name_: str, profile: str = DEFAULT_PROFILE) -> float:
    """Metres per second for a class under a profile — the router's cost basis."""
    if profile == "foot":
        return FOOT_KMH / 3.6
    factor = float(PROFILE_FACTOR.get(profile, {}).get(class_name_, 1.0))
    return (SPEEDS_KMH.get(class_name_, 30.0) * factor) / 3.6


# -----------------------------------------------------------------------------
# The pack itself
# -----------------------------------------------------------------------------
PACK_FORMAT = "ark-map-pack"
PACK_SCHEMA = 2

#: The browser application this format was designed against.  A pack says which
#: application versions may open it; the app refuses anything outside the range
#: rather than opening half a pack and looking broken.
APP_VERSION = 1
APP_COMPAT = {"min": 1, "max": 1}
ENGINE = {"graph": GRAPH_VERSION, "places": 1, "layers": 1}

LICENCE = "ODbL-1.0"
ATTRIBUTION = (
    "Map data \u00a9 OpenStreetMap contributors, available under the Open "
    "Database Licence (ODbL 1.0)."
)
ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"

# Drawn layers, in the order they are stacked (lowest first).  One GeoJSON file
# each, so the browser fetches only what it is about to draw and a thin network is
# a small file at a wide zoom.  `buildings` is optional in a pack: a region with
# no building footprints still draws, it just says so in the manifest.
LAYERS = ("forest", "water", "boundary", "buildings", "base-minor", "base-major")
REQUIRED_LAYERS = ("base-major", "base-minor")
PACK_FILES = ("manifest.json", "graph.json", "graph.bin", "places.json",
              "attribution.txt") + tuple("%s.geojson" % name for name in LAYERS)

# Bounds of a real region, used to refuse a manifest that claims a point or a
# whole planet.  A pack is a *regional* extract; this is a sanity check, not a
# political boundary.
MAX_ABS_LAT = 85.0
MAX_ABS_LON = 180.0
MAX_PACK_BYTES = 4 * 1024 ** 3        # 4 GiB; larger belongs on a different tool
MAX_GRAPH_POINTS = 400000
MAX_PLACES = 40000
MAX_BUILDINGS = 30000

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def name_key(text: str) -> str:
    """The form a search compares: no case, no accents, no runs of spaces.

    'Z\u00fcrich' and 'zurich' are the same place to somebody thumbing a keyboard,
    and a search that insists otherwise is a search that says "not found" about a
    name on the map in front of them.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def slug(text: str) -> str:
    """A folder-safe name for a pack: lower case ASCII words joined by hyphens."""
    key = name_key(text)
    out = []
    for ch in key:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "region"


def fingerprint(path: str) -> str:
    """sha256 of a file, read in blocks so a 500 MB source does not eat the RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _write_bytes(path: str, payload: bytes) -> int:
    """Write temp-then-rename in the same folder, so a cut cannot leave half a file."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    temp = os.path.join(folder, ".%s.part" % os.path.basename(path))
    with open(temp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    # The file's own bytes are on the card; the rename that published them is
    # made durable too, or a cut could leave the old name in place.
    try:
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    return len(payload)


def write_json_file(path: str, data: object, *, indent: int = 0) -> int:
    """Write JSON with stable key order and no NaN, through the one writer above."""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=indent or None,
                      separators=(",", ":") if not indent else (",", ": "))
    return _write_bytes(path, text.encode("utf-8"))


def read_json_file(path: str) -> object:
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


# -----------------------------------------------------------------------------
# Building the graph
# -----------------------------------------------------------------------------


class Graph(object):
    """A direction-resolved street network, small enough to hold in a browser.

    Points are kept as floats here and written as fixed-point (1e-6 degrees, about
    11 cm) into `graph.bin`.  Edges are stored *outgoing*: `out[i]` is the list of
    edges that leave node `i`, so the router's inner loop is a flat scan and never
    branches on direction.  A two-way street is two edges and that is on purpose —
    direction is stored, not inferred at query time.
    """

    def __init__(self) -> None:
        self.lat: List[float] = []
        self.lon: List[float] = []
        self.refs: Dict[int, int] = {}          # OSM node id -> index
        self.names: List[str] = []
        self._name_ids: Dict[str, int] = {}
        self.out: List[List[Tuple[int, float, int, int, int, int]]] = []
        self.turns: List[Tuple[int, int, int, int, int]] = []
        self.ways = 0                           # ways that made it into the graph
        self.skipped = 0                        # ways refused by `is_routable`
        self.oneway_edges = 0

    # -- points ---------------------------------------------------------------

    def node(self, osm_id: int, lat: float, lon: float) -> int:
        """The index for an OSM node, remembering it the first time it is asked."""
        found = self.refs.get(osm_id)
        if found is not None:
            return found
        index = len(self.lat)
        self.lat.append(lat)
        self.lon.append(lon)
        self.out.append([])
        self.refs[osm_id] = index
        return index

    def node_count(self) -> int:
        return len(self.lat)

    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.out)

    # -- names ----------------------------------------------------------------

    def name_id(self, name: str) -> int:
        """Street names are interned: one id, one copy in `graph.json`."""
        cleaned = " ".join((name or "").split())
        if not cleaned:
            return NO_NAME
        found = self._name_ids.get(cleaned)
        if found is not None:
            return found
        index = len(self.names)
        if index >= NO_NAME:
            # 65,535 streets in one region is already a country; past it a name is
            # dropped rather than wrapped round onto somebody else's street.
            return NO_NAME
        self.names.append(cleaned)
        self._name_ids[cleaned] = index
        return index

    # -- ways -----------------------------------------------------------------

    def add_way(self, points: Sequence[int], tags: Dict[str, str],
                way_id: int = 0) -> bool:
        """Add one way as edges.  Returns False when the way is not usable.

        `points` are already-resolved node indexes in OSM order.  A way with fewer
        than two points adds nothing: a single point is not a street.
        """
        klass = road_class(tags)
        if not klass:
            self.skipped += 1
            return False
        if len(points) < 2:
            return False
        forward, backward = direction_modes(tags)
        if not forward and not backward:
            self.skipped += 1
            return False
        cls = class_id(klass)
        nid = self.name_id(tags.get("name") or tags.get("ref") or "")
        way = int(way_id or 0) & 0xFFFFFFFF
        for a, b in zip(points, points[1:]):
            if a == b:
                continue
            metres = haversine_m(self.lat[a], self.lon[a], self.lat[b], self.lon[b])
            if metres < 0.5:
                # Two nodes a few centimetres apart are a survey artefact, not a
                # street: they add an edge that costs nothing and make a directions
                # list say "turn left" twice in the same instant.
                continue
            if metres > 100000.0:
                # A straight line longer than 100 km inside one way is not a
                # street; it is a broken extract or a ferry crossing, and a
                # router must not send a car across it as if it were tarmac.
                continue
            if forward:
                self.out[a].append((b, metres, cls, nid, way, forward))
            if backward:
                self.out[b].append((a, metres, cls, nid, way, backward))
            if forward and not backward or backward and not forward:
                self.oneway_edges += 1
        self.ways += 1
        return True

    # -- turn restrictions ----------------------------------------------------

    def add_turn(self, from_way: int, via_node: int, to_way: int,
                 kind: int, modes: int = ALL_MODES) -> bool:
        """Record one `no_*` or `only_*` restriction at a node."""
        if from_way <= 0 or to_way <= 0 or not modes:
            return False
        if not (0 <= via_node < len(self.lat)):
            return False
        self.turns.append((int(from_way) & 0xFFFFFFFF, int(via_node),
                           int(to_way) & 0xFFFFFFFF, int(kind), int(modes)))
        return True

    # -- writing --------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Pack the graph exactly as `GRAPH_LAYOUT` describes it."""
        count = len(self.lat)
        edges = self.edge_count()
        head = GRAPH_HEADER.pack(GRAPH_MAGIC, GRAPH_VERSION, 0, count, edges,
                                 len(self.names), 0)
        nodes = bytearray(count * _NODE.size)
        offsets = bytearray((count + 1) * _OFFSET.size)
        body = bytearray(edges * _EDGE.size)
        at = 0
        for index in range(count):
            _NODE.pack_into(nodes, index * _NODE.size,
                            int(round(self.lat[index] * 1e6)),
                            int(round(self.lon[index] * 1e6)))
            _OFFSET.pack_into(offsets, index * _OFFSET.size, at)
            for to, metres, cls, nid, way, modes in self.out[index]:
                _EDGE.pack_into(body, at * _EDGE.size, to, float(metres), cls,
                                nid, way, modes)
                at += 1
        _OFFSET.pack_into(offsets, count * _OFFSET.size, at)
        return bytes(head) + bytes(nodes) + bytes(offsets) + bytes(body)

    def to_json(self) -> Dict[str, object]:
        """Everything the browser needs to read `graph.bin` — and nothing else."""
        return {
            "format": "ark-map-graph",
            "version": GRAPH_VERSION,
            "layout": GRAPH_LAYOUT,
            "nodes": len(self.lat),
            "edges": self.edge_count(),
            "names": list(self.names),
            "classes": list(ROAD_CLASSES),
            "modes": dict(MODE_BITS),
            "speeds_kmh": dict(SPEEDS_KMH),
            "foot_kmh": FOOT_KMH,
            "profiles": {key: {"label": PROFILES[key]["label"],
                               "classes": list(PROFILES[key]["classes"]),
                               "factor": dict(PROFILES[key]["factor"]),
                               "oneway": bool(PROFILES[key]["oneway"])}
                         for key in sorted(PROFILES)},
            "turn_penalty_s": TURN_PENALTY_S,
            "turn_penalty_uturn_s": TURN_PENALTY_UTURN_S,
            # [from way, via node index, to way, kind, mode mask]
            "turns": [list(row) for row in self.turns],
        }

    def bounds(self) -> List[float]:
        """`[south, west, north, east]` — what the map should open on."""
        if not self.lat:
            return [0.0, 0.0, 0.0, 0.0]
        return [min(self.lat), min(self.lon), max(self.lat), max(self.lon)]

    def stats(self) -> Dict[str, int]:
        return {
            "points": len(self.lat),
            "edges": self.edge_count(),
            "ways": self.ways,
            "skipped": self.skipped,
            "oneway_edges": self.oneway_edges,
            "names": len(self.names),
            "turns": len(self.turns),
        }


# -----------------------------------------------------------------------------
# Reading the graph back — the other half of the contract
# -----------------------------------------------------------------------------


class ReadGraph(object):
    """A graph parsed out of `graph.bin`, for verifying what was written.

    The browser's router walks the same bytes with the same arithmetic in
    `web/router.js`; this class exists so a pack can be *checked* before it is
    published (and so a test can prove the layout without a browser).  It holds
    the file's bytes and decodes on demand rather than building objects for
    every edge: a 200,000-edge pack is not something to expand twice.
    """

    def __init__(self, payload: bytes) -> None:
        if len(payload) < GRAPH_HEADER.size:
            raise ValueError("this is not a map graph: the file is %d bytes, the "
                             "header alone is %d" % (len(payload), GRAPH_HEADER.size))
        magic, version, _flags, nodes, edges, names, _reserved = GRAPH_HEADER.unpack_from(payload, 0)
        if magic != GRAPH_MAGIC:
            raise ValueError("this is not a map graph: it starts with %r, not %r"
                             % (magic, GRAPH_MAGIC))
        if version != GRAPH_VERSION:
            raise ValueError("this map graph is version %d and this reader knows %d"
                             % (version, GRAPH_VERSION))
        self.payload = payload
        self.nodes = nodes
        self.edges = edges
        self.name_count = names
        self.at_nodes = GRAPH_HEADER.size
        self.at_offsets = self.at_nodes + nodes * _NODE.size
        self.at_edges = self.at_offsets + (nodes + 1) * _OFFSET.size
        self.expected = self.at_edges + edges * _EDGE.size
        if len(payload) != self.expected:
            raise ValueError("this map graph is %d bytes and its own header says it "
                             "should be %d — the file is cut or padded"
                             % (len(payload), self.expected))
        if nodes == 0:
            raise ValueError("this map graph has no points: there is nothing to route on")

    def point(self, index: int) -> Tuple[float, float]:
        lat_e6, lon_e6 = _NODE.unpack_from(self.payload, self.at_nodes + index * _NODE.size)
        return lat_e6 / 1e6, lon_e6 / 1e6

    def offsets(self, index: int) -> Tuple[int, int]:
        start = _OFFSET.unpack_from(self.payload, self.at_offsets + index * _OFFSET.size)[0]
        end = _OFFSET.unpack_from(self.payload, self.at_offsets + (index + 1) * _OFFSET.size)[0]
        return start, end

    def edge(self, number: int) -> Tuple[int, float, int, int, int, int]:
        return _EDGE.unpack_from(self.payload, self.at_edges + number * _EDGE.size)

    def problems(self) -> List[str]:
        """Everything wrong with the file a reader can see, as sentences.

        An empty list is the only healthy answer.  These are the checks the
        verifier runs: an edge that points past the last point, offsets that do
        not ascend, a name id out of range, a length that is not a number, a mode
        mask that permits nobody or names a profile this reader never heard of.
        """
        found: List[str] = []
        last = 0
        for index in range(self.nodes):
            start, end = self.offsets(index)
            if start != last:
                found.append("point %d starts its edges at %d, the one before ended "
                             "at %d" % (index, start, last))
            if end < start:
                found.append("point %d has edges from %d to %d" % (index, start, end))
                break
            last = end
        if last != self.edges:
            found.append("the offsets end at %d edges and the header says %d"
                         % (last, self.edges))
        for number in range(self.edges):
            to, metres, cls, nid, _way, modes = self.edge(number)
            if to >= self.nodes:
                found.append("edge %d points at point %d, and there are %d"
                             % (number, to, self.nodes))
                break
            if metres != metres or metres <= 0 or metres > 100000:
                found.append("edge %d is %.1f metres long" % (number, metres))
                break
            if cls >= len(ROAD_CLASSES):
                found.append("edge %d is road class %d, and only %d are known"
                             % (number, cls, len(ROAD_CLASSES)))
                break
            if nid != NO_NAME and nid >= self.name_count:
                found.append("edge %d names street %d, and graph.json lists %d"
                             % (number, nid, self.name_count))
                break
            if modes == 0 or (modes & ~ALL_MODES):
                found.append("edge %d permits modes %d, which is not a combination "
                             "this reader knows" % (number, modes))
                break
        return found


def read_graph_bytes(payload: bytes) -> ReadGraph:
    return ReadGraph(payload)


def read_graph(folder: str) -> ReadGraph:
    """Parse `graph.bin` beside the given `graph.json`, and cross-check the counts."""
    path = os.path.join(folder, "graph.bin")
    with open(path, "rb") as handle:
        graph = ReadGraph(handle.read())
    side = read_json_file(os.path.join(folder, "graph.json"))
    if not isinstance(side, dict):
        raise ValueError("graph.json is not an object")
    if int(side.get("nodes", -1)) != graph.nodes:
        raise ValueError("graph.json says %s points and the graph holds %d"
                         % (side.get("nodes"), graph.nodes))
    if int(side.get("edges", -1)) != graph.edges:
        raise ValueError("graph.json says %s edges and the graph holds %d"
                         % (side.get("edges"), graph.edges))
    if len(side.get("names") or []) != graph.name_count:
        raise ValueError("graph.json lists %d street names and the graph holds %d"
                         % (len(side.get("names") or []), graph.name_count))
    graph.names = list(side.get("names") or [])
    return graph


# -----------------------------------------------------------------------------
# Drawing layers and places — what a person sees and can type at
# -----------------------------------------------------------------------------


def _round(value: float) -> float:
    """Coordinates to 6 decimal places: 11 cm, and half the bytes of a float64 dump."""
    return round(float(value), 6)


def feature_collection(features: Optional[Iterable[Dict[str, object]]] = None,
                       **extra: object) -> Dict[str, object]:
    """A GeoJSON FeatureCollection, with room for a sentence beside the features."""
    body: Dict[str, object] = {"type": "FeatureCollection",
                               "features": list(features or [])}
    body.update(extra)
    return body


def line_feature(points: Sequence[Tuple[float, float]],
                 properties: Optional[Dict[str, object]] = None) -> Optional[Dict[str, object]]:
    """One drawn line from a list of `(lat, lon)`, or `None` if it is not a line.

    The first and last point decide whether there is a line at all: a way whose
    geometry collapsed to a single point is not drawn and not stored, because a
    file full of zero-length lines is a file that costs bytes to say nothing.
    """
    drawn: List[List[float]] = []
    for pair in points:
        # GeoJSON order is [lon, lat]; the rest of this module speaks (lat, lon).
        point = [_round(pair[1]), _round(pair[0])]
        if not drawn or drawn[-1] != point:
            drawn.append(point)
    if len(drawn) < 2:
        return None
    return {"type": "Feature",
            "properties": dict(properties or {}),
            "geometry": {"type": "LineString", "coordinates": drawn}}


def ring_area_m2(points: Sequence[Tuple[float, float]]) -> float:
    """A closed ring's area in square metres, near enough to sort lakes from ponds.

    Equirectangular projection around the ring's mean latitude: the error is a
    fraction of a percent at regional scale, which is all a *size threshold* needs.
    This is not a survey and nothing else in the format uses it.
    """
    if len(points) < 3:
        return 0.0
    lat0 = sum(p[0] for p in points) / len(points)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110574.0
    total = 0.0
    for index, (lat1, lon1) in enumerate(points):
        lat2, lon2 = points[(index + 1) % len(points)]
        total += (lon1 * kx) * (lat2 * ky) - (lon2 * kx) * (lat1 * ky)
    return abs(total) / 2.0


def polygon_feature(points: Sequence[Tuple[float, float]],
                    properties: Optional[Dict[str, object]] = None) -> Optional[Dict[str, object]]:
    """One filled shape from a ring of `(lat, lon)` — closed here, never assumed.

    An OpenStreetMap water or forest way is usually closed but is not required to
    be, so the ring is closed on the way out rather than trusted on the way in.  A
    shape that cannot hold three corners is drawn as a line instead: a lake that
    lost its geometry to a broken way should still mark the spot on the map.
    """
    ring: List[List[float]] = []
    for pair in points:
        # GeoJSON order is [lon, lat]; the rest of this module speaks (lat, lon).
        point = [_round(pair[1]), _round(pair[0])]
        if not ring or ring[-1] != point:
            ring.append(point)
    if ring and ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    if len(ring) < 4:
        return line_feature(points, properties)
    return {"type": "Feature",
            "properties": dict(properties or {}),
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def place(name: str, kind: str, lat: float, lon: float,
          **extra: object) -> Optional[Dict[str, object]]:
    """One thing a search can find: a name, what it is, and where it is.

    `key` is `name_key()` of the name, so the browser compares the same form the
    builder stored.  A place with no name is not a searchable place and returns
    `None` — an unnamed bench in a field cannot be typed at, and guessing a name
    for it is inventing data.
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return None
    record: Dict[str, object] = {
        "name": cleaned,
        "key": name_key(cleaned),
        "kind": (kind or "place").strip().lower(),
        "lat": _round(lat),
        "lon": _round(lon),
    }
    for field, value in extra.items():
        if value not in (None, ""):
            record[field] = _round(value) if isinstance(value, float) else value
    return record


# -----------------------------------------------------------------------------
# What a search can find, and what each thing is called
# -----------------------------------------------------------------------------

# The tag that decides what a thing *is*, tried in this order.  The order is part
# of the answer: a `place=village` that also carries `amenity=…` is a village,
# because that is the word a person types, and a `shop=hairdresser` inside
# `building=yes` is a hairdresser.
POI_KEYS: Tuple[str, ...] = ("place", "amenity", "shop", "tourism", "leisure",
                             "railway", "aeroway", "highway", "man_made", "historic",
                             "natural", "office", "healthcare")

# A way is asked a shorter question.  A road is not somewhere a person types to go:
# street names are already in `graph.json`, where the router needs them, and
# repeating 1,269 residential ways in `places.json` would bury the villages.
WAY_POI_KEYS: Tuple[str, ...] = ("amenity", "shop", "tourism", "leisure", "railway",
                                 "aeroway", "office", "healthcare", "historic", "man_made")

# Values that name a category rather than a thing.  `highway=yes` is not a bus stop,
# and a search result called "Yes" costs a person a tap to learn the box guessed.
POI_REFUSE = frozenset(("yes", "no", "true", "1", "0", "false"))

# Plain words for the things this box is likely to hold.  Anything absent falls back
# to the tag value with its underscores opened out, so `boundary_stone` still reads
# as "Boundary stone" without a table entry for every tag in OpenStreetMap.
KIND_LABELS: Dict[str, str] = {
    "city": "City",
    "town": "Town",
    "village": "Village",
    "hamlet": "Hamlet",
    "suburb": "Suburb",
    "quarter": "Quarter",
    "neighbourhood": "Neighbourhood",
    "isolated_dwelling": "Isolated house",
    "locality": "Place name",
    "bus_stop": "Bus stop",
    "bus_station": "Bus station",
    "rail": "Railway station",
    "station": "Station",
    "halt": "Station",
    "tram_stop": "Tram stop",
    "parking": "Car park",
    "fuel": "Fuel station",
    "charging_station": "Charging point",
    "bicycle_parking": "Bicycle parking",
    "restaurant": "Restaurant",
    "cafe": "Cafe",
    "fast_food": "Takeaway",
    "bar": "Bar",
    "pub": "Pub",
    "bakery": "Bakery",
    "supermarket": "Supermarket",
    "convenience": "Shop",
    "kiosk": "Kiosk",
    "hotel": "Hotel",
    "guest_house": "Guest house",
    "camp_site": "Campsite",
    "place_of_worship": "Place of worship",
    "school": "School",
    "kindergarten": "Kindergarten",
    "university": "University",
    "pharmacy": "Pharmacy",
    "hospital": "Hospital",
    "clinic": "Clinic",
    "doctors": "Doctor",
    "dentist": "Dentist",
    "police": "Police",
    "fire_station": "Fire station",
    "post_office": "Post office",
    "bank": "Bank",
    "atm": "Cash machine",
    "toilets": "Toilets",
    "drinking_water": "Drinking water",
    "shelter": "Shelter",
    "bench": "Bench",
    "picnic_table": "Picnic table",
    "pitch": "Sports pitch",
    "playground": "Playground",
    "sports_centre": "Sports centre",
    "swimming_pool": "Swimming pool",
    "museum": "Museum",
    "viewpoint": "Viewpoint",
    "attraction": "Attraction",
    "artwork": "Public artwork",
    "information": "Information point",
    "fountain": "Fountain",
    "peak": "Summit",
    "spring": "Spring",
    "company": "Business",
    "industry": "Works",
    "boundary_stone": "Boundary stone",
    "memorial": "Memorial",
    "monument": "Monument",
    "tower": "Tower",
}

# Settlements are asked for by name far more often than anything else, so they come
# first in a list of answers.  Everything else shares one rank: a bus stop 40 m away
# is worth more than a village across the country, and the browser breaks ties by
# distance, so inventing finer ranks here would only hide that.
PLACE_RANK: Dict[str, int] = {"city": 0, "town": 1, "village": 2, "hamlet": 3,
                              "suburb": 4, "quarter": 5, "neighbourhood": 5,
                              "isolated_dwelling": 6, "locality": 7}
POI_RANK = 20


def kind_label(value: str) -> str:
    """The plain words for a tag value, e.g. Bus stop, or empty when there are none."""
    word = (value or "").strip().lower()
    if not word:
        return ""
    found = KIND_LABELS.get(word)
    if found:
        return found
    return word.replace("_", " ").replace("-", " ").strip().capitalize()


def search_rank(value: str) -> int:
    return PLACE_RANK.get((value or "").strip().lower(), POI_RANK)


def poi_kind(tags: Dict[str, str],
             chain: Sequence[str] = POI_KEYS) -> Tuple[str, str]:
    """`(tag value, plain words)` for what this thing is, or `("", "")`."""
    for key in chain:
        value = (tags.get(key) or "").strip().lower()
        if not value or value in POI_REFUSE:
            continue
        words = kind_label(value)
        if words:
            return value, words
    return "", ""


# -----------------------------------------------------------------------------
# Drawing layers — what the region looks like from the sky
# -----------------------------------------------------------------------------
#
# A drawing layer is a file the browser asks for by name, so the set is fixed by
# `LAYERS` and one function decides which file a way goes in.  The road layers are
# decided by the *same* `road_class()` the router uses, and that is the point: a way
# that is drawn and a way that can be routed must not disagree, or the map shows a
# street a car cannot enter and hides one it can.  A private driveway carrying
# `access=private` is therefore not drawn at all — it is not in the graph, and
# drawing it would promise a road the first tap refuses.

# Water as a shape.  An empty tuple means "any value of this key": `water=lake` and
# `water=pond` are both water without anybody maintaining a list of ponds.
WATER_TAGS: Dict[str, Tuple[str, ...]] = {
    "natural": ("water",),
    "water": (),
    "landuse": ("reservoir", "basin"),
    "waterway": ("riverbank", "dock"),
}

# Water as a line — a river too narrow to be a shape.
WATER_LINE = frozenset(("river", "stream", "canal", "drain", "ditch"))

FOREST_TAGS: Dict[str, Tuple[str, ...]] = {
    "landuse": ("forest",),
    "natural": ("wood",),
}

BOUNDARY_TAGS: Dict[str, Tuple[str, ...]] = {
    "boundary": ("administrative", "national_park", "protected_area"),
}


def tags_match(tags: Dict[str, str], table: Dict[str, Tuple[str, ...]]) -> bool:
    """Does any key in `table` hold one of its values?  Case and space are forgiven."""
    for key, values in table.items():
        got = (tags.get(key) or "").strip().lower()
        if got and (not values or got in values):
            return True
    return False


def is_water(tags: Dict[str, str], closed: bool = False) -> bool:
    """A lake is a closed shape; a river is a line.  Both are water."""
    if closed:
        return tags_match(tags, WATER_TAGS)
    return (tags.get("waterway") or "").strip().lower() in WATER_LINE


def is_forest(tags: Dict[str, str], closed: bool = False) -> bool:
    """A forest is a shape.  An open way tagged `landuse=forest` is a broken ring,
    and filling it would paint a triangle across the region."""
    return bool(closed) and tags_match(tags, FOREST_TAGS)


def is_boundary(tags: Dict[str, str]) -> bool:
    return tags_match(tags, BOUNDARY_TAGS)


def is_building(tags: Dict[str, str], closed: bool = False) -> bool:
    """A building footprint, if the way is a closed shape and says it is one."""
    value = (tags.get("building") or "").strip().lower()
    return bool(closed) and bool(value) and value != "no"


def layer_of(tags: Dict[str, str], closed: bool = False) -> str:
    """Which drawing layer this way belongs in, or `''` when it is not drawn."""
    if is_water(tags, closed):
        return "water"
    if is_forest(tags, closed):
        return "forest"
    if is_boundary(tags):
        return "boundary"
    if is_building(tags, closed):
        return "buildings"
    klass = road_class(tags)
    if not klass:
        return ""
    return "base-major" if klass in MAJOR_CLASSES else "base-minor"


def centroid(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """The middle of a shape — the point a search result points at.

    The mean of the shape's own points, not an area-weighted centroid: it costs one
    pass and no geometry, and for a lake or a hospital grounds it lands inside the
    shape far more often than a bug-free centroid does for a crescent.  It is a
    label position, and the panel always draws the shape itself beside it.
    """
    if not points:
        return (0.0, 0.0)
    total_lat = 0.0
    total_lon = 0.0
    for lat, lon in points:
        total_lat += float(lat)
        total_lon += float(lon)
    return (total_lat / len(points), total_lon / len(points))


# -----------------------------------------------------------------------------
# Building one region — the graph, the pictures and the names, in one pass
# -----------------------------------------------------------------------------
#
# A pack's three products have to agree with each other, so one object builds all
# three rather than three scripts reading the same file: the routing graph, the
# drawn layers, and the list of names a person can type at.  If they are built
# apart they drift — a road drawn but unroutable, a lake at the wrong scale, a name
# with no point to fly to — and a drift is invisible until someone is standing in
# the rain looking at a street the map believes in.


class PackBuilder(object):
    """One region's three products, accumulated as the source is read once.

    Feed it OSM objects; each `add_*()` answers `True` when the object was used, so
    a host script can report what it could not use without guessing.  `finish()`
    answers everything `write_pack()` needs.

    Nothing here reads a file, spawns a process or knows what pyosmium is — a test
    drives it with a handful of made-up ways, and the host builder
    (`scripts/ark-map-pack`) drives it with a real .osm.pbf.  That is what keeps
    this module importable on the box, where no PBF reader is installed.
    """

    def __init__(self, max_points: int = MAX_GRAPH_POINTS,
                 max_places: int = MAX_PLACES,
                 max_buildings: int = MAX_BUILDINGS,
                 min_water_area: float = 0.0,
                 named_water_lines_only: bool = False) -> None:
        self.graph = Graph()
        self.layers: Dict[str, List[Dict[str, object]]] = {name: [] for name in LAYERS}
        self.places: List[Dict[str, object]] = []
        self.max_points = int(max_points)
        self.max_places = int(max_places)
        self.max_buildings = int(max_buildings)
        self.min_water_area = float(min_water_area or 0.0)
        self.named_water_lines_only = bool(named_water_lines_only)
        self._seen: set = set()
        self._box: Optional[List[float]] = None   # [south, west, north, east]
        self.stats = {
            "ways": 0, "roads": 0, "routable_ways": 0, "refused_ways": 0,
            "other_ways": 0, "drawn": 0, "areas": 0, "points": 0,
            "places": 0, "repeats": 0, "places_dropped": 0,
            "buildings": 0, "buildings_dropped": 0,
            "water_dropped": 0,
            "restrictions": 0, "restrictions_skipped": 0,
        }

    # -- what the map opens on ------------------------------------------------

    def _stretch(self, lat: float, lon: float) -> None:
        """Grow the box the map opens on.  Every object contributes, drawn or not,
        so an area outside the road network cannot fall off the first view."""
        if self._box is None:
            self._box = [float(lat), float(lon), float(lat), float(lon)]
            return
        box = self._box
        box[0] = min(box[0], lat)
        box[1] = min(box[1], lon)
        box[2] = max(box[2], lat)
        box[3] = max(box[3], lon)

    def bounds(self) -> List[float]:
        """`[south, west, north, east]` — measured, never assumed."""
        if self._box:
            return list(self._box)
        return self.graph.bounds()

    # -- names a person can type at -------------------------------------------

    def _keep(self, record: Optional[Dict[str, object]]) -> bool:
        """One searchable place, once.  A name and a point are the whole identity:
        the same hospital tagged on both a node and the building it stands in is
        one hospital on a phone's screen."""
        if not record:
            return False
        try:
            mark = (record.get("key"), record.get("kind"),
                    round(float(record.get("lat") or 0.0), 5),
                    round(float(record.get("lon") or 0.0), 5))
        except (TypeError, ValueError):
            return False
        if mark in self._seen:
            self.stats["repeats"] += 1
            return False
        if len(self.places) >= self.max_places:
            self.stats["places_dropped"] += 1
            return False
        self._seen.add(mark)
        self.places.append(record)
        self.stats["places"] = len(self.places)
        return True

    def add_point(self, osm_id: int, tags: Dict[str, str],
                  lat: float, lon: float) -> bool:
        """A single OSM node: somewhere to search for, and nothing else."""
        self.stats["points"] += 1
        value, words = poi_kind(tags)
        if not value:
            return False
        name = " ".join((tags.get("name") or "").split())
        if not name:
            return False       # a place with no name is not a place to search
        self._stretch(lat, lon)
        return self._keep(place(name, value, lat, lon, label=words,
                                rank=search_rank(value), id=int(osm_id)))

    # -- ways: the roads a car may use, and the shapes a map may draw ----------

    def add_way(self, osm_id: int, tags: Dict[str, str],
                nodes: Sequence[Tuple[int, float, float]]) -> bool:
        """One OSM way, given its nodes as `(node id, lat, lon)` in OSM order.

        A way is looked at three ways and none of them may refuse the others: it may
        be a road the router can use, a shape the map can draw, and a place a person
        can search for.  Most ways are none of the three (a fence, a bench) and cost
        one dictionary lookup each.
        """
        self.stats["ways"] += 1
        if len(nodes) < 2:
            return False
        closed = len(nodes) >= 4 and nodes[0][0] == nodes[-1][0]
        points = [(float(lat), float(lon)) for _nid, lat, lon in nodes]
        name = " ".join((tags.get("name") or "").split())
        used = False

        # 1. the router.  Only a routable way registers nodes, so the graph holds
        #    street junctions rather than every corner of every building.
        if tags.get("highway"):
            self.stats["roads"] += 1
            if is_routable(tags):
                if len(self.graph.lat) + len(nodes) > self.max_points:
                    raise ValueError(
                        "this region needs more than %d points, which is more than "
                        "one phone's pack can carry — split it into smaller regions"
                        % self.max_points)
                index = [self.graph.node(int(nid), float(lat), float(lon))
                         for nid, lat, lon in nodes]
                if self.graph.add_way(index, tags, way_id=osm_id):
                    self.stats["routable_ways"] += 1
                    used = True
                else:
                    self.stats["refused_ways"] += 1
            else:
                self.stats["refused_ways"] += 1
        else:
            self.stats["other_ways"] += 1

        # 2. the picture.  The road layers answer to the same class table as the
        #    router, so what is drawn and what can be driven cannot disagree.
        value, words = poi_kind(tags, WAY_POI_KEYS)
        layer = layer_of(tags, closed)
        if layer == "buildings" and len(self.layers["buildings"]) >= self.max_buildings:
            self.stats["buildings_dropped"] += 1
            layer = ""
        if (layer == "water" and closed and self.min_water_area > 0
                and not name and ring_area_m2(points) < self.min_water_area):
            # A state is full of unnamed stock tanks; at this scale they are noise
            # and megabytes.  A named lake or reservoir is always kept.
            self.stats["water_dropped"] += 1
            layer = ""
        elif (layer == "water" and not closed and self.named_water_lines_only
                and not name):
            # Every stream is cut into dozens of ways; the unnamed ones are the
            # font of a state's water layer and draw nothing a traveller can name.
            self.stats["water_dropped"] += 1
            layer = ""
        if layer:
            props = self._properties(tags, name, value, words)
            feature = None
            if closed and layer in ("water", "forest", "boundary", "buildings"):
                feature = polygon_feature(points, props)
            else:
                feature = line_feature(points, props)
            if feature:
                self.layers[layer].append(feature)
                self.stats["drawn"] += 1
                used = True
                if layer == "buildings":
                    self.stats["buildings"] += 1
                if closed and layer in ("water", "forest"):
                    self.stats["areas"] += 1

        # 3. the search.  A lake, a park, a hospital campus: the shape is drawn and
        #    the name must be typeable, or a person can see it and not find it.
        if value and name:
            lat, lon = centroid(points)
            if self._keep(place(name, value, lat, lon, label=words,
                                rank=search_rank(value), id=int(osm_id))):
                used = True
        # Only an object that contributes stretches the view: an extract can
        # carry a long-distance relation's members from far outside the region
        # (a power line's towers 2,000 km away), and one of those must not open
        # the map — or fail the verifier — on a region it was never part of.
        if used:
            for _nid, lat, lon in nodes:
                self._stretch(lat, lon)
        return used

    # -- turn restrictions ----------------------------------------------------

    def add_restriction(self, members: Sequence[Tuple[str, int, str]],
                        tags: Dict[str, str]) -> bool:
        """One OSM `type=restriction` relation, if it can be carried honestly.

        Only a restriction with `from` and `to` ways and a `via` **node** is
        stored.  A `via` way is a more complicated shape (the restriction covers a
        whole stretch), and pretending a node restriction means the same thing
        would forbid turns the survey permits — so it is counted and left out, and
        the manifest says how many were left out.
        """
        if (tags.get("type") or "").strip().lower() != "restriction":
            return False
        from_way = to_way = 0
        via_node = None
        via_way = False
        # OSM readers say `node`/`way`; pyosmium's member list says `n`/`w`.
        # Both mean the same thing and neither is worth a second caller.
        short = {"n": "node", "w": "way", "r": "relation"}
        for role, ref, kind in members:
            role = (role or "").strip().lower()
            kind = short.get((kind or "").strip().lower(),
                             (kind or "").strip().lower())
            if kind == "way" and role == "from":
                from_way = int(ref)
            elif kind == "way" and role == "to":
                to_way = int(ref)
            elif kind == "node" and role == "via":
                via_node = int(ref)
            elif kind == "way" and role == "via":
                via_way = True
        if via_way or not from_way or not to_way or via_node is None:
            self.stats["restrictions_skipped"] += 1
            return False
        index = self.graph.refs.get(via_node)
        if index is None:
            # The junction is not in the graph (one of the ways was refused), so a
            # restriction there has nothing to restrict.
            self.stats["restrictions_skipped"] += 1
            return False
        except_modes = 0
        for word in (tags.get("except") or "").replace(";", ",").split(","):
            word = word.strip().lower()
            if word == "motorcar" or word == "motor_vehicle":
                except_modes |= MODE_CAR
            elif word == "bicycle":
                except_modes |= MODE_BIKE
            elif word == "foot":
                except_modes |= MODE_FOOT
        allowed = ALL_MODES & ~except_modes
        added = False
        for key, mask in (("restriction", ALL_MODES),
                          ("restriction:motorcar", MODE_CAR),
                          ("restriction:motor_vehicle", MODE_CAR),
                          ("restriction:bicycle", MODE_BIKE),
                          ("restriction:foot", MODE_FOOT)):
            value = (tags.get(key) or "").strip().lower()
            if not value:
                continue
            # A conditional restriction ("no_left_turn @ (Mo-Fr 07:00-09:00)")
            # is a timetable, and this pack has no clock.  Leave it out.
            if "@" in value or "conditional" in value:
                self.stats["restrictions_skipped"] += 1
                continue
            kind = TURN_ONLY if value.startswith("only_") else TURN_NO
            if self.graph.add_turn(from_way, index, to_way, kind, mask & allowed):
                added = True
        if added:
            self.stats["restrictions"] = len(self.graph.turns)
        return added

    @staticmethod
    def _properties(tags: Dict[str, str], name: str, value: str,
                    words: str) -> Dict[str, object]:
        """What the browser needs to draw and name one feature — and no more.

        A whole OSM tag dictionary per feature is the difference between a 900 kB
        layer and a 20 MB one, on a phone, over a hotspot.
        """
        props: Dict[str, object] = {}
        if name:
            props["name"] = name
        klass = road_class(tags)
        if klass:
            props["class"] = klass
        building = (tags.get("building") or "").strip().lower()
        if building and building != "no":
            props["kind"] = "building"
            props["label"] = "Building"
        elif value:
            props["kind"] = value
            props["label"] = words
        return props

    # -- what a pack is made of ------------------------------------------------

    def finish(self) -> Dict[str, object]:
        """Everything `write_pack()` needs, and a count of what was left out."""
        layers = {name: feature_collection(features)
                  for name, features in self.layers.items()}
        return {"graph": self.graph, "layers": layers,
                "places": list(self.places), "bounds": self.bounds(),
                "stats": dict(self.stats)}


def build_from_source(source, max_points: int = MAX_GRAPH_POINTS,
                      max_places: int = MAX_PLACES,
                      max_buildings: int = MAX_BUILDINGS) -> Dict[str, object]:
    """Drive a `PackBuilder` from anything that can offer OSM objects.

    `source` has three methods, in this shape::

        points()       yields  (osm_id, tags, lat, lon)
        ways()         yields  (osm_id, tags, [(node id, lat, lon), ...])
        relations()    yields  (osm_id, tags, [(role, ref, "node"|"way"), ...])

    Three methods rather than one pass, because every reader of an .osm.pbf reads
    nodes, ways and relations differently (pyosmium included), and a host script
    that rereads a file has lost nothing at all.  What this function will not do
    is import pyosmium: it runs on the box in the test suite, where nothing can
    open a PBF.  `relations()` is optional so a small made-up source can leave it
    out.
    """
    builder = PackBuilder(max_points=max_points, max_places=max_places,
                          max_buildings=max_buildings)
    for osm_id, tags, lat, lon in source.points():
        builder.add_point(osm_id, tags or {}, lat, lon)
    for osm_id, tags, nodes in source.ways():
        builder.add_way(osm_id, tags or {}, nodes)
    relations = getattr(source, "relations", None)
    if callable(relations):
        for _osm_id, tags, members in relations():
            builder.add_restriction(members, tags or {})
    return builder.finish()


def _hex64(text: str) -> str:
    candidate = (text or "").strip().lower()
    if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate):
        return ""
    return candidate


def build_pack(dest: str, source, *, source_url: str, region_name: str,
               pack_version: str, data_date: str,
               region: str = "", source_path: str = "", source_sha256: str = "",
               source_bytes: int = 0, notes: str = "", built: str = "",
               limitations: str = "", min_zoom: int = 6, max_zoom: int = 17,
               pack_id: str = "",
               max_points: int = MAX_GRAPH_POINTS,
               max_places: int = MAX_PLACES,
               max_buildings: int = MAX_BUILDINGS) -> Dict[str, object]:
    """Read a source once and write one finished pack — the whole job in one call.

    `write_pack()` is the authority on what a pack must carry and re-checks every
    refusal itself; the checks below are repeated here for one reason only: they are
    cheap, and they happen **before** the source is read.  A host script handed a
    wrong URL should learn that in a second, not after parsing a whole country.

        built = ark_maps.build_pack("/tmp/pack", source,
                                    source_url="https://…/liechtenstein.osm.pbf",
                                    region_name="Liechtenstein", region="LI",
                                    pack_version="2026-11-01",
                                    data_date="2026-11-01",
                                    source_path="/tmp/arkmaps/liechtenstein.osm.pbf")

    Answers `{"manifest": …, "stats": …}`.  `manifest` is exactly the file on disk —
    nothing is added to it afterwards, because a manifest edited in memory after it
    was written is a second version of the truth.  `stats` is what the builder made
    of the source: ways read, roads routed, objects drawn, places kept, repeats and
    dropped places, so a host script can say what it could not use instead of
    guessing.
    """
    url = (source_url or "").strip()
    if not url:
        raise ValueError("a map pack must say where its data came from: no source "
                         "URL was given")
    label = (region_name or "").strip()
    if not label:
        raise ValueError("a map pack must say which region it covers: no region name "
                         "was given")
    if not _VERSION_RE.match((pack_version or "").strip()):
        raise ValueError("a map pack needs an immutable version made of letters, "
                         "digits, dots, dashes or underscores: %r" % (pack_version,))
    if not _DATE_RE.match((data_date or "").strip()):
        raise ValueError("a map pack must record the date of the data it was built "
                         "from, as YYYY-MM-DD: %r" % (data_date,))
    digest = _hex64(source_sha256)
    if not digest and source_path:
        digest = fingerprint(source_path)
    if not digest:
        raise ValueError("a map pack must carry the sha256 of the file it was built "
                         "from: give source_path or source_sha256")
    size = int(source_bytes or 0)
    if not size and source_path:
        size = os.path.getsize(source_path)

    made = build_from_source(source, max_points=max_points, max_places=max_places,
                             max_buildings=max_buildings)
    manifest = write_pack(dest, graph=made["graph"], places=made["places"],
                          layers=made["layers"], bounds=made["bounds"],
                          source_url=url, source_sha256=digest,
                          region_name=label, region=region,
                          pack_version=pack_version, data_date=data_date,
                          source_bytes=size, source_path=source_path,
                          notes=notes, built=built, limitations=limitations,
                          min_zoom=min_zoom, max_zoom=max_zoom, pack_id=pack_id)
    return {"manifest": manifest, "stats": dict(made["stats"])}


def write_pack(dest: str, *, graph: "Graph", places: Sequence[Dict[str, object]] = (),
               layers: Optional[Dict[str, Sequence[Dict[str, object]]]] = None,
               source_url: str, source_sha256: str,
               region_name: str = "", region: str = "", bounds: Optional[Sequence[float]] = None,
               pack_version: str = "", data_date: str = "",
               source_bytes: int = 0, source_path: str = "",
               notes: str = "", built: str = "", limitations: str = "",
               min_zoom: int = 6, max_zoom: int = 17,
               pack_id: str = "") -> Dict[str, object]:
    """Write one complete map pack into `dest` and answer its manifest.

    Refusals happen **before** a byte is written, and each one names the thing
    that is missing rather than writing a folder that looks finished and is not:

    * no source URL, or a source fingerprint that is not 64 hex characters —
      a pack that cannot say where it came from is not a pack we may hand out;
    * `source_path` given and its sha256 disagreeing with `source_sha256` — the
      pack would then carry a fingerprint of some *other* download;
    * a graph with no points — a pack nobody can route on;
    * a layer name that is not in `LAYERS` — a file the browser never asks for;
    * a region name, version or data date that is missing or malformed.

    `manifest.json` is written **last**, so a folder that stopped halfway has no
    manifest and is therefore visibly incomplete rather than plausibly complete.
    An old manifest is removed first: a rebuild that stops halfway must not leave
    the *previous* manifest describing files that have already been replaced.
    """
    url = (source_url or "").strip()
    if not url:
        raise ValueError("a map pack must say where its data came from: no source "
                         "URL was given")
    digest = _hex64(source_sha256)
    if not digest:
        raise ValueError("a map pack must carry the sha256 of the file it was built "
                         "from: %r is not 64 hex characters" % (source_sha256,))
    if source_path:
        found = fingerprint(source_path)
        if found != digest:
            raise ValueError("the source file is %s and the pack was told %s — one of "
                             "them is the wrong download" % (found, digest))
    if not graph.node_count():
        raise ValueError("this graph has no points: there is nothing to route on")
    wanted = dict(layers or {})
    unknown = sorted(set(wanted) - set(LAYERS))
    if unknown:
        raise ValueError("these are not drawing layers this pack defines: %s (a pack "
                         "holds %s)" % (", ".join(unknown), ", ".join(LAYERS)))
    label = (region_name or "").strip()
    if not label:
        raise ValueError("a map pack must say which region it covers: no region name "
                         "was given")
    version = (pack_version or "").strip()
    if not _VERSION_RE.match(version):
        raise ValueError("a map pack needs an immutable version made of letters, "
                         "digits, dots, dashes or underscores: %r" % (pack_version,))
    if not _DATE_RE.match((data_date or "").strip()):
        raise ValueError("a map pack must record the date of the data it was built "
                         "from, as YYYY-MM-DD: %r" % (data_date,))
    ident = (pack_id or "").strip() or slug(label)
    if not _SLUG_RE.match(ident):
        raise ValueError("a map pack's identifier must be lower-case letters, digits "
                         "and dashes: %r" % (ident,))
    low, high = int(min_zoom), int(max_zoom)
    if not (0 <= low <= high <= 22):
        raise ValueError("zoom coverage must be 0…22 with min <= max: %r…%r"
                         % (min_zoom, max_zoom))
    folder = os.path.abspath(dest)
    os.makedirs(folder, exist_ok=True)
    kept = [record for record in places if record]
    written: Dict[str, Dict[str, object]] = {}

    def publish(name: str, payload: bytes) -> None:
        written[name] = {"bytes": _write_bytes(os.path.join(folder, name), payload),
                         "sha256": hashlib.sha256(payload).hexdigest()}

    publish("graph.bin", graph.to_bytes())
    publish("graph.json", json.dumps(
        graph.to_json(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))
    publish("places.json", json.dumps(
        {"format": "ark-map-places", "version": ENGINE["places"],
         "count": len(kept), "places": kept},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    layer_counts: Dict[str, int] = {}
    for name in LAYERS:
        given = wanted.get(name)
        if isinstance(given, dict):
            body = given
        else:
            body = feature_collection(given or ())
        features = body.get("features") or []
        layer_counts[name] = len(features)
        publish("%s.geojson" % name, json.dumps(
            body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8"))
    publish("attribution.txt", ("%s\n%s\n" % (ATTRIBUTION, ATTRIBUTION_URL)).encode("utf-8"))

    stamp = (built or "").strip() or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    heading = list(bounds) if bounds is not None else graph.bounds()
    if len(heading) != 4:
        heading = graph.bounds()
    # A manifest is the only proof a folder is finished, so an old one is removed
    # first: a rebuild that stops halfway must not leave the *previous* manifest
    # describing files that have already been replaced.
    manifest_path = os.path.join(folder, "manifest.json")
    if os.path.exists(manifest_path):
        os.remove(manifest_path)
    modes_present = sorted(
        profile for profile, bit in MODE_BITS.items()
        if any(edge[5] & bit for edges in graph.out for edge in edges))
    kinds = sorted({str(record.get("kind")) for record in kept if record.get("kind")})
    total = sum(int(written[name]["bytes"]) for name in written)
    manifest: Dict[str, object] = {
        "format": PACK_FORMAT,
        "schema": PACK_SCHEMA,
        "id": ident,
        "version": version,
        "app": dict(APP_COMPAT),
        "engine": dict(ENGINE),
        "region": {"name": label, "code": (region or "").strip(),
                   "bounds": [_round(value) for value in heading]},
        "coverage": {"bounds": [_round(value) for value in heading],
                     "limitations": (limitations or "").strip()},
        "source": {"url": url, "sha256": digest,
                   "bytes": int(source_bytes or 0),
                   "data_date": data_date.strip()},
        "licence": LICENCE,
        "attribution": {"text": ATTRIBUTION, "url": ATTRIBUTION_URL},
        "capabilities": {
            "map": True,
            "search": True,
            "routing": modes_present,
            "turn_restrictions": bool(graph.turns),
            "addresses": False,
        },
        "map": {"min_zoom": low, "max_zoom": high,
                "layers": list(LAYERS), "required_layers": list(REQUIRED_LAYERS),
                "counts": {name: layer_counts[name] for name in LAYERS}},
        "search": {"count": len(kept), "kinds": kinds[:40],
                   "addresses": False,
                   "coverage": "named places and points of interest; street names "
                               "are in the routing graph"},
        "graph": {"nodes": graph.node_count(), "edges": graph.edge_count(),
                  "names": len(graph.names), "stats": graph.stats(),
                  "turn_restrictions": len(graph.turns),
                  "turn_kinds": "no_* and only_* with a via node"},
        "places": {"count": len(kept), "file": "places.json"},
        "files": [[name, written[name]["bytes"], written[name]["sha256"]]
                  for name in sorted(written)],
        "total_bytes": total,
        "installed_bytes": total,
        "built": stamp,
        "notes": (notes or "").strip(),
    }
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _write_bytes(manifest_path, text.encode("utf-8"))
    return manifest


# -----------------------------------------------------------------------------
# Reading a pack back — proving it without a browser
# -----------------------------------------------------------------------------


def read_manifest(folder: str) -> Dict[str, object]:
    """The manifest of a finished pack, or a refusal naming what is wrong."""
    path = os.path.join(os.path.abspath(folder), "manifest.json")
    if not os.path.exists(path):
        raise ValueError("this folder has no manifest.json, so it is not a finished "
                         "pack: %s" % (folder,))
    data = read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("manifest.json is not an object")
    if data.get("format") != PACK_FORMAT:
        raise ValueError("manifest.json says this is %r, not an ark map pack"
                         % (data.get("format"),))
    if data.get("schema") != PACK_SCHEMA:
        raise ValueError("this pack uses schema %r and this box reads schema %d"
                         % (data.get("schema"), PACK_SCHEMA))
    return data


def _bounds_problems(bounds: object) -> List[str]:
    if not (isinstance(bounds, (list, tuple)) and len(bounds) == 4):
        return ["the coverage bounds are not four numbers"]
    try:
        south, west, north, east = (float(v) for v in bounds)
    except (TypeError, ValueError):
        return ["the coverage bounds are not four numbers"]
    found = []
    if not (-MAX_ABS_LAT <= south <= north <= MAX_ABS_LAT):
        found.append("the south/north bounds are not a real latitude range")
    if not (-MAX_ABS_LON <= west <= east <= MAX_ABS_LON):
        found.append("the west/east bounds are not a real longitude range")
    if (north - south) > 20 or (east - west) > 30:
        found.append("these bounds cover more than a regional extract should; "
                     "build a smaller region")
    return found


def manifest_problems(manifest: Dict[str, object]) -> List[str]:
    """Everything wrong with a manifest, as sentences a person can act on.

    This is the check the card's importer runs before it lets a folder into the
    published area, and the one the browser repeats on the manifest it downloaded:
    both need the same answer, so it lives here rather than in either caller.
    """
    found: List[str] = []
    if not isinstance(manifest, dict):
        return ["the manifest is not an object"]
    if manifest.get("format") != PACK_FORMAT:
        found.append("this is not an ark map pack")
    if manifest.get("schema") != PACK_SCHEMA:
        found.append("the pack schema is %r and this box reads %d"
                     % (manifest.get("schema"), PACK_SCHEMA))
    ident = str(manifest.get("id") or "")
    if not _SLUG_RE.match(ident):
        found.append("the pack identifier is missing or not a safe folder name")
    version = str(manifest.get("version") or "")
    if not _VERSION_RE.match(version):
        found.append("the pack version is missing or not a safe name")
    app = manifest.get("app")
    if not isinstance(app, dict) or not isinstance(app.get("min"), int) \
            or not isinstance(app.get("max"), int) or app["min"] > app["max"]:
        found.append("the application compatibility range is missing or malformed")
    engine = manifest.get("engine")
    if not isinstance(engine, dict) or engine.get("graph") != GRAPH_VERSION:
        found.append("this pack was built for graph engine %r and this box reads %d"
                     % ((engine or {}).get("graph") if isinstance(engine, dict) else None,
                        GRAPH_VERSION))
    region = manifest.get("region")
    if not isinstance(region, dict) or not str(region.get("name") or "").strip():
        found.append("the pack does not name its region")
    bounds = (manifest.get("coverage") or {}).get("bounds") \
        if isinstance(manifest.get("coverage"), dict) else None
    found.extend(_bounds_problems(bounds))
    source = manifest.get("source")
    if not isinstance(source, dict):
        found.append("the pack does not say where its data came from")
    else:
        if not str(source.get("url") or "").strip():
            found.append("the pack has no source URL")
        if not _HEX64_RE.match(str(source.get("sha256") or "")):
            found.append("the pack has no valid source fingerprint")
        if not _DATE_RE.match(str(source.get("data_date") or "")):
            found.append("the pack does not record the date of its data")
    if manifest.get("licence") != LICENCE:
        found.append("the pack does not carry the OpenStreetMap licence")
    attribution = manifest.get("attribution")
    if not isinstance(attribution, dict) or not str(attribution.get("text") or "").strip():
        found.append("the pack does not carry its attribution sentence")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict):
        found.append("the pack does not say what it can do")
    else:
        modes = capabilities.get("routing")
        if not isinstance(modes, list) or not modes \
                or any(mode not in MODE_BITS for mode in modes):
            found.append("the pack's routing modes are missing or unknown")
    for key in ("map", "search", "graph"):
        if not isinstance(manifest.get(key), dict):
            found.append("the pack does not describe its %s" % (key,))
    files = manifest.get("files")
    if not isinstance(files, list):
        found.append("the pack does not list its files")
        files = []
    seen = set()
    for row in files:
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            found.append("the pack lists a file in a shape this box cannot read")
            continue
        name, size, digest = str(row[0]), row[1], str(row[2])
        if os.path.basename(name) != name or name in ("", ".", "..", "manifest.json"):
            found.append("%r is not a bare file name a pack may carry" % (name,))
            continue
        if name in seen:
            found.append("%s is listed twice" % (name,))
        seen.add(name)
        if name not in PACK_FILES:
            found.append("%s is not a file this pack format defines" % (name,))
        if not isinstance(size, int) or size < 0:
            found.append("%s has no valid size" % (name,))
        if not _HEX64_RE.match(digest):
            found.append("%s has no valid fingerprint" % (name,))
    for name in PACK_FILES:
        if name != "manifest.json" and name not in seen:
            found.append("the manifest does not name %s" % (name,))
    total = manifest.get("total_bytes")
    if not isinstance(total, int) or total <= 0 or total > MAX_PACK_BYTES:
        found.append("the pack's total size is missing or outside what a pack may be")
    return found


def verify_pack(folder: str) -> Dict[str, object]:
    """Check every byte of a pack against its manifest — the app's `--verify`.

    Answers `{"ok": bool, "problems": [...], "manifest": {...}, "bytes": n}`.  A
    problem is a sentence a person can act on (a file missing, a file whose
    fingerprint moved, a graph the layout cannot parse, a layer the manifest
    counts but the file does not hold), never a stack trace: this is the check
    that has to pass before a pack is copied onto a phone.
    """
    root = os.path.abspath(folder)
    report: Dict[str, object] = {"ok": False, "folder": root, "problems": [],
                                 "bytes": 0, "manifest": None}
    problems: List[str] = report["problems"]  # type: ignore[assignment]
    try:
        manifest = read_manifest(root)
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
        return report
    report["manifest"] = manifest
    problems.extend(manifest_problems(manifest))
    listed = {}
    for row in manifest.get("files") or []:
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            continue
        listed[str(row[0])] = (int(row[1]), str(row[2]))
    total = 0
    for name, (size, digest) in sorted(listed.items()):
        path = os.path.join(root, name)
        if not os.path.exists(path):
            problems.append("%s is missing" % (name,))
            continue
        actual = os.path.getsize(path)
        total += actual
        if size and actual != size:
            problems.append("%s is %d bytes and the manifest says %d"
                            % (name, actual, size))
        found = fingerprint(path)
        if found != digest:
            problems.append("%s does not match its fingerprint (%s… vs %s…)"
                            % (name, found[:12], digest[:12]))
    report["bytes"] = total
    if isinstance(manifest.get("total_bytes"), int) and total != manifest["total_bytes"]:
        problems.append("the pack is %d bytes on disk and its manifest says %d"
                        % (total, manifest["total_bytes"]))
    problems.extend(_verify_graph_files(root))
    problems.extend(_verify_places(root, manifest))
    report["ok"] = not problems
    return report


def _verify_graph_files(root: str) -> List[str]:
    """The graph half: a `graph.bin` the layout parses and `graph.json` agreeing."""
    problems: List[str] = []
    binary = os.path.join(root, "graph.bin")
    meta = os.path.join(root, "graph.json")
    if not (os.path.exists(binary) and os.path.exists(meta)):
        return problems
    try:
        with open(binary, "rb") as handle:
            graph = read_graph_bytes(handle.read())
    except (OSError, ValueError) as exc:
        return ["graph.bin cannot be read: %s" % (exc,)]
    problems.extend("graph.bin: %s" % (line,) for line in graph.problems())
    try:
        described = read_json_file(meta)
    except (OSError, ValueError) as exc:
        return problems + ["graph.json cannot be read: %s" % (exc,)]
    if not isinstance(described, dict):
        return problems + ["graph.json is not an object"]
    # `graph.json`'s "names" is the *list* of street names, not a count, so it is
    # compared by length — comparing the list against the header's number would
    # fail every pack ever written.
    counted = {"nodes": graph.nodes, "edges": graph.edges,
               "names": graph.name_count}
    for field, actual in (("nodes", graph.nodes), ("edges", graph.edges)):
        wanted = described.get(field)
        if wanted != actual:
            problems.append("graph.json says %s is %r and graph.bin holds %d"
                            % (field, wanted, actual))
    listed_names = described.get("names")
    if listed_names is not None and len(listed_names) != counted["names"]:
        problems.append("graph.json lists %d street names and graph.bin holds %d"
                        % (len(listed_names), counted["names"]))
    if described.get("modes") != MODE_BITS:
        problems.append("graph.json does not describe the routing modes this reader uses")
    # A turn restriction that names a node or a way the graph never carries is a
    # rule about somewhere else; it is harmless but it means the builder and the
    # graph disagree, so it is worth refusing publication over.
    turns = described.get("turns")
    if turns is not None:
        if not isinstance(turns, list):
            problems.append("graph.json's turn restrictions are not a list")
        else:
            for row in turns[:1000]:
                if not (isinstance(row, list) and len(row) == 5):
                    problems.append("a turn restriction is not five numbers")
                    break
                _from, via, _to, kind, modes = row
                if not (0 <= int(via) < graph.nodes) or int(kind) not in (TURN_NO, TURN_ONLY) \
                        or not (0 < int(modes) <= ALL_MODES):
                    problems.append("a turn restriction points somewhere this graph "
                                    "does not have")
                    break
    return problems


def _verify_places(root: str, manifest: Dict[str, object]) -> List[str]:
    """The search half: a places file whose count matches the manifest."""
    problems: List[str] = []
    path = os.path.join(root, "places.json")
    if not os.path.exists(path):
        return problems
    try:
        body = read_json_file(path)
    except (OSError, ValueError) as exc:
        return ["places.json cannot be read: %s" % (exc,)]
    if not isinstance(body, dict) or not isinstance(body.get("places"), list):
        return ["places.json does not hold a list of places"]
    places = body["places"]
    wanted = ((manifest.get("search") or {}) if isinstance(manifest.get("search"), dict) else {}).get("count")
    if isinstance(wanted, int) and wanted != len(places):
        problems.append("places.json holds %d places and the manifest says %d"
                        % (len(places), wanted))
    for record in places[:500]:
        if not isinstance(record, dict):
            problems.append("places.json holds an entry that is not an object")
            break
        if not str(record.get("name") or "").strip() or not str(record.get("key") or "").strip():
            problems.append("a place in places.json has no name or no search key")
            break
        try:
            lat, lon = float(record.get("lat")), float(record.get("lon"))
        except (TypeError, ValueError):
            problems.append("a place in places.json has no usable point")
            break
        if not (-MAX_ABS_LAT <= lat <= MAX_ABS_LAT and -MAX_ABS_LON <= lon <= MAX_ABS_LON):
            problems.append("a place in places.json is outside the real world")
            break
    return problems


def pack_summary(folder: str) -> Dict[str, object]:
    """A catalogue row for one pack folder — no byte is fingerprinted here.

    `verify_pack()` is the expensive check; this is the cheap description a page
    or a catalogue can build for every pack on the card without reading gigabytes
    on every request.
    """
    manifest = read_manifest(folder)
    problems = manifest_problems(manifest)
    region = manifest.get("region") or {}
    source = manifest.get("source") or {}
    return {
        "id": manifest.get("id"),
        "version": manifest.get("version"),
        "name": region.get("name") if isinstance(region, dict) else "",
        "code": region.get("code") if isinstance(region, dict) else "",
        "bounds": (manifest.get("coverage") or {}).get("bounds")
        if isinstance(manifest.get("coverage"), dict) else [],
        "data_date": source.get("data_date") if isinstance(source, dict) else "",
        "built": manifest.get("built"),
        "bytes": manifest.get("total_bytes"),
        "capabilities": manifest.get("capabilities") or {},
        "map": manifest.get("map") or {},
        "search": manifest.get("search") or {},
        "app": manifest.get("app") or {},
        "licence": manifest.get("licence"),
        "attribution": manifest.get("attribution") or {},
        "problems": problems,
    }
