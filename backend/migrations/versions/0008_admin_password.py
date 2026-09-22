"""admin password login

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-22
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0008_admin_password.sql")


def downgrade() -> None:
    run_sql(
        "DROP INDEX IF EXISTS admin_users_login_username_key;"
        "ALTER TABLE admin_users"
        "    DROP COLUMN IF EXISTS login_username,"
        "    DROP COLUMN IF EXISTS password_hash,"
        "    DROP COLUMN IF EXISTS password_set_at;"
    )
