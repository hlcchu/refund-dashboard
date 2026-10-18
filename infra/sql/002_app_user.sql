-- Grant the Container App's user-assigned managed identity least-privilege access. Replace
-- <app-identity-name> and <app-identity-client-id> with the `appIdentityName` and
-- `appIdentityClientId` outputs of infra/main.bicep.
-- No DELETE anywhere; no UPDATE on approvals or the audit trail.
-- The user is created from the identity's client ID (SID), so the SQL server does not need the
-- Entra "Directory Readers" role that CREATE USER ... FROM EXTERNAL PROVIDER requires.
DECLARE @sid NVARCHAR(64) =
    CONVERT(NVARCHAR(64), CONVERT(VARBINARY(16), CONVERT(UNIQUEIDENTIFIER, '<app-identity-client-id>')), 1);
EXEC ('CREATE USER [<app-identity-name>] WITH SID = ' + @sid + ', TYPE = E;');
GO
GRANT SELECT, INSERT, UPDATE ON dbo.refund_requests  TO [<app-identity-name>];
GRANT SELECT, INSERT         ON dbo.refund_approvals TO [<app-identity-name>];
GRANT SELECT, INSERT         ON dbo.audit_events     TO [<app-identity-name>];
GRANT SELECT, INSERT, UPDATE ON dbo.app_settings     TO [<app-identity-name>];
GO
