"""PROJECT ARK — what a region is, once it has been folded small enough to hold.

A *map pack* is one folder on the card:

    manifest.json    what it is, where the bytes came from, every fingerprint
     graph.json      how to read graph.bin (node/edge counts, street names)
     graph.bin       the routing graph: a header, node points, an index, edges
   places.json       what can be searched for: names, kinds, points
    base-major.geojson / base-minor.geojson / water.geojson / forest.geojson /
    boundary.geojson
     attribution.txt one line that must stay on the screen

Everything here is a *format*: this module writes and reads those files and knows
nothing about OSM downloaders, browsers, nginx or the card.  The host-side
builder (`scripts/ark-map-pack`, which needs pyosmium) reads an .osm.pbf and calls
`build_graph()` / `write_pack()`; the browser reads the same bytes with
`pi/maps/web/router.js`, which knows `GRAPH_LAYOUT` by heart.

Two rules this file keeps:

* **A pack says where it came from.**  `manifest.json` carries the source URL, the
  source file's sha256 and the licence (ODbL-1.0 for OpenStreetMap data), and
  `attribution.txt` is the sentence that has to stay visible.  A pack without it
  is a pack nobody may publish.
* **Nothing here decides what runs.**  No service, no path on the live box, no
  certificate: this is data and the arithmetic that reads it.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import time
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# -----------------------------------------------------------------------------
# The graph, byte for byte
# -----------------------------------------------------------------------------
#
#   0  magic       4s   b"ARKG"
#   4  version     H    1
#   6  flags       H    0 (reserved)
#   8  nodeCount   I
#  12  edgeCount   I    directed
#  16  nameCount   I    street names, in the order graph.json lists them
#  20  reserved    I    0
#                        -> 24 bytes of header
#  24  nodes       nodeCount  * 8   int32 lat_e6, int32 lon_e6
#      offsets     (nodeCount+1) * 4 uint32, ascending: node i owns
#                                    edges[offsets[i]:offsets[i+1]]
#      edges       edgeCount  * 12  uint32 to, float32 metres, uint16 cls,
#                                    uint16 nameId (0xFFFF = no name)
#
# A node's edges are its *outgoing* edges; a two-way street therefore appears
# twice, once from each end, and the router never has to think about direction.
GRAPH_MAGIC = b"ARKG"
GRAPH_VERSION = 1
GRAPH_HEADER = struct.Struct("<4sHHIIII")
_NODE = struct.Struct("<ii")
_OFFSET = struct.Struct("<I")
_EDGE = struct.Struct("<IfHH")
NO_NAME = 0xFFFF
GRAPH_LAYOUT = (
    "header 24B (magic ARKG, version, flags, nodes, edges, names, reserved); "
    "nodes nodeCount*8B (int32 lat*1e6, int32 lon*1e6); "
    "offsets (nodeCount+1)*4B; edges edgeCount*12B (uint32 to, float32 metres, "
    "uint16 class, uint16 name)"
)

# -----------------------------------------------------------------------------
# Road classes — the numbers are frozen
# -----------------------------------------------------------------------------
#
# A class is a small integer written into every edge, and the browser's router
# turns it into a speed and a drawing weight.  The order below is therefore
# append-only: an existing class keeps its number for ever, and a class we do not
# know yet goes on the end (`ROAD_CLASSES.index` is the number).  A pack built by
# an older builder keeps working in a newer browser, which is the only reason
# this is a list and not a dict.
ROAD_CLASSES = (
    "path",       # 0  footway, steps, bridleway, pedestrian
    "track",      # 1  track, byway, service
    "residential",  # 2 residential, unclassified, living_street
    "tertiary",   # 3
    "secondary",  # 4
    "primary",    # 5
    "trunk",      # 6
    "motorway",   # 7
)

# Unknown tags on a routable way are `residential`, never `path`: a way tagged
# `highway=yes` in the wild is a road, and guessing "footway" would keep a car off
# it for no reason the data gives.
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
}

# The classes drawn on the "major" layer; everything else goes on "minor", so the
# browser can show a thin network at a low zoom and thicken it as you come in.
MAJOR_CLASSES = frozenset(("primary", "trunk", "motorway"))

# Metres per second at the class's own limit — a planning number, not a promise.
# `ark_maps` is honest about this in the pack: `manifest.json` records
# `"speeds": "class defaults, not measured"`, because a routing graph without a
# `maxspeed` survey travels at the class's speed.  Driving on a track at 16 km/h
# is a guess and it says so.
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

# What each way of travelling may use, and how fast it really goes.  The speed is
# a fraction of the class speed so one table serves every profile: a bicycle on a
# primary road does not do 70.
PROFILES: Dict[str, Dict[str, object]] = {
    "car": {
        "label": "By car",
        "classes": ("motorway", "trunk", "primary", "secondary", "tertiary",
                    "residential", "track"),
        "factor": {"motorway": 1.0, "trunk": 1.0, "primary": 1.0,
                   "secondary": 1.0, "tertiary": 1.0, "residential": 1.0,
                   "track": 0.6},
        "oneway": True,
    },
    "bike": {
        "label": "By bicycle",
        "classes": ("primary", "secondary", "tertiary", "residential", "track",
                    "path"),
        "factor": {"primary": 0.35, "secondary": 0.4, "tertiary": 0.45,
                   "residential": 0.55, "track": 0.55, "path": 0.5},
        "oneway": True,
    },
    "foot": {
        "label": "On foot",
        "classes": ("primary", "secondary", "tertiary", "residential", "track",
                    "path"),
        # Walking is walking: the class decides *whether* you may go, not how fast.
        "factor": {"primary": 1.0, "secondary": 1.0, "tertiary": 1.0,
                   "residential": 1.0, "track": 1.0, "path": 1.0},
        "oneway": False,
    },
}
FOOT_KMH = 4.8
DEFAULT_PROFILE = "car"

# A turn costs time even when the two streets are the same length.  Without it a
# router happily zig-zags through a grid because every zig saves a metre; with it
# the answer reads like something a person would drive.  Seconds, per turn kind.
TURN_PENALTY_S = 4.0
TURN_PENALTY_UTURN_S = 20.0


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

# A way carrying any of these is not a way to travel on.  `construction` is
# checked too: a road being built is not a road, and routing through one is how a
# directions list sends somebody into a hole in the ground.
_REFUSE_ACCESS = frozenset((
    "no", "private", "agricultural", "forestry", "military", "customers",
))

# `highway=construction` / `proposed` / `abandoned` and friends are not roads yet.
_REFUSE_HIGHWAY = frozenset(("construction", "proposed", "abandoned", "razed",
                             "disused", "platform", "elevator"))


def is_routable(tags: Dict[str, str]) -> bool:
    """Is this way something the graph should carry?

    Deliberately conservative: a way we leave out is a missing street on a map,
    while a way we wrongly include sends somebody through a locked gate or into a
    building site.  Both are wrong; only one is dangerous.
    """
    highway = (tags.get("highway") or "").strip().lower()
    if not highway or highway not in _ROUTABLE_HIGHWAY:
        return False
    if highway in _REFUSE_HIGHWAY:
        return False
    for key in ("access", "vehicle", "motor_vehicle", "motorcar"):
        if (tags.get(key) or "").strip().lower() in _REFUSE_ACCESS:
            return False
    if (tags.get("area") or "").strip().lower() == "yes":
        return False
    if (tags.get("service") or "").strip().lower() == "parking_aisle":
        # A car park's aisles are a maze, not a network: keeping them adds edges
        # that are never the right answer and make the graph read as denser than
        # the streets really are.
        return False
    return True


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
    """Direction of travel: `1` forward, `-1` backward, `0` both ways.

    `-1` is the cheapest honest way to draw and route a reversed one-way street:
    the builder still emits the way's points in the order OSM gives them, and this
    number says which way round they may be used.
    """
    raw = (tags.get("oneway") or "").strip().lower()
    if raw in ("yes", "true", "1"):
        return 1
    if raw in ("-1", "reverse"):
        return -1
    if raw in ("no", "false", "0"):
        return 0
    # A roundabout is one-way unless it says otherwise, and that is the one
    # implicit case worth honouring — a two-way roundabout is a mistake in the
    # data and driving it both ways is a mistake on the road.
    if (tags.get("junction") or "").strip().lower() == "roundabout":
        return 1
    if (tags.get("highway") or "").strip().lower() in ("motorway", "motorway_link"):
        return 1
    return 0


def speed_ms(class_name_: str, profile: str = DEFAULT_PROFILE) -> float:
    """Metres per second for a class under a profile — the router's cost basis."""
    if profile == "foot":
        return FOOT_KMH / 3.6
    scale = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
    factor = float((scale["factor"] or {}).get(class_name_, 1.0))
    return (SPEEDS_KMH.get(class_name_, 30.0) * factor) / 3.6


