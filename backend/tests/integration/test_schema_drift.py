"""The ORM must agree with the migrated schema (the SQL files are the source of truth)."""

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import Base


async def test_orm_matches_database(engine: AsyncEngine) -> None:
    def reflect(sync_conn):  # type: ignore[no-untyped-def]
        insp = inspect(sync_conn)
        return {t: {c["name"]: c for c in insp.get_columns(t)} for t in insp.get_table_names()}

    async with engine.connect() as conn:
        db = await conn.run_sync(reflect)

    problems = []
    for table in Base.metadata.sorted_tables:
        if table.name not in db:
            problems.append(f"missing table {table.name}")
            continue
        for col in table.columns:
            actual = db[table.name].get(col.name)
            if actual is None:
                problems.append(f"{table.name}.{col.name} missing in database")
            elif not col.primary_key and actual["nullable"] != col.nullable:
                problems.append(
                    f"{table.name}.{col.name} nullable: orm={col.nullable} db={actual['nullable']}"
                )
    assert not problems, "\n".join(problems)


async def test_migration_created_partitions(engine: AsyncEngine) -> None:
    def tables(sync_conn):  # type: ignore[no-untyped-def]
        return set(inspect(sync_conn).get_table_names())

    async with engine.connect() as conn:
        names = await conn.run_sync(tables)
    assert {"play_history_default", "audit_log_default"} <= names
    assert any(n.startswith("play_history_2") for n in names)
