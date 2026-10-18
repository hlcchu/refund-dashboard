# Demo recordings

End-to-end UI tests of the local demo (`docker compose up --build`, dev users), recorded 2026-10-18.

| File | Commit | What it shows |
|---|---|---|
| [`01-full-e2e-test.mp4`](01-full-e2e-test.mp4) | `bdfd6ac` | Full walkthrough as each role. |
| [`02-fix-retest.mp4`](02-fix-retest.mp4) | `72ca8bc` | Retest of the two UI fixes. |

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

Not covered: real Microsoft Entra sign-in and Azure deployment. Both need a customer tenant and subscription.
