"""field-survey additions asked for after the first field walk-through

Three sets of columns, all optional, all captured by the field app and all
carried into the CSV exports:

- ``animals.identification_mark_1`` / ``identification_mark_2`` — natural marks
  (a horn break, a white sock, a torn ear) recorded free-text. They are the
  fallback identity when a tag is cut off, which is the tampering mode the
  council flagged as most likely. Deliberately NOT required: an officer must
  never be blocked from registering an animal because it has no distinguishing
  mark.
- ``animals.age_years`` — the keeper's stated age in years. ``age_class`` stays
  as the enum the dashboards aggregate on; this is the finer number the office
  asked to see in the CSV. Sanity-bounded 0-40 in the database so a typo of
  "1998" cannot enter the register.
- ``owners.self_declared_cattle_count`` — how many animals the keeper *says*
  they have, recorded at the door before anything is counted. The gap between
  this and the number actually registered is the audit signal: an owner who
  declares 4 and registers 2 has two animals somewhere.
- ``owners.premises_area_sq_yards`` — the size of the shed/plot in square yards
  (the unit used locally, not square metres). Feeds the capacity question: how
  many animals a premises can physically hold.

Revision ID: 0004_field_additions
Revises: 0003_exports_and_analytics
"""
from alembic import op

revision = "0004_field_additions"
down_revision = "0003_exports_and_analytics"
branch_labels = None
depends_on = None


SQL = """
ALTER TABLE animals
    ADD COLUMN IF NOT EXISTS identification_mark_1 TEXT,
    ADD COLUMN IF NOT EXISTS identification_mark_2 TEXT,
    ADD COLUMN IF NOT EXISTS age_years NUMERIC(4,1);

ALTER TABLE owners
    ADD COLUMN IF NOT EXISTS self_declared_cattle_count INTEGER,
    ADD COLUMN IF NOT EXISTS premises_area_sq_yards NUMERIC(10,2);

-- Bounds, not business rules: they exist to stop a fat-fingered entry (a year
-- typed into an age box, a negative count) reaching the register at all.
ALTER TABLE animals DROP CONSTRAINT IF EXISTS animals_age_years_sane;
ALTER TABLE animals ADD CONSTRAINT animals_age_years_sane
    CHECK (age_years IS NULL OR (age_years >= 0 AND age_years <= 40));

ALTER TABLE owners DROP CONSTRAINT IF EXISTS owners_declared_count_sane;
ALTER TABLE owners ADD CONSTRAINT owners_declared_count_sane
    CHECK (self_declared_cattle_count IS NULL
           OR (self_declared_cattle_count >= 0 AND self_declared_cattle_count <= 2000));

ALTER TABLE owners DROP CONSTRAINT IF EXISTS owners_area_sane;
ALTER TABLE owners ADD CONSTRAINT owners_area_sane
    CHECK (premises_area_sq_yards IS NULL
           OR (premises_area_sq_yards >= 0 AND premises_area_sq_yards <= 1000000));

COMMENT ON COLUMN animals.identification_mark_1 IS
    'Optional natural identifying mark; fallback identity if the tag is removed.';
COMMENT ON COLUMN animals.identification_mark_2 IS
    'Optional second natural identifying mark.';
COMMENT ON COLUMN animals.age_years IS
    'Keeper-stated age in years. age_class remains the aggregated bucket.';
COMMENT ON COLUMN owners.self_declared_cattle_count IS
    'Animals the keeper states they hold, recorded before counting. Gap vs registered = audit signal.';
COMMENT ON COLUMN owners.premises_area_sq_yards IS
    'Premises/shed area in square yards (local unit).';
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE animals
            DROP CONSTRAINT IF EXISTS animals_age_years_sane,
            DROP COLUMN IF EXISTS identification_mark_1,
            DROP COLUMN IF EXISTS identification_mark_2,
            DROP COLUMN IF EXISTS age_years;
        ALTER TABLE owners
            DROP CONSTRAINT IF EXISTS owners_declared_count_sane,
            DROP CONSTRAINT IF EXISTS owners_area_sane,
            DROP COLUMN IF EXISTS self_declared_cattle_count,
            DROP COLUMN IF EXISTS premises_area_sq_yards;
        """
    )
