import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401
from app.db import Base, normalize_db_url

config = context.config
# configparser interpolates %: a URL-encoded password (p%40ss) would raise. Escape before set_main_option.
config.set_main_option("sqlalchemy.url",
                       normalize_db_url(os.environ.get("DATABASE_URL", "sqlite:///./review-api.db")).replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
