"""discover channels by searching telegram

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-24
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0012_telegram_search.sql")


def downgrade() -> None:
    run_sql(
        "DELETE FROM channel_candidates WHERE source = 'telegram_search';"
        "ALTER TABLE channel_candidates DROP CONSTRAINT IF EXISTS channel_candidates_source_check;"
        "ALTER TABLE channel_candidates"
        "    ADD CONSTRAINT channel_candidates_source_check"
        "    CHECK (source IN ('seed', 'user', 'crawl_mention', 'crawl_forward'));"
        "DELETE FROM feature_flags WHERE key = 'telegram_search';"
        "DELETE FROM settings WHERE key = 'music_search_terms';"
    )
