"""Run a multi-statement SQL file inside the Alembic transaction.

SQLAlchemy's asyncpg adapter always prepares statements, and a prepared statement can
only hold one command, so the file goes through the raw driver connection instead.
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

SQL_DIR = Path(__file__).parent / "sql"


def run_sql_file(name: str) -> None:
    run_sql((SQL_DIR / name).read_text(encoding="utf-8"))


def run_sql(sql: str) -> None:
    raw = op.get_bind().connection.dbapi_connection
    await_only(raw.driver_connection.execute(sql))
