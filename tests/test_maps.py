"""The map pack, the shelf, and the doors in front of them.

These tests build a pack from a handful of made-up OpenStreetMap objects.  That
is deliberate: a test that needed a country extract would be a test nobody runs,
and the format's job is to be right about *any* region, including a village with
one street, one one-way lane and one turn restriction.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

import ark_maps


class Source:
    """A tiny place: a junction, a one-way lane, a footpath and a pharmacy."""

    def points(self):
        return [
            (1, {"place": "village", "name": "Zürich"}, 47.3700, 8.5400),
            (2, {"amenity": "pharmacy", "name": "Central Pharmacy"}, 47.3740, 8.5440),
            (3, {}, 47.3710, 8.5410),
        ]

    def ways(self):
        return [
            (10, {"highway": "residential", "name": "Bahnhofstrasse"},
             [(100, 47.3700, 8.5400), (101, 47.3720, 8.5420), (102, 47.3740, 8.5440)]),
            (11, {"highway": "residential", "name": "Kirchgasse"},
             [(102, 47.3740, 8.5440), (103, 47.3760, 8.5450)]),
            (12, {"highway": "footway", "name": "River Path"},
             [(200, 47.3701, 8.5401), (201, 47.3708, 8.5408)]),
            (13, {"highway": "residential", "name": "One Way", "oneway": "yes"},
             [(300, 47.3700, 8.5400), (301, 47.3710, 8.5410)]),
            (14, {"building": "yes", "name": "Town Hall"},
             [(400, 47.3710, 8.5410), (401, 47.3710, 8.5420),
              (402, 47.3716, 8.5420), (403, 47.3716, 8.5410), (400, 47.3710, 8.5410)]),
            (15, {"natural": "water", "name": "Lake"},
             [(500, 47.3600, 8.5300), (501, 47.3600, 8.5350), (502, 47.3650, 8.5350),
              (503, 47.3650, 8.5300), (500, 47.3600, 8.5300)]),
        ]

    def relations(self):
        return [(900, {"type": "restriction", "restriction": "no_left_turn"},
                 [("from", 10, "way"), ("via", 102, "node"), ("to", 11, "way")])]


def build(dest: Path, **overrides):
    args = dict(source_url="https://example.org/region.osm.pbf",
                region_name="Test Region", region="TR", pack_version="2026-11-01",
                data_date="2026-11-01", source_sha256="a" * 64,
                built="2026-11-02T00:00:00Z", pack_id="test-region")
    args.update(overrides)
    return ark_maps.build_pack(str(dest), Source(), **args)


def zip_pack(folder: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                bundle.write(path, "%s/%s" % (folder.name, path.relative_to(folder)))
    return target


def test_pack_round_trip(tmp_path):
    folder = tmp_path / "pack"
    result = build(folder)
    manifest = result["manifest"]
    report = ark_maps.verify_pack(str(folder))
    assert report["ok"], report["problems"]
    assert manifest["schema"] == 2
    assert manifest["engine"]["graph"] == 2
    assert manifest["source"]["url"].endswith(".osm.pbf")
    assert manifest["source"]["data_date"] == "2026-11-01"
    assert manifest["app"]["min"] == 1
    assert manifest["capabilities"]["search"] is True
    assert set(manifest["capabilities"]["routing"]) == {"car", "bike", "foot"}
    assert manifest["graph"]["turn_restrictions"] == 1
    assert manifest["map"]["counts"]["water"] == 1
    assert manifest["map"]["counts"]["buildings"] == 1
    graph = ark_maps.read_graph(str(folder))
    assert graph.nodes == 8
    assert graph.edges == 10


def test_oneway_is_one_way_for_a_car_and_not_for_feet(tmp_path):
    folder = tmp_path / "pack"
    build(folder)
    graph = ark_maps.read_graph(str(folder))
    by_way = {}
    for index in range(graph.edges):
        to, metres, cls, name, way, modes = graph.edge(index)
        by_way.setdefault(way, []).append((graph_point(graph, graph_node(graph, index)), to, modes))
    # Way 13 is one segment, so it has a forward edge for all three modes and a
    # reverse edge that only feet may use.
    vehicle = [row for row in by_way[13] if row[2] & ark_maps.MODE_CAR]
    foot = [row for row in by_way[13] if row[2] & ark_maps.MODE_FOOT]
    assert len(vehicle) == 1
    assert len(foot) == 2


def graph_node(graph, edge_index):
    # A tiny helper rather than exposing the graph's internals in the test: the
    # edge's source is the node whose offsets contain it.
    for node in range(graph.nodes):
        start, end = graph.offsets(node)
        if start <= edge_index < end:
            return node
    raise AssertionError("edge has no source")


def graph_point(graph, node):
    return graph.point(node)


def test_turn_restriction_is_in_the_description(tmp_path):
    folder = tmp_path / "pack"
    build(folder)
    described = json.loads((folder / "graph.json").read_text())
    assert described["turns"] == [[10, 2, 11, ark_maps.TURN_NO, ark_maps.ALL_MODES]]
    assert described["modes"] == ark_maps.MODE_BITS


@pytest.mark.parametrize("field,value", [
    ("source_url", ""),
    ("source_sha256", "not-a-fingerprint"),
    ("pack_version", ""),
    ("data_date", "someday"),
    ("region_name", ""),
])
def test_a_pack_must_say_where_it_came_from(tmp_path, field, value):
    with pytest.raises(ValueError):
        build(tmp_path / "pack", **{field: value})


def test_a_changed_byte_is_caught(tmp_path):
    folder = tmp_path / "pack"
    build(folder)
    target = folder / "places.json"
    target.write_bytes(target.read_bytes() + b" ")
    report = ark_maps.verify_pack(str(folder))
    assert not report["ok"]
    assert any("places.json" in problem for problem in report["problems"])


def test_a_manifest_cannot_claim_another_schema(tmp_path):
    folder = tmp_path / "pack"
    build(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["schema"] = 99
    (folder / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        ark_maps.read_manifest(str(folder))


def test_import_publish_and_delete(tmp_path, ark):
    from ark_map_store import Store

    folder = tmp_path / "pack"
    build(folder)
    archive = zip_pack(folder, tmp_path / "pack.zip")
    store = Store()
    with archive.open("rb") as handle:
        row = store.import_zip(handle, filename="pack.zip")
    assert row["id"] == "test-region"
    assert row["verified"] is True
    assert row["published"] is False
    assert store.published_rows() == []

    store.publish("test-region", "2026-11-01", True)
    published = store.published_rows()
    assert len(published) == 1
    assert published[0]["id"] == "test-region"

    store.delete("test-region", "2026-11-01")
    assert store.rows() == []


def test_a_zip_cannot_escape_the_incoming_folder(tmp_path, ark):
    from ark_map_store import Refused, Store

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("pack/manifest.json", "{}")
        bundle.writestr("pack/../../escaped.txt", "no")
    store = Store()
    with archive.open("rb") as handle:
        with pytest.raises(Refused):
            store.import_zip(handle, filename="bad.zip")
    assert not (tmp_path / "escaped.txt").exists()


def test_an_unchecked_folder_is_not_offered(tmp_path, ark):
    from ark_map_store import Store

    folder = tmp_path / "pack"
    build(folder)
    destination = Store().packs / "test-region" / "2026-11-01"
    destination.parent.mkdir(parents=True)
    import shutil
    shutil.copytree(folder, destination)
    store = Store()
    row = next(row for row in store.rows() if row["id"] == "test-region")
    assert row["verified"] is False
    assert row["published"] is False
    assert store.published_rows() == []
    with pytest.raises(Exception):
        store.publish("test-region", "2026-11-01", True)


def test_catalogue_and_range_downloads(tmp_path, ark, public):
    from ark_map_store import Store

    folder = tmp_path / "pack"
    build(folder)
    archive = zip_pack(folder, tmp_path / "pack.zip")
    store = Store()
    with archive.open("rb") as handle:
        store.import_zip(handle, filename="pack.zip")
    store.publish("test-region", "2026-11-01", True)

    client = public.client
    catalog = client.get("/maps/api/catalog")
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["app_version"] == 1
    assert body["packs"][0]["id"] == "test-region"
    assert "folder" not in body["packs"][0]

    manifest = client.get("/maps/api/packs/test-region/2026-11-01/manifest.json")
    assert manifest.status_code == 200
    assert manifest.json()["id"] == "test-region"

    whole = client.get("/maps/api/packs/test-region/2026-11-01/places.json")
    assert whole.status_code == 200
    size = len(whole.content)

    part = client.get("/maps/api/packs/test-region/2026-11-01/places.json",
                      headers={"Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.content == whole.content[:10]
    assert part.headers["content-range"] == "bytes 0-9/%d" % size

    tail = client.get("/maps/api/packs/test-region/2026-11-01/places.json",
                      headers={"Range": "bytes=%d-" % (size - 5)})
    assert tail.status_code == 206
    assert tail.content == whole.content[-5:]

    past = client.get("/maps/api/packs/test-region/2026-11-01/places.json",
                      headers={"Range": "bytes=%d-" % (size + 100)})
    assert past.status_code == 416
    assert past.headers["content-range"] == "bytes */%d" % size

    head = client.head("/maps/api/packs/test-region/2026-11-01/places.json")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(size)

    store.publish("test-region", "2026-11-01", False)
    hidden = client.get("/maps/api/packs/test-region/2026-11-01/places.json")
    assert hidden.status_code == 404
    assert client.get("/maps/api/packs/test-region/2026-11-01/manifest.json").status_code == 404


def test_the_app_is_served_with_a_worker_scope(ark, public):
    client = public.client
    index = client.get("/maps/")
    assert index.status_code == 200
    assert "Project Ark" in index.text
    assert "default-src 'self'" in index.headers["content-security-policy"]
    worker = client.get("/maps/sw.js")
    assert worker.status_code == 200
    assert worker.headers["service-worker-allowed"] == "/maps/"
    assert client.get("/maps/not-a-file").status_code == 404
    assert client.get("/maps/api/packs/../etc/passwd").status_code == 404


def test_the_admin_maps_page_imports_checks_and_publishes(client, tmp_path):
    from helpers import LAN, claim_box, csrf_of, get

    first = get(client, "/admin/maps", headers=LAN)
    assert first.status_code in (303, 403)
    cookie = claim_box(client)
    page = get(client, "/admin/maps", cookie="ark_admin=%s" % cookie)
    assert page.status_code == 200
    assert "No map packs are on the Ark yet" in page.text

    folder = tmp_path / "pack"
    build(folder)
    archive = zip_pack(folder, tmp_path / "pack.zip")
    token = csrf_of(page.text)
    imported = client.post(
        "/admin/maps/import",
        data={"csrf": token},
        files={"file": ("pack.zip", archive.read_bytes(), "application/zip")},
        headers=LAN, cookies={"ark_admin": cookie}, follow_redirects=False)
    assert imported.status_code == 303, imported.text[:400]
    page = get(client, "/admin/maps", cookie="ark_admin=%s" % cookie)
    assert "test-region" in page.text
    assert "Checked, not offered" in page.text

    token = csrf_of(page.text)
    published = client.post(
        "/admin/maps/publish",
        data={"id": "test-region", "version": "2026-11-01", "on": "1", "csrf": token},
        headers=LAN, cookies={"ark_admin": cookie}, follow_redirects=False)
    assert published.status_code == 303, published.text[:400]
    page = get(client, "/admin/maps", cookie="ark_admin=%s" % cookie)
    assert "Offered to readers" in page.text
