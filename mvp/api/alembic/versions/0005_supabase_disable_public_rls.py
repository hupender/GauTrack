"""Disable RLS on public tables for Supabase-hosted Postgres.

Supabase enables (or expects) row-level security for API exposure. GauTrack
enforces access in application code and Postgres GRANTs/triggers, not Supabase
RLS. Without this, the app role cannot INSERT into login_attempts (and login
returns 500).

Revision ID: 0005_supabase_disable_public_rls
Revises: 0004_field_additions
"""
from __future__ import annotations

from alembic import op

revision = "0005_supabase_disable_public_rls"
down_revision = "0004_field_additions"
branch_labels = None
depends_on = None

# Drop all policies on public tables, then disable RLS (idempotent on plain Postgres).
SQL = """
DO $$
DECLARE
    pol RECORD;
    tbl RECORD;
BEGIN
    FOR pol IN
        SELECT schemaname, tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON %I.%I',
            pol.policyname, pol.schemaname, pol.tablename
        );
    END LOOP;

    FOR tbl IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY',
            tbl.tablename
        );
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    # Re-enabling RLS without policies would lock the app out; leave as no-op.
    pass
