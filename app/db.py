import struct
from collections.abc import Callable

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


# Dev/test equivalent of Azure SQL append-only ledger tables (see infra/sql/001_schema.sql).
SQLITE_APPEND_ONLY_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
    """,
)


SQL_COPT_SS_ACCESS_TOKEN = 1256  # msodbcsql pre-connect attribute for an Entra access token
SQL_ENTRA_SCOPE = "https://database.windows.net/.default"


def odbc_token_struct(token: str) -> bytes:
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def inject_entra_token(cparams: dict, token_provider: Callable[[], str]) -> None:
    attrs = dict(cparams.get("attrs_before") or {})
    attrs[SQL_COPT_SS_ACCESS_TOKEN] = odbc_token_struct(token_provider())
    cparams["attrs_before"] = attrs


def managed_identity_token_provider(client_id: str) -> Callable[[], str]:
    # The ODBC driver's built-in ActiveDirectoryMsi only knows the VM IMDS endpoint, which
    # Azure Container Apps does not expose; azure-identity handles IDENTITY_ENDPOINT as well.
    from azure.identity import ManagedIdentityCredential

    credential = ManagedIdentityCredential(client_id=client_id)
    return lambda: credential.get_token(SQL_ENTRA_SCOPE).token


def make_engine(url: str, sql_token_provider: Callable[[], str] | None = None) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_fks(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    if engine.dialect.name == "mssql" and sql_token_provider is not None:

        @event.listens_for(engine, "do_connect")
        def _entra_token(_dialect, _record, _cargs, cparams):
            inject_entra_token(cparams, sql_token_provider)

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            for stmt in SQLITE_APPEND_ONLY_TRIGGERS:
                conn.exec_driver_sql(stmt)
