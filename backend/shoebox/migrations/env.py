from alembic import context
from sqlmodel import SQLModel

from shoebox import models  # noqa: F401  (registers tables)
from shoebox.db import get_engine

target_metadata = SQLModel.metadata


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        # Batch mode rebuilds a table (copy, drop, rename) to change it. With foreign keys
        # enforced, dropping a table that other rows reference fails, so turn enforcement off
        # for the upgrade (it can't change inside a transaction) and check afterwards instead.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:  # the pre-upgrade snapshot in BACKUP_ROOT/db is the way back
                    raise RuntimeError(f"upgrade left {len(broken)} broken reference(s), e.g. {broken[:3]}")
            connection.commit()
        finally:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


run_migrations_online()
