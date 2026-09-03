"""export audit log + read-only analytics role

- export_log: every bulk CSV download is recorded (who, which dataset, which
  filters, how many rows, from which address) and carried into the same
  hash chain as the rest of the audit trail. Append-only, like events.
- gautrack_ro: a read-only database role for analysis tools (Power BI, Excel,
  Metabase). The role is CREATEd by `make analyst-user`, which sets a password;
  this migration only grants it privileges, and skips silently if it does not
  exist yet, so the migration is safe on a machine that never wants one.
  It is granted SELECT on operational tables only - never on users (password
  hashes), sessions (live tokens) or login_attempts.

Revision ID: 0003_exports_and_analytics
Revises: 0002_council_hardening
"""
from alembic import op

from config import settings

revision = "0003_exports_and_analytics"
down_revision = "0002_council_hardening"
branch_labels = None
depends_on = None

RO_ROLE = "gautrack_ro"

SQL = """
CREATE TABLE IF NOT EXISTS export_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id    UUID REFERENCES users(id),
    dataset    TEXT NOT NULL,
    filters    TEXT,
    row_count  INTEGER NOT NULL DEFAULT 0,
    ip         TEXT
);
CREATE INDEX IF NOT EXISTS export_log_user_ts ON export_log (user_id, ts DESC);

-- chained through the same SECURITY DEFINER audit trigger as owners/animals/fines
DROP TRIGGER IF EXISTS export_log_audit ON export_log;
CREATE TRIGGER export_log_audit AFTER INSERT ON export_log
    FOR EACH ROW EXECUTE FUNCTION gt_audit();

-- an export record must not be erasable by the account that made the export
DROP TRIGGER IF EXISTS export_log_append_only ON export_log;
CREATE TRIGGER export_log_append_only BEFORE UPDATE OR DELETE ON export_log
    FOR EACH STATEMENT EXECUTE FUNCTION gt_append_only();

-- A view for analysis tools that needs no personal data at all: this is what a
-- public or press-facing dashboard should point at.
CREATE OR REPLACE VIEW v_daily_counts AS
SELECT e.ulb_id,
       u.code                        AS ulb_code,
       e.type::text                  AS event_type,
       (e.occurred_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
       count(*)                      AS events
FROM events e JOIN ulbs u ON u.id = e.ulb_id
GROUP BY 1, 2, 3, 4;
"""


def _grants(app_role: str) -> str:
    return f"""
GRANT SELECT, INSERT ON export_log TO "{app_role}";
REVOKE UPDATE, DELETE ON export_log FROM "{app_role}";
GRANT SELECT ON v_daily_counts TO "{app_role}";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_role}";
"""


#: Granted only if the role exists. Note what is absent: users, sessions,
#: login_attempts, devices. An analyst never needs a password hash.
RO_GRANTS = f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RO_ROLE}') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA public TO {RO_ROLE}';
    EXECUTE 'GRANT SELECT ON ulbs, owners, animals, events, fines, shelters,
                              fine_schedule, v_daily_counts TO {RO_ROLE}';
    EXECUTE 'REVOKE ALL ON users, sessions, login_attempts, devices FROM {RO_ROLE}';
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(SQL)
    op.execute(_grants(settings.postgres_app_user))
    op.execute(RO_GRANTS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_daily_counts; DROP TABLE IF EXISTS export_log CASCADE;")
