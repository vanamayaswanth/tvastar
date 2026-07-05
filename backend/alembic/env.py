"""Alembic migration environment."""
from alembic import context

# ponytail: Will import SQLAlchemy metadata from adapters.postgres.models once tables are defined
target_metadata = None


def run_migrations_offline():
    context.configure(url=context.config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    raise NotImplementedError("Async migration runner TBD")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
