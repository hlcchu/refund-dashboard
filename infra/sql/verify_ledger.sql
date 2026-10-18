-- Verify the database ledger against the digests stored in immutable blob storage.
-- Run periodically (e.g. from an Azure Automation runbook) and alert on failure.
DECLARE @digest_locations NVARCHAR(MAX) =
    (SELECT * FROM sys.database_ledger_digest_locations FOR JSON AUTO, INCLUDE_NULL_VALUES);
EXEC sys.sp_verify_database_ledger_from_digest_storage @digest_locations;
