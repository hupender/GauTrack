#!/usr/bin/env python3
"""Print today's chain tip — the "anchor".

Run this once a day and publish the output somewhere you do not control the
history of: a signed e-mail to the DMC and the auditor, the day's file noting,
or a printout in the register. Once a tip hash is published, nobody can later
rewrite any record created before it without the published hash disagreeing.

Sending the mail is deliberately out of scope: printing it keeps the trust chain
in a human's hands.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from sqlalchemy import create_engine, text  # noqa: E402

import audit  # noqa: E402
from config import settings  # noqa: E402


def main() -> int:
    engine = create_engine(settings.owner_database_url, future=True)
    with engine.connect() as conn:
        result = audit.verify_all(conn)
        counts = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM audit_log) AS audit_rows,"
                "       (SELECT count(*) FROM events)    AS event_rows,"
                "       (SELECT count(*) FROM audit_log WHERE ts::date = current_date) AS audit_today,"
                "       (SELECT count(*) FROM events    WHERE received_at::date = current_date) AS events_today"
            )
        ).mappings().one()

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": dt.date.today().isoformat(),
        "audit_tip_hash": result["audit_log"]["tip_hash"],
        "event_tip_hash": result["events"]["tip_hash"],
        "audit_rows": int(counts["audit_rows"]),
        "event_rows": int(counts["event_rows"]),
        "audit_rows_today": int(counts["audit_today"]),
        "events_today": int(counts["events_today"]),
        "chain_ok": result["ok"],
    }

    if "--json" in sys.argv:
        print(json.dumps(payload, indent=2))
    else:
        print("GauTrack daily anchor — Rewari district")
        print("=" * 46)
        print(f"date               : {payload['date']}")
        print(f"generated (UTC)    : {payload['generated_at']}")
        print(f"chain status       : {'OK' if payload['chain_ok'] else 'BROKEN'}")
        print(f"audit rows         : {payload['audit_rows']} (+{payload['audit_rows_today']} today)")
        print(f"event rows         : {payload['event_rows']} (+{payload['events_today']} today)")
        print()
        print(f"AUDIT TIP HASH     : {payload['audit_tip_hash']}")
        print(f"EVENT TIP HASH     : {payload['event_tip_hash']}")
        print()
        print("Publish these two hashes. Anything recorded before today can then be")
        print("proved unaltered by re-running scripts/verify_chain.py.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
