# Demo recordings

End-to-end UI tests, recorded 2026-10-18: two against the local demo (`docker compose up --build`)
and one against a live Azure deployment of `infra/`.

| File | Commit | What it shows |
|---|---|---|
| [`01-full-e2e-test.mp4`](01-full-e2e-test.mp4) | `bdfd6ac` | Full walkthrough as each role. |
| [`02-fix-retest.mp4`](02-fix-retest.mp4) | `72ca8bc` | Retest of the two UI fixes. |
| [`03-azure-live-demo.mp4`](03-azure-live-demo.mp4) | `7161d2b` | Walkthrough on the live Azure deployment. |
| [`04-azure-deploy-terminal.mp4`](04-azure-deploy-terminal.mp4) | `c1671ae` | Terminal recording: one-shot deploy of `main`, 5 minutes live, teardown (2026-10-21). |

**What the full walkthrough covers:**
- The $50.00 vs $50.01 approval threshold.
- Two approvals from different checkers.
- Self-approval is blocked (API returns 403) and the attempt is audited.
- Reject and cancel.
- Finance read-only views: CSV export, refund history, audit trail and hash-chain check.
- Admin threshold change, recorded in the audit trail.
- Rejection of amounts over the maximum.

**Bugs found in the full walkthrough:**
- Admin could still see summary cards left over from the previous user.
- After the first of two approvals, the message said "Refund approved." even though the refund was still pending.

**Retest results:** Admin now sees only Settings and Users seen. After the first approval, the message reads "Approval 1/2 recorded"; the second approval completes the refund.

**Live Azure deployment (`03`):** deployed with `infra/deploy.sh` to a test subscription:
- Container Apps in East US.
- Azure SQL ledger database in Central US.
- Write-once (WORM) storage for ledger digests.
- Managed identity for the app's database access.

The walkthrough repeats the threshold, two-checker, self-approval, Finance and Admin flows against Azure SQL, where:
- `audit_events` is an append-only ledger table, and the database rejects UPDATE and DELETE.
- Ledger digests were uploaded to the WORM container.

The test stack was deleted afterwards.

Sign-in in `03` uses the demo user picker (`REFUND_AUTH_MODE=dev`). The test tenant has no Entra app registration, so real Microsoft Entra sign-in is still not covered. The production configuration (`auth_mode=entra`) was also deployed. It served the UI and returned 401 for API calls without a token.

**Terminal deploy (`04`):** a single scripted run, recorded with asciinema (waits over 2 s are
shortened), of `main` after the architecture review, with the app and SQL both in Central US:
1. `infra/deploy.sh` creates the infrastructure, including Application Insights and four alerts.
   ACR Tasks are blocked on the subscription, so the script falls back to a local Docker build
   and push. It then deploys the Container App. Total time: about 5 minutes.
2. The one-off SQL steps run: `001_schema.sql`, then `002_app_user.sql`. All four tables are
   ledger tables, and `audit_events` is `APPEND_ONLY`.
3. The app stays live for 5 minutes, with probes every 30 seconds. `/healthz` and the UI return
   200. `/api/refunds` returns 401 without a token: auth is in production mode with a placeholder
   Entra client ID.
4. Checks:
   - The alerts `http-5xx`, `sql-load`, `restarts` and `audit-chain-invalid` are all enabled.
   - The long-term SQL backup policy is yearly, kept `P6Y`.
   - Application Insights received the requests. `/healthz` probes are excluded from tracing.
5. Teardown: no long-term backups exist yet, the unlocked WORM policy is removed, and
   `az group delete` completes.
