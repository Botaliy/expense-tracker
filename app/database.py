from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import models so they're registered on Base.metadata before create_all.
    from app import models  # noqa: F401
    from app.forecast import seed_default_exclusions

    # Seed only a brand-new table, so defaults the user removed stay removed.
    new_exclusions = not inspect(engine).has_table(models.ForecastExclusion.__tablename__)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    if new_exclusions:
        with Session(engine) as db:
            seed_default_exclusions(db)


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add nullable columns new models declare.

    ``create_all`` only creates missing tables, so a column added to a model
    would otherwise never reach an existing database. Only nullable columns are
    handled; anything more involved deserves a real migration tool.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"Can't auto-add NOT NULL column {table.name}.{column.name}"
                    )
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
                for index in table.indexes:
                    if column.name in index.columns:
                        index.create(conn, checkfirst=True)
