from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Integer, DateTime, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

_connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class ProvisionedDevice(Base):
    __tablename__ = "provisioned_devices"

    id                     = Column(String,   primary_key=True)
    hostname               = Column(String,   unique=True, nullable=False)
    fqdn                   = Column(String,   nullable=False)
    tunnel_id              = Column(String,   nullable=False)
    tunnel_token_encrypted = Column(String,   nullable=False)     # Fernet-encrypted
    dns_record_id          = Column(String,   nullable=True)       # CF DNS record ID
    device_id              = Column(String,   nullable=False)
    activation_code        = Column(String,   nullable=False)
    status                 = Column(String,   default="active")    # pending | active | deprovisioned
    created_at             = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen              = Column(DateTime, nullable=True)
    origin_server_id       = Column(String,   nullable=True)       # server that created this record


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    code              = Column(String,   primary_key=True)
    issued_at         = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at        = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    used_at           = Column(DateTime, nullable=True)
    used_by_hostname  = Column(String,   nullable=True)
    used              = Column(Integer,  default=0)                # 0 or 1
    origin_server_id  = Column(String,   nullable=True)            # server that issued this code


class SyncPeer(Base):
    __tablename__ = "sync_peers"

    id               = Column(String,   primary_key=True)          # uuid
    url              = Column(String,   unique=True, nullable=False)
    name             = Column(String,   nullable=True)
    peer_sync_key    = Column(String,   nullable=True)             # their sync key (for calling their endpoints)
    added_at         = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_sync_at     = Column(DateTime, nullable=True)
    last_sync_status = Column(String,   default="pending")         # pending | ok | error
    last_sync_error  = Column(String,   nullable=True)
    active           = Column(Integer,  default=1)


class ServerConfig(Base):
    """Key-value store for server-level settings (e.g. server_id)."""
    __tablename__ = "server_config"

    key   = Column(String, primary_key=True)
    value = Column(String, nullable=False)


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()
    _ensure_server_id()


def _migrate():
    """Add columns introduced after initial schema creation."""
    migrations = [
        ("provisioned_devices", "dns_record_id TEXT"),
        ("provisioned_devices", "updated_at DATETIME"),
        ("provisioned_devices", "origin_server_id TEXT"),
        ("activation_codes",    "updated_at DATETIME"),
        ("activation_codes",    "origin_server_id TEXT"),
    ]
    with engine.connect() as conn:
        for table, col_def in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_def}"))
                conn.commit()
            except Exception:
                pass  # column already exists


def _ensure_server_id():
    """Generate a stable server UUID on first run."""
    import uuid
    db = SessionLocal()
    try:
        if not db.get(ServerConfig, "server_id"):
            db.add(ServerConfig(key="server_id", value=str(uuid.uuid4())))
            db.commit()
    finally:
        db.close()


def get_server_id() -> str:
    db = SessionLocal()
    try:
        row = db.get(ServerConfig, "server_id")
        return row.value if row else "unknown"
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
