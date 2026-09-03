"""SPEC §7: photo hash mismatch -> 400, non-image -> 400.

Also checks that a photo can only be read back through an entity the caller is
allowed to see.
"""
from __future__ import annotations

import hashlib
import struct
import zlib


def _png(colour=(18, 96, 63), size=8) -> bytes:
    """A tiny, genuinely valid PNG — no image library needed."""
    raw = b"".join(bytes([0]) + bytes(colour) * size for _ in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def test_upload_with_correct_hash_succeeds(client, fixtures):
    client.login("t_rwr_field")
    data = _png()
    r = client.post_raw(
        "/api/photos",
        files={"file": ("cow.png", data, "image/png")},
        data={"sha256": hashlib.sha256(data).hexdigest()},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["mime"] == "image/png"


def test_photo_hash_mismatch_is_rejected(client, fixtures):
    client.login("t_rwr_field")
    data = _png(colour=(200, 10, 10))
    r = client.post_raw(
        "/api/photos",
        files={"file": ("cow.png", data, "image/png")},
        data={"sha256": "0" * 64},
    )
    assert r.status_code == 400
    assert "hash mismatch" in r.json()["detail"]


def test_non_image_is_rejected(client, fixtures):
    """The Content-Type the client claims is ignored; we read the magic bytes."""
    client.login("t_rwr_field")
    r = client.post_raw(
        "/api/photos",
        files={"file": ("evil.jpg", b"#!/bin/sh\nrm -rf /\n", "image/jpeg")},
    )
    assert r.status_code == 400
    assert "JPEG or PNG" in r.json()["detail"]


def test_html_disguised_as_image_is_rejected(client, fixtures):
    client.login("t_rwr_field")
    r = client.post_raw(
        "/api/photos",
        files={"file": ("x.png", b"<html><script>alert(1)</script></html>", "image/png")},
    )
    assert r.status_code == 400


def test_oversized_upload_is_rejected(client, fixtures):
    client.login("t_rwr_field")
    blob = b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024 + 10)
    r = client.post_raw("/api/photos", files={"file": ("big.jpg", blob, "image/jpeg")})
    assert r.status_code == 400
    assert "5MB" in r.json()["detail"]


def test_photo_is_only_readable_through_an_in_scope_entity(client, fixtures):
    """Upload as a Bawal officer, attach to a Bawal animal, then try to read it
    as a Rewari officer."""
    data = _png(colour=(1, 2, 3))

    client.login("t_bwl_field")
    photo_id = client.post_raw(
        "/api/photos",
        files={"file": ("c.png", data, "image/png")},
        data={"sha256": hashlib.sha256(data).hexdigest()},
    ).json()["id"]
    created = client.post(
        "/api/animals",
        json={"species": "cattle", "sex": "female", "photo_id": photo_id, "tag_id": "777777777777",
              "tag_type": "pashu_aadhaar_12"},
    )
    assert created.status_code == 201
    assert client.get(f"/api/photos/{photo_id}").status_code == 200  # uploader can read it
    client.logout()

    client.login("t_rwr_field")
    assert client.get(f"/api/photos/{photo_id}").status_code == 404, "must not leak another ULB's photo"
    client.logout()

    client.login("t_super")
    assert client.get(f"/api/photos/{photo_id}").status_code == 200
