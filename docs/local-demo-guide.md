# Local demo: step-by-step guide

This guide walks through the dashboard as each role and takes about 15 minutes. Everything runs
in one container on your laptop. It doesn't need Azure or a Microsoft Entra tenant; the app signs
in built-in demo users itself.

## 1. Start the app

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine with
Compose v2) and Git.

```bash
git clone https://github.com/hlcchu/refund-dashboard.git
cd refund-dashboard
docker compose up --build
```

Wait for `Uvicorn running on http://0.0.0.0:8000`, then open <http://localhost:8000>.

- **Switch user:** use the **Demo user** picker at the top right, or open
  `http://localhost:8000/?as=<user>`, for example `?as=frank`.
- **Fresh start:** the demo keeps its data between runs. To start again with only the sample
  refunds, press `Ctrl+C`, then run `docker compose down -v && docker compose up`.
- **Without Docker** (Python 3.12):
  ```bash
  pip install -r requirements-dev.txt
  REFUND_SEED_DEMO_DATA=true uvicorn --factory app.main:create_app --port 8000
  ```

### Demo users

| User | Roles | Used for |
|---|---|---|
| Alice | Maker | Raises refunds |
| Bob, Carol | Checker | Approve or reject other people's refunds |
| Erin | Maker + Checker | Shows that holding both roles doesn't let you approve your own refund |
| Frank | Finance | Read-only: totals, export, audit trail |
| Adam | Admin | Settings only; no access to refunds |

### Sample data

The app starts with seven sample refunds:

| Order | Raised by | Amount | Status |
|---|---|---|---|
| ORD-58213 | Alice | $19.99 | Approved by Bob |
| ORD-58377 | Alice | $120.00 | Pending, 1 of 2 approvals (Bob) |
| ORD-58410 | Erin | $45.50 | Pending |
| ORD-58502 | Alice | $250.00 | Pending |
| ORD-58611 | Erin | $12.00 | Rejected by Carol |
| ORD-58720 | Alice | $75.25 | Pending |
| ORD-58799 | Erin | $8.99 | Cancelled |

## 2. Maker: raise refunds (Alice)

1. Select **Alice (Maker)**.
2. **Check:** Alice sees only refunds she raised, and there are no Approve or Reject buttons.
3. Raise a refund using the **Raise refund** form:
   - Customer ID: `CUST-2001`
   - Order ref: `ORD-9001`
   - Amount: `50.00`
   - Currency: `USD`
   - Reason: `Damaged on arrival`

   Then click **Submit for approval**.
   **Expect:** "Refund submitted; needs 1 approval." It requires **one** approval, because $50.00
   is not *above* the threshold.
4. Raise a second refund the same way with order `ORD-9002` and amount `50.01`.
   **Expect:** "Refund submitted; needs 2 approvals from different checkers."
5. *(Optional)* Enter an amount over `50000` (the maximum per refund). The server rejects it.

## 3. Checker: single and dual approval (Bob, then Carol)

1. Select **Bob (Checker)**. Checkers see every refund.
2. Approve **ORD-9001** ($50.00). You can leave the comment empty.
   **Expect:** "Refund approved." The status changes to **APPROVED**.
3. Approve **ORD-9002** ($50.01).
   **Expect:** "Approval 1/2 recorded; a different checker must also approve." The refund stays
   **PENDING**, and Bob no longer gets an Approve button for it.
4. Select **Carol (Checker)** and approve **ORD-9002**.
   **Expect:** "Refund approved." It shows two approvals, one from Bob and one from Carol.
5. Reject another pending refund, such as **ORD-58502**, as Carol. Enter a reason of at least
   5 characters.
   **Expect:** "Refund rejected."
6. Click **Details** on any refund to see its approvals and history.

## 4. Segregation of duties: no self-approval (Erin)

1. Select **Erin (Maker + Checker)**.
2. **Check ORD-58410**, which Erin raised: it shows only **Cancel**, with no Approve or Reject.
3. **Check other people's pending refunds:** they do show Approve and Reject, because Erin is also
   a Checker.

The UI hides the button, but the rule is enforced on the server, keyed on the user's identity
rather than their roles. To prove it, run this while the app is running. It calls the API as Erin
and tries to approve her own refund:

```bash
TOKEN=$(curl -s -X POST localhost:8000/dev/token -H 'Content-Type: application/json' \
  -d '{"user":"erin"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
ID=$(curl -s 'localhost:8000/api/refunds?status=PENDING' -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json; print(next(r["id"] for r in json.load(sys.stdin) if r["order_ref"]=="ORD-58410"))')
curl -s -X POST "localhost:8000/api/refunds/$ID/approve" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' -w '\nHTTP %{http_code}\n'
```

**Expect:**

```
{"detail":"Segregation of duties: you cannot approve a refund you raised"}
HTTP 403
```

The blocked attempt is recorded as `refund.approve.denied` in the audit trail, which you'll see
in the next step.

4. *(Optional)* Click **Cancel** on ORD-58410.
   **Expect:** "Refund cancelled." Only the maker who raised a refund can cancel it.

## 5. Finance: read-only reporting and audit (Frank)

1. Select **Frank (Finance)**.
2. **Summary cards:** show the count and total for each status (Pending, Approved, Rejected,
   Cancelled). Use the status filter to narrow the list.
3. **Read-only:** there's no Raise refund form and no Approve, Reject or Cancel buttons. The API
   returns 403 if Frank tries.
4. **Export CSV:** click it to download the full refund list. The values are escaped, so opening
   the file in Excel can't run formulas.
5. **Audit trail:** each change shows who did it, which roles they held and the time, including:
   - `refund.created`
   - `refund.approval_recorded`
   - `refund.approved`
   - `refund.rejected`
   - `refund.cancelled`
   - Erin's `refund.approve.denied`
6. Click **Verify hash chain**.
   **Expect:** valid = true and the number of events checked. Every event stores the SHA-256 hash
   of the one before it, so editing or deleting any row breaks the chain from that point on.

## 6. Admin: audited settings, no refund access (Adam)

1. Select **Adam (Admin)**.
2. **Check:** no refund list or summary cards are visible. The API returns 403 for refund data.
   Adam sees only **Settings** and **Users seen**.
3. In Settings, change **Two checkers required above** from `50.00` to `100.00` and click
   **Save (audited)**.
   **Expect:** "Settings saved and recorded in the audit trail."
4. **Users seen:** lists each demo user who has signed in, with their roles.
5. Select **Frank** again.
   **Expect:** the audit trail has a `settings.updated` event by Adam, showing the old and new
   values, and **Verify hash chain** is still valid.
6. *(Optional)* As Alice, raise a refund of `75.00`. It now needs only one approval. Then set the
   threshold back to `50.00` as Adam.

## 7. Database-level immutability (optional)

The audit table rejects edits even from someone with direct database access. In SQLite, triggers
enforce this; in Azure, the table is an append-only Azure SQL ledger table. Try it against the
demo database:

```bash
docker compose exec refund-dashboard python -c "
import sqlite3; c = sqlite3.connect('/data/refunds.db')
try: c.execute(\"UPDATE audit_events SET actor_name='mallory'\")
except Exception as e: print('blocked:', e)"
```

**Expect:** `blocked: audit_events is append-only`.

## 8. Run the tests

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pytest
```

The tests cover token forgery, the full role matrix, self-approval, the $50.00 vs $50.01
boundary, idempotency, concurrent approvals and audit tamper detection.

## Stop the demo

Press `Ctrl+C`. Run `docker compose down -v` to also delete the demo data.

Recordings of this walkthrough are in [`demo/`](../demo/README.md).
