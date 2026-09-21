"""reference data

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0002_reference_data.sql")


def downgrade() -> None:
    run_sql(
        "TRUNCATE plans, channel_categories, settings, feature_flags, payment_providers, "
        "artists RESTART IDENTITY CASCADE"
    )
