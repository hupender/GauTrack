"""council round-1 hardening

- fine_schedule.authority_ref / legal_status / effective_from: every fine amount must
  point at the legal instrument it rests on (RTI-proof).  fines.authority_ref copies it
  at issue time so a later schedule change cannot rewrite history.
- lookup_log: every district-wide tag lookup is recorded (who, which tag, from where)
  and hash-chained through the same audit trigger as the other tables.

Revision ID: 0002_council_hardening
Revises: 0001_init
"""
from alembic import op

from config import settings

revision = "0002_council_hardening"
down_revision = "0001_init"
branch_labels = None
depends_on = None


SQL = """
ALTER TABLE fine_schedule
    ADD COLUMN IF NOT EXISTS authority_ref  TEXT,
    ADD COLUMN IF NOT EXISTS legal_status   TEXT NOT NULL DEFAULT 'administrative_practice'
        CHECK (legal_status IN ('notified','administrative_practice','proposed')),
    ADD COLUMN IF NOT EXISTS effective_from DATE;

UPDATE fine_schedule SET
    authority_ref  = 'Haryana Govt press note (prharyana.gov.in) + Rewari MC practice; NO gazette/bye-law order located as of 2026-08-17. Confirm order no. with ULB Haryana. Note: Haryana Municipal Act 1973 s.214 caps bye-law fines at Rs 2,000 - schedule may need to be re-framed as pound/feeding/transport charges under the Cattle Trespass Act 1871.',
    legal_status   = 'administrative_practice',
    effective_from = DATE '2026-08-17'
WHERE authority_ref IS NULL;

ALTER TABLE fines
    ADD COLUMN IF NOT EXISTS authority_ref TEXT;

CREATE TABLE IF NOT EXISTS lookup_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id     UUID REFERENCES users(id),
    tag_id      TEXT NOT NULL,
    animal_id   UUID REFERENCES animals(id),
    in_scope    BOOLEAN,
    ip          TEXT
);
CREATE INDEX IF NOT EXISTS lookup_log_user_ts ON lookup_log (user_id, ts DESC);

-- chained through the same SECURITY DEFINER audit trigger as owners/animals/fines
DROP TRIGGER IF EXISTS lookup_log_audit ON lookup_log;
CREATE TRIGGER lookup_log_audit AFTER INSERT ON lookup_log
    FOR EACH ROW EXECUTE FUNCTION gt_audit();

-- lookups are append-only too
DROP TRIGGER IF EXISTS lookup_log_append_only ON lookup_log;
CREATE TRIGGER lookup_log_append_only BEFORE UPDATE OR DELETE ON lookup_log
    FOR EACH STATEMENT EXECUTE FUNCTION gt_append_only();
"""


def _grants(app_role: str) -> str:
    r = f'"{app_role}"'
    return f"""
GRANT SELECT, INSERT ON lookup_log TO {r};
REVOKE UPDATE, DELETE ON lookup_log FROM {r};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r};
"""


def upgrade() -> None:
    op.execute(SQL)
    op.execute(_grants(settings.postgres_app_user))


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS lookup_log CASCADE;
        ALTER TABLE fines DROP COLUMN IF EXISTS authority_ref;
        ALTER TABLE fine_schedule DROP COLUMN IF EXISTS authority_ref,
            DROP COLUMN IF EXISTS legal_status, DROP COLUMN IF EXISTS effective_from;
        """
    )
