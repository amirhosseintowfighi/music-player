"""mtproto fallback for channels without a web preview

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-22
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0010_mtproto_fallback.sql")


def downgrade() -> None:
    run_sql(
        "DROP INDEX IF EXISTS channels_mtproto_due;"
        "UPDATE channels SET source_type = 'web_preview', crawl_status = 'preview_disabled'"
        " WHERE source_type = 'mtproto';"
        "DELETE FROM feature_flags WHERE key = 'mtproto_fallback';"
    )
