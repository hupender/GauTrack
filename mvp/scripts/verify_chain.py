#!/usr/bin/env python3
"""Walk the tamper-evident hash chains and report OK / BROKEN.

    python scripts/verify_chain.py            # both chains
    python scripts/verify_chain.py --json     # machine readable

Exit code 0 = OK, 1 = BROKEN, 2 = could not check.

What "BROKEN" means: somebody changed, inserted or removed a row directly in the
database, behind the application. It does NOT mean the application misbehaved —
the application cannot write these rows any other way.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from sqlalchemy import create_engine  # noqa: E402

import audit  # noqa: E402
from config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        engine = create_engine(settings.owner_database_url, future=True)
        with engine.connect() as conn:
            result = audit.verify_all(conn)
    except Exception as exc:  # noqa: BLE001
        print(f"could not verify: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1

    for name, key in (("audit_log", "id"), ("events", "seq")):
        part = result[name]
        status = "OK" if part["ok"] else "BROKEN"
        print(f"{name:<10} {status:<7} rows={part['rows']:<7} tip={part['tip_hash'][:16]}…")
        if not part["ok"]:
            first = part["first_broken"] or {}
            print(f"           first broken {key} = {first.get(key)}")
            print(f"           content_broken={first.get('content_broken')} link_broken={first.get('link_broken')}")
            if name == "audit_log":
                print(f"           table={first.get('table_name')} action={first.get('action')} row={first.get('row_id')}")

    print()
    print("RESULT: " + ("OK — no row has been altered." if result["ok"] else "BROKEN — the database has been tampered with."))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
