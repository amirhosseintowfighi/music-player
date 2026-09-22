"""re-probe tracks with no cover

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-22
"""

from migrations.sqlrun import run_sql_file

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0011_recheck_covers.sql")


def downgrade() -> None:
    # Nothing to undo: a cleared probe timestamp only means "look again".
    pass
