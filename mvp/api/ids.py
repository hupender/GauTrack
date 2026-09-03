"""UUIDv7 (RFC 9562) — time-ordered so B-tree inserts stay local, and 74 random
bits so an id is not guessable.  Unguessability is *defence in depth only*:
every object route still loads rows through a scoped query (see authz.py)."""
from __future__ import annotations

import os
import time
import uuid

_RAND_B_MASK = (1 << 62) - 1


def uuid7() -> uuid.UUID:
    """48-bit unix-ms | ver 7 | 12 random | var 0b10 | 62 random."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0x0FFF
    rand_b = rand & _RAND_B_MASK
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)
