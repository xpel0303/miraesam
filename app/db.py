from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 의존성: 요청마다 세션 하나."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """백그라운드 작업용 세션."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 이미 만들어진 표에 나중에 더한 칸 (create_all 은 새 표만 만들고, 있는 표에 칸을 더하지 않음)
#   (표, 칸, SQL 자료형, 기본값)
ADDED_COLUMNS = [
    ("exam_responses", "meta", "JSON", "'{}'"),  # 온라인 시험: 시작·제출 시각
]


def migrate():
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, col, typ, default in ADDED_COLUMNS:
            if table in tables and col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}"))


def init_db():
    from . import models  # noqa: F401  (모델 등록)
    Base.metadata.create_all(engine)
    migrate()