# -----------------------------------------------------------------------------
# The pack itself
# -----------------------------------------------------------------------------
PACK_VERSION = 1

# The licence and the sentence that must stay on the screen.  A pack without
# these is a pack nobody may publish, so `write_pack()` refuses without them
# rather than writing a folder that looks complete and is not lawful to carry.
LICENCE = "ODbL-1.0"
ATTRIBUTION = (
    "Map data \u00a9 OpenStreetMap contributors, available under the Open "
    "Database Licence (ODbL 1.0)."
)
ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"

# Drawn layers, in the order they are stacked.  One GeoJSON file each, so the
# browser fetches only what it is about to draw and a thin network is a small
# file at a wide zoom.
LAYERS = ("base-major", "base-minor", "water", "forest", "boundary")

PACK_FILES = ("manifest.json", "graph.json", "graph.bin", "places.json",
              "attribution.txt") + tuple("%s.geojson" % name for name in LAYERS)


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
        self.out: List[List[Tuple[int, float, int, int]]] = []
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

    def add_way(self, points: Sequence[int], tags: Dict[str, str]) -> bool:
        """Add one way as edges.  Returns False when the way is not routable.

        `points` are already-resolved node indexes in OSM order.  A way with fewer
        than two points adds nothing: a single point is not a street.
        """
        klass = road_class(tags)
        if not klass:
            self.skipped += 1
            return False
        if len(points) < 2:
            return False
        direction = oneway(tags)
        cls = class_id(klass)
        nid = self.name_id(tags.get("name") or tags.get("ref") or "")
        for a, b in zip(points, points[1:]):
            if a == b:
                continue
            metres = haversine_m(self.lat[a], self.lon[a], self.lat[b], self.lon[b])
            if metres < 0.5:
                # Two nodes a few centimetres apart are a survey artefact, not a
                # street: they add an edge that costs nothing and make a directions
                # list say "turn left" twice in the same instant.
                continue
            if direction >= 0:
                self.out[a].append((b, metres, cls, nid))
            if direction <= 0:
                self.out[b].append((a, metres, cls, nid))
            if direction != 0:
                self.oneway_edges += 1
        self.ways += 1
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
            for to, metres, cls, nid in self.out[index]:
                _EDGE.pack_into(body, at * _EDGE.size, to, float(metres), cls, nid)
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
            "profiles": {key: {"label": PROFILES[key]["label"]}
                         for key in sorted(PROFILES)},
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

    def point(self, index: int) -> Tuple[float, float]:
        lat_e6, lon_e6 = _NODE.unpack_from(self.payload, self.at_nodes + index * _NODE.size)
        return lat_e6 / 1e6, lon_e6 / 1e6

    def offsets(self, index: int) -> Tuple[int, int]:
        start = _OFFSET.unpack_from(self.payload, self.at_offsets + index * _OFFSET.size)[0]
        end = _OFFSET.unpack_from(self.payload, self.at_offsets + (index + 1) * _OFFSET.size)[0]
        return start, end

    def edge(self, number: int) -> Tuple[int, float, int, int]:
        return _EDGE.unpack_from(self.payload, self.at_edges + number * _EDGE.size)

    def problems(self) -> List[str]:
        """Everything wrong with the file a reader can see, as sentences.

        An empty list is the only healthy answer.  These are the checks the
        verifier runs: an edge that points past the last point, offsets that do
        not ascend, a name id out of range, a length that is not a number.
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
            to, metres, cls, nid = self.edge(number)
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


def layer_of(tags: Dict[str, str], closed: bool = False) -> str:
    """Which drawing layer this way belongs in, or `''` when it is not drawn."""
    if is_water(tags, closed):
        return "water"
    if is_forest(tags, closed):
        return "forest"
    if is_boundary(tags):
        return "boundary"
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

# A cap, so a mis-typed region fails loudly on the host instead of producing a file
# no phone can hold.  Liechtenstein measures 107,366 routable nodes; 400,000 is
# roughly four of it, and a whole country would refuse here with a sentence rather
# than fill the card.
MAX_GRAPH_POINTS = 400000
MAX_PLACES = 40000


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
                 max_places: int = MAX_PLACES) -> None:
        self.graph = Graph()
        self.layers: Dict[str, List[Dict[str, object]]] = {name: [] for name in LAYERS}
        self.places: List[Dict[str, object]] = []
        self.max_points = int(max_points)
        self.max_places = int(max_places)
        self._seen: set = set()
        self._box: Optional[List[float]] = None   # [south, west, north, east]
        self.stats = {
            "ways": 0, "roads": 0, "routable_ways": 0, "refused_ways": 0,
            "other_ways": 0, "drawn": 0, "areas": 0, "points": 0,
            "places": 0, "repeats": 0, "places_dropped": 0,
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
        self._stretch(lat, lon)
        value, words = poi_kind(tags)
        if not value:
            return False
        name = " ".join((tags.get("name") or "").split())
        if not name:
            return False       # a place with no name is not a place to search
        return self._keep(place(name, value, lat, lon, label=words,
                                rank=search_rank(value), id=int(osm_id)))

    # -- ways: the roads a car may use, and the shapes a map may draw ----------

    def add_way(self, osm_id: int, tags: Dict[str, str],
                nodes: Sequence[Tuple[int, float, float]]) -> bool:
        """One OSM way, given its nodes as `(node id, lat, lon)` in OSM order.

        A way is looked at three ways and none of them may refuse the others: it may
        be a road the router can use, a shape the map can draw, and a place a person
        can search for.  Most ways are none of the three (a building, a fence, a
        bench) and cost one dictionary lookup each.
        """
        self.stats["ways"] += 1
        if len(nodes) < 2:
            return False
        for _nid, lat, lon in nodes:
            self._stretch(lat, lon)
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
                if self.graph.add_way(index, tags):
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
        if layer:
            props = self._properties(tags, name, value, words)
            feature = None
            if closed and layer in ("water", "forest", "boundary"):
                feature = polygon_feature(points, props)
            else:
                feature = line_feature(points, props)
            if feature:
                self.layers[layer].append(feature)
                self.stats["drawn"] += 1
                used = True
                if closed and layer in ("water", "forest"):
                    self.stats["areas"] += 1

        # 3. the search.  A lake, a park, a hospital campus: the shape is drawn and
        #    the name must be typeable, or a person can see it and not find it.
        if value and name:
            lat, lon = centroid(points)
            if self._keep(place(name, value, lat, lon, label=words,
                                rank=search_rank(value), id=int(osm_id))):
                used = True
        return used

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
        if value:
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
                      max_places: int = MAX_PLACES) -> Dict[str, object]:
    """Drive a `PackBuilder` from anything that can offer OSM objects.

    `source` has two methods, in this shape::

        points()  yields  (osm_id, tags, lat, lon)
        ways()    yields  (osm_id, tags, [(node id, lat, lon), ...])

    Two methods rather than one pass, because every reader of an .osm.pbf reads
    nodes and ways differently (pyosmium included), and a host script that rereads a
    3.5 MB file has lost nothing at all.  What this function will not do is import
    pyosmium: it runs on the box in the test suite, where nothing can open a PBF.
    """
    builder = PackBuilder(max_points=max_points, max_places=max_places)
    for osm_id, tags, lat, lon in source.points():
        builder.add_point(osm_id, tags or {}, lat, lon)
    for osm_id, tags, nodes in source.ways():
        builder.add_way(osm_id, tags or {}, nodes)
    return builder.finish()


def build_pack(dest: str, source, *, source_url: str, region_name: str,
               region: str = "", source_path: str = "", source_sha256: str = "",
               source_bytes: int = 0, notes: str = "", built: str = "",
               max_points: int = MAX_GRAPH_POINTS,
               max_places: int = MAX_PLACES) -> Dict[str, object]:
    """Read a source once and write one finished pack — the whole job in one call.

    `write_pack()` is the authority on what a pack must carry and re-checks every
    refusal itself; the checks below are repeated here for one reason only: they are
    cheap, and they happen **before** the source is read.  A host script handed a
    wrong URL should learn that in a second, not after parsing a whole country.

        built = ark_maps.build_pack("/tmp/pack", source,
                                    source_url="https://…/liechtenstein.osm.pbf",
                                    region_name="Liechtenstein", region="LI",
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
    digest = _hex64(source_sha256)
    if not digest and source_path:
        digest = fingerprint(source_path)
    if not digest:
        raise ValueError("a map pack must carry the sha256 of the file it was built "
                         "from: give source_path or source_sha256")
    size = int(source_bytes or 0)
    if not size and source_path:
        size = os.path.getsize(source_path)

    made = build_from_source(source, max_points=max_points, max_places=max_places)
    manifest = write_pack(dest, graph=made["graph"], places=made["places"],
                          layers=made["layers"], bounds=made["bounds"],
                          source_url=url, source_sha256=digest,
                          region_name=label, region=region,
                          source_bytes=size, source_path=source_path,
                          notes=notes, built=built)
    return {"manifest": manifest, "stats": dict(made["stats"])}



def _hex64(text: str) -> str:
    candidate = (text or "").strip().lower()
    if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate):
        return ""
    return candidate


