from __future__ import annotations

from alembic import context

import app.models  # noqa: F401  - registers all tables on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(get_settings().database_url)
    with engine.connect() as conn:
        # render_as_batch lets ALTER TABLE migrations work on SQLite as well as PostgreSQL
        context.configure(
            connection=conn, target_metadata=target_metadata, render_as_batch=True, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
