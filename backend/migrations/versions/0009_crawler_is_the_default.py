"""the crawler is the default indexing source

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0009_crawler_is_the_default.sql")


def downgrade() -> None:
    run_sql(
        "UPDATE feature_flags SET value = '\"mtproto\"'"
        " WHERE key = 'indexing_source' AND value = '\"crawler\"';"
        "INSERT INTO feature_flags (key, value, description) VALUES"
        " ('lazy_resolve', 'false', 'Unused since ADR-003') ON CONFLICT (key) DO NOTHING;"
    )
