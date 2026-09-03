"""CSV export of the registry, for the office's own analysis.

Why this exists: the dashboard answers the daily operational questions, but a
commissioner's office also needs the raw rows - to put in a note, to reconcile
against a contractor's bill, or to open in Excel. Doing that by hand out of the
database needs a DBA; doing it here needs a browser.

Three rules govern every export in this module:

1. **Bulk PII extraction is a privileged act.** `field_officer` may register and
   look up animals all day, but may not walk away with the whole owner list;
   `viewer` (the CM/press role) may not read a single personal record at all.
   Export is therefore limited to super_admin, ulb_admin and auditor.
2. **Scope still applies.** A `ulb_admin` exports their own ULB and nothing else,
   exactly as on screen. Filters can narrow that further; they can never widen it.
3. **Every export is recorded** in `export_log`, which is append-only and carried
   into the same hash chain as the rest of the audit trail. A registry where
   someone can quietly take a copy of every phone number is not auditable.
"""
from __future__ import annotations

import csv
import datetime as dt
import enum
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import Principal, client_ip, get_principal
from authz import apply_ulb_scope, require_roles
from db import get_db
from models import Animal, Event, ExportLog, Fine, Owner, Role, Shelter

router = APIRouter(prefix="/api/export", tags=["export"])

#: Roles allowed to take a bulk copy of records (see rule 1 above).
EXPORT_ROLES = {Role.super_admin, Role.ulb_admin, Role.auditor}

#: A single export is capped so that one request cannot pull the whole database
#: into memory (and so a leaked session cannot quietly drain it in one call).
MAX_ROWS = 100_000


def _csv_safe(value: object) -> str:
    """Render a value for CSV, defusing spreadsheet formula injection.

    A cell beginning with = + - or @ is executed as a formula by Excel and by
    LibreOffice. Owner names and notes are attacker-supplied free text, so a
    keeper called `=HYPERLINK(...)` would otherwise run on the clerk's machine
    that opens the file. Prefixing with an apostrophe makes it literal text.
    """
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        # str(KeeperType.household) is "KeeperType.household"; the file must say
        # "household", which is what every other consumer of this data expects.
        value = value.value
    out = str(value)
    if out and out[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + out
    return out


# --------------------------------------------------------------------- datasets
# Each dataset: the ORM class, the ULB column to scope on, the date column that
# `from`/`to` filter on, and the columns exported (never password hashes, never
# session tokens, never the phone HMAC - see the omissions noted per dataset).
DATASETS: dict[str, dict] = {
    "owners": {
        "model": Owner,
        "ulb_col": lambda: Owner.ulb_id,
        "date_col": lambda: Owner.created_at,
        # phone_hash is deliberately NOT exported: it is SHA-256 over
        # SECRET_KEY|phone (see sync.phone_hash), used for duplicate detection,
        # and shipping it in a spreadsheet spreads a cryptographic artefact for
        # no analytical gain.
        "columns": [
            "id", "ulb_id", "name", "relation_name", "phone_norm", "address",
            "ward_or_village", "keeper_type", "id_type", "id_last4",
            # Survey figures the office asked for: what the keeper declared, and
            # how big the premises is.  Compared against the animals actually
            # registered, the first is the under-declaration signal.
            "self_declared_cattle_count", "premises_area_sq_yards",
            "lat", "lng", "notes", "merged_into", "created_at", "updated_at",
        ],
    },
    "animals": {
        "model": Animal,
        "ulb_col": lambda: Animal.ulb_id,
        "date_col": lambda: Animal.created_at,
        "columns": [
            "id", "ulb_id", "owner_id", "species", "sex", "age_class",
            # age_years is the keeper-stated number; age_class stays the bucket
            # the dashboards aggregate on.  Both ship, because a clerk
            # reconciling a gaushala bill needs the number, not the bucket.
            "age_years", "breed", "colour_markings",
            "identification_mark_1", "identification_mark_2",
            "tag_id", "tag_type", "secondary_tag_id", "status",
            "current_shelter_id", "lat", "lng", "created_at", "updated_at",
        ],
    },
    "events": {
        "model": Event,
        "ulb_col": lambda: Event.ulb_id,
        "date_col": lambda: Event.occurred_at,
        "columns": [
            "id", "seq", "type", "animal_id", "owner_id", "ulb_id", "user_id",
            "lat", "lng", "gps_accuracy_m", "occurred_at", "received_at", "payload",
        ],
    },
    "fines": {
        "model": Fine,
        "ulb_col": lambda: Fine.ulb_id,
        "date_col": lambda: Fine.issued_at,
        "columns": [
            "id", "event_id", "animal_id", "owner_id", "ulb_id", "offence_number",
            "amount", "status", "receipt_no", "issued_at", "paid_at", "authority_ref",
        ],
    },
    "shelters": {
        "model": Shelter,
        "ulb_col": lambda: Shelter.ulb_id,
        "date_col": None,  # shelters have no meaningful date to slice on
        "columns": [
            "id", "ulb_id", "name", "kind", "capacity", "current_count",
            "lat", "lng", "phone",
        ],
    },
}


@router.get("/{dataset}.csv")
def export_csv(
    dataset: str,
    request: Request,
    ulb: int | None = Query(default=None, description="restrict to one ULB (must be in scope)"),
    date_from: dt.date | None = Query(default=None, alias="from"),
    date_to: dt.date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Download one dataset as CSV, sliced by ULB and date.

    Examples the office will actually use:
      /api/export/fines.csv?from=2026-04-01&to=2026-07-31   (the contractor quarter)
      /api/export/animals.csv?ulb=1                          (Rewari MC only)
    """
    spec = DATASETS.get(dataset)
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown dataset")

    scope = principal.scope
    require_roles(scope, EXPORT_ROLES)

    model = spec["model"]
    stmt = select(model)

    # scoped query - a ulb_admin cannot widen beyond their own ULB (rule 2)
    stmt = apply_ulb_scope(stmt, spec["ulb_col"](), scope)
    if ulb is not None:
        if scope.ulb_ids is not None and ulb not in scope.ulb_ids:
            raise HTTPException(status_code=403, detail="ULB not in scope")
        stmt = stmt.where(spec["ulb_col"]() == ulb)

    date_col = spec["date_col"]() if spec["date_col"] else None
    if date_col is not None:
        if date_from is not None:
            stmt = stmt.where(date_col >= dt.datetime.combine(date_from, dt.time.min))
        if date_to is not None:
            # inclusive of the whole end day
            stmt = stmt.where(date_col < dt.datetime.combine(date_to, dt.time.max))
    elif date_from or date_to:
        raise HTTPException(status_code=400, detail=f"{dataset} cannot be filtered by date")

    stmt = stmt.limit(MAX_ROWS + 1)
    rows = db.execute(stmt).scalars().all()
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]

    columns = spec["columns"]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_safe(getattr(row, c, None)) for c in columns])
    if truncated:
        # never truncate silently: the reader must know the file is partial
        writer.writerow([f"TRUNCATED at {MAX_ROWS} rows - narrow the date range and export again"])

    # rule 3: record who took what, before handing the file over
    db.add(ExportLog(
        user_id=principal.user.id,
        dataset=dataset,
        filters=f"ulb={ulb} from={date_from} to={date_to}",
        row_count=len(rows),
        ip=client_ip(request),
    ))
    db.commit()

    stamp = dt.datetime.now().strftime("%Y%m%d")
    filename = f"gautrack-{dataset}-{stamp}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),  # BOM so Excel reads UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
