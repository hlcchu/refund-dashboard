-- Azure SQL schema for the Refund Dashboard. Run once as a member of the SQL admin group.
-- The database is created with ledger ON (infra/main.bicep), so:
--   * audit_events is an APPEND-ONLY ledger table: UPDATE/DELETE are rejected by the engine,
--     even for db_owner.
--   * refund_requests / refund_approvals are UPDATABLE ledger tables: every historical row
--     version is kept in a history table and covered by the ledger hash.
--   * Database digests are uploaded automatically to WORM blob storage, so tampering by a
--     DBA can be detected with sys.sp_verify_database_ledger_from_digest_storage.
-- Keep in sync with app/models.py.

CREATE TABLE dbo.refund_requests (
    id                 NVARCHAR(36)   NOT NULL CONSTRAINT pk_refund_requests PRIMARY KEY,
    customer_id        NVARCHAR(64)   NOT NULL,
    order_ref          NVARCHAR(64)   NOT NULL,
    amount_minor       BIGINT         NOT NULL,
    currency           NVARCHAR(3)    NOT NULL,
    reason             NVARCHAR(1000) NOT NULL,
    status             NVARCHAR(16)   NOT NULL,
    required_approvals INT            NOT NULL,
    maker_oid          NVARCHAR(64)   NOT NULL,
    maker_name         NVARCHAR(256)  NOT NULL,
    idempotency_key    NVARCHAR(128)  NULL,
    rejection_reason   NVARCHAR(1000) NULL,
    created_at         DATETIMEOFFSET NOT NULL,
    updated_at         DATETIMEOFFSET NOT NULL,
    decided_at         DATETIMEOFFSET NULL,
    version            INT            NOT NULL,
    CONSTRAINT ck_refund_amount_positive CHECK (amount_minor > 0),
    CONSTRAINT ck_refund_required_approvals CHECK (required_approvals >= 1),
    CONSTRAINT ck_refund_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED'))
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.refund_requests_history), LEDGER = ON);
GO

CREATE INDEX ix_refund_requests_status ON dbo.refund_requests (status);
CREATE INDEX ix_refund_requests_maker_oid ON dbo.refund_requests (maker_oid);
CREATE UNIQUE INDEX uq_refund_maker_idempotency
    ON dbo.refund_requests (maker_oid, idempotency_key) WHERE idempotency_key IS NOT NULL;
GO

CREATE TABLE dbo.refund_approvals (
    id           INT IDENTITY(1, 1) NOT NULL CONSTRAINT pk_refund_approvals PRIMARY KEY,
    refund_id    NVARCHAR(36)   NOT NULL CONSTRAINT fk_approval_refund REFERENCES dbo.refund_requests (id),
    checker_oid  NVARCHAR(64)   NOT NULL,
    checker_name NVARCHAR(256)  NOT NULL,
    comment      NVARCHAR(1000) NULL,
    created_at   DATETIMEOFFSET NOT NULL,
    CONSTRAINT uq_approval_checker UNIQUE (refund_id, checker_oid)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.refund_approvals_history), LEDGER = ON);
GO

CREATE TABLE dbo.audit_events (
    seq         BIGINT         NOT NULL CONSTRAINT pk_audit_events PRIMARY KEY,
    occurred_at NVARCHAR(40)   NOT NULL,
    actor_oid   NVARCHAR(64)   NOT NULL,
    actor_name  NVARCHAR(256)  NOT NULL,
    actor_roles NVARCHAR(256)  NOT NULL,
    action      NVARCHAR(64)   NOT NULL,
    entity_type NVARCHAR(32)   NOT NULL,
    entity_id   NVARCHAR(64)   NOT NULL,
    payload     NVARCHAR(MAX)  NOT NULL,
    prev_hash   NVARCHAR(64)   NOT NULL CONSTRAINT uq_audit_prev_hash UNIQUE,
    hash        NVARCHAR(64)   NOT NULL CONSTRAINT uq_audit_hash UNIQUE
)
WITH (LEDGER = ON (APPEND_ONLY = ON));
GO

-- Runtime limits changed by Refund.Admin. Updatable ledger table: every previous value is
-- retained in history, and each change is also written to audit_events.
CREATE TABLE dbo.app_settings (
    [key]           NVARCHAR(64)  NOT NULL CONSTRAINT pk_app_settings PRIMARY KEY,
    value           NVARCHAR(64)  NOT NULL,
    updated_at      DATETIMEOFFSET NOT NULL,
    updated_by_oid  NVARCHAR(64)  NOT NULL,
    updated_by_name NVARCHAR(256) NOT NULL,
    version         INT           NOT NULL
)
WITH (SYSTEM_VERSIONING = ON, LEDGER = ON);
GO

CREATE INDEX ix_audit_events_entity_id ON dbo.audit_events (entity_id);
CREATE INDEX ix_audit_events_actor_oid ON dbo.audit_events (actor_oid);
GO