def write_pack(dest: str, *, graph: "Graph", places: Sequence[Dict[str, object]] = (),
               layers: Optional[Dict[str, Sequence[Dict[str, object]]]] = None,
               source_url: str, source_sha256: str,
               region_name: str = "", region: str = "", bounds: Optional[Sequence[float]] = None,
               source_bytes: int = 0, source_path: str = "",
               notes: str = "", built: str = "") -> Dict[str, object]:
    """Write one complete map pack into `dest` and answer its manifest.

    Refusals happen **before** a byte is written, and each one names the thing
    that is missing rather than writing a folder that looks finished and is not:

    * no source URL, or a source fingerprint that is not 64 hex characters —
      a pack that cannot say where it came from is not a pack we may hand out;
    * `source_path` given and its sha256 disagreeing with `source_sha256` — the
      pack would then carry a fingerprint of some *other* download;
    * a graph with no points — a pack nobody can route on;
    * a layer name that is not in `LAYERS` — a file the browser never asks for.

    `manifest.json` is written **last**, so a folder that stopped halfway has no
    manifest and is therefore visibly incomplete rather than plausibly complete.
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
    # The region's name is checked here, with the other refusals, and not beside
    # the manifest that records it: the docstring above promises that nothing is
    # written when a pack is refused, and a check that runs after `graph.bin` has
    # been published makes that sentence false.
    label = (region_name or "").strip()
    if not label:
        raise ValueError("a map pack must say which region it covers: no region name "
                         "was given")
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
        {"format": "ark-map-places", "version": PACK_VERSION,
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
    manifest: Dict[str, object] = {
        "format": "ark-map-pack",
        "version": PACK_VERSION,
        "region": {"name": label, "slug": slug(label),
                   "code": (region or "").strip(),
                   "bounds": [_round(value) for value in heading]},
        "source": {"url": url, "sha256": digest,
                   "bytes": int(source_bytes or 0)},
        "licence": LICENCE,
        "attribution": {"text": ATTRIBUTION, "url": ATTRIBUTION_URL},
        "graph": {"nodes": graph.node_count(), "edges": graph.edge_count(),
                  "names": len(graph.names), "stats": graph.stats()},
        "places": {"count": len(kept), "file": "places.json"},
        "layers": {"order": list(LAYERS),
                   "counts": {name: layer_counts[name] for name in LAYERS}},
        "files": [[name, written[name]["bytes"], written[name]["sha256"]]
                  for name in sorted(written)],
        "total_bytes": sum(int(written[name]["bytes"]) for name in written),
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
    if data.get("format") != "ark-map-pack":
        raise ValueError("manifest.json says this is %r, not an ark map pack"
                         % (data.get("format"),))
    if data.get("version") != PACK_VERSION:
        raise ValueError("this pack is version %r and this box reads version %d"
                         % (data.get("version"), PACK_VERSION))
    return data


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
    listed = {}
    for row in manifest.get("files") or []:
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            problems.append("the manifest lists a file in a shape this box cannot read")
            continue
        listed[str(row[0])] = (int(row[1]), str(row[2]))
    for name in PACK_FILES:
        if name == "manifest.json":
            continue
        if name not in listed:
            problems.append("the manifest does not name %s" % (name,))
    total = 0
    for name, (size, digest) in sorted(listed.items()):
        path = os.path.join(root, name)
        if os.path.basename(name) != name or name in ("", ".", ".."):
            problems.append("%r is not a bare file name, so this pack is refused"
                            % (name,))
            continue
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
    graph_problems = _verify_graph_files(root)
    problems.extend(graph_problems)
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
    places = os.path.join(root, "places.json")
    if os.path.exists(places):
        try:
            body = read_json_file(places)
        except (OSError, ValueError) as exc:
            problems.append("places.json cannot be read: %s" % (exc,))
        else:
            if not isinstance(body, dict) or not isinstance(body.get("places"), list):
                problems.append("places.json does not hold a list of places")
    return problems
