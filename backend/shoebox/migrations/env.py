from alembic import context
from sqlmodel import SQLModel

from shoebox import models  # noqa: F401  (registers tables)
from shoebox.db import get_engine

target_metadata = SQLModel.metadata


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
