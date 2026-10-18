# Demo recordings

End-to-end UI tests, recorded 2026-10-18: two against the local demo (`docker compose up --build`)
and one against a live Azure deployment of `infra/`.

| File | Commit | What it shows |
|---|---|---|
| [`01-full-e2e-test.mp4`](01-full-e2e-test.mp4) | `bdfd6ac` | Full walkthrough as each role. |
| [`02-fix-retest.mp4`](02-fix-retest.mp4) | `72ca8bc` | Retest of the two UI fixes. |
| [`03-azure-live-demo.mp4`](03-azure-live-demo.mp4) | `7161d2b` | Walkthrough on the live Azure deployment. |

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
