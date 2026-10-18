-- Grant the Container App's user-assigned managed identity least-privilege access. Replace
-- <app-identity-name> with the `appIdentityName` output of infra/main.bicep.
-- No DELETE anywhere; no UPDATE on approvals or the audit trail.
CREATE USER [<app-identity-name>] FROM EXTERNAL PROVIDER;
GO
GRANT SELECT, INSERT, UPDATE ON dbo.refund_requests  TO [<app-identity-name>];
GRANT SELECT, INSERT         ON dbo.refund_approvals TO [<app-identity-name>];
GRANT SELECT, INSERT         ON dbo.audit_events     TO [<app-identity-name>];
GRANT SELECT, INSERT, UPDATE ON dbo.app_settings     TO [<app-identity-name>];
GO
