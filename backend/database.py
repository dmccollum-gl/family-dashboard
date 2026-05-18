from sqlalchemy import create_engine, Column, String, JSON, BigInteger, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

engine = create_engine("sqlite:///./dashboard.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class UserPrefs(Base):
    __tablename__ = "user_prefs"
    email              = Column(String, primary_key=True)
    display_name       = Column(String, default="")
    display_color      = Column(String, default="#1976d2")
    selected_calendars = Column(JSON, default=list)
    access_token       = Column(String, default=None)
    refresh_token      = Column(String, default=None)
    token_expiry       = Column(BigInteger, default=None)  # ms since epoch


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """Add columns introduced after initial schema creation."""
    with engine.connect() as conn:
        for col in ("access_token TEXT", "refresh_token TEXT", "token_expiry INTEGER"):
            try:
                conn.execute(text(f"ALTER TABLE user_prefs ADD COLUMN {col}"))
                conn.commit()
            except Exception:
                pass  # column already exists


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
