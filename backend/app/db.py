"""
Base de données : SQLite par défaut (fichier data/assistant.db).
Passer à PostgreSQL = changer OPTI_DATABASE_URL, le reste du code ne bouge pas.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

is_sqlite = config.DATABASE_URL.startswith("sqlite")
if is_sqlite:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=True,
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # lectures pendant les écritures
        cur.execute("PRAGMA foreign_keys=ON")    # suppression en cascade des messages
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401  (enregistre les tables)
    Base.metadata.create_all(engine)
