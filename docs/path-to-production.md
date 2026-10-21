# Path to production

The templates in `infra/` give a working, secure-by-default deployment: Entra-only sign-in,
managed identities with no secrets, an append-only ledger and WORM storage. They're tuned for a
demo or pilot. This page lists what to add before production. The network items are left out of
the templates because the customer hasn't confirmed an Azure deployment (see
[decisions.md](decisions.md) #13).

## 1. Identity and access

- [ ] Create the real app registration (`infra/entra/setup-app-registration.sh`) and deploy with
      its client ID. The app refuses to start in production without Entra mode.
- [ ] Assign users or groups to `Refund.Maker`, `Refund.Checker`, `Refund.Finance` and
      `Refund.Admin`. Assigning **groups** needs Microsoft Entra ID P1 or P2.
- [ ] Add the app URL, or your custom domain, as an SPA redirect URI.
- [ ] Make sure no one is in both the Admin role and the Maker or Checker roles. The app blocks
      such users from changing settings, but keep the groups separate anyway.

## 2. Region

- [ ] Deploy the app and Azure SQL to the **same region**. Don't use `SQL_LOCATION`.
- [ ] If the region refuses new SQL servers (`RegionDoesNotAllowProvisioning`), either pick a
      region that accepts them or open an Azure support request for SQL access in that region.

## 3. Network lockdown

Today Azure SQL has a public endpoint with "Allow Azure services" turned on. Sign-in is Entra-only,
but the endpoint can be reached from any Azure-hosted address. The app's URL is also public.
For production:

- [ ] **Private network for the app:** recreate the Container Apps environment as a
      workload-profiles environment inside a VNet. Use the Consumption profile, which keeps the
      same pricing, and give it a dedicated infrastructure subnet (/27 or larger). The
      environment's network can't be changed after creation.
- [ ] **Private endpoint for Azure SQL:** create it in the VNet with the
      `privatelink.database.windows.net` private DNS zone. Then set `publicNetworkAccess:
      'Disabled'` on the SQL server and remove the `AllowAzureServices` firewall rule.
- [ ] **Private endpoint for the digest storage account:** use the
      `privatelink.blob.core.windows.net` zone. Then restrict the storage firewall. Confirm that
      SQL ledger digest uploads still succeed afterwards: the SQL server's managed identity must
      still be able to reach the account, e.g. through a resource-instance rule. Check
      `sys.database_ledger_digest_locations`.
- [ ] **Limit who can reach the app:** either add IP restrictions on the Container App ingress for
      your corporate egress addresses, which is free, or make the environment internal and reach
      it over VPN or ExpressRoute.
- [ ] **Container Registry:** Basic and Standard don't support private endpoints. Either keep
      public pull, which is authenticated by the managed identity with the admin user disabled,
      or upgrade to Premium (about $50/month at list price) and add a private endpoint.

Indicative extra cost: Private Link endpoints are about $7–8/month each, plus data processing.
Private DNS zones are $0.50/month each. Use `scripts/azure_pricing.py` or the Azure pricing
calculator for your region.

## 4. Edge protection: Azure Front Door with a web application firewall (out of scope)

Not included. This is an internal tool, and IP restrictions or an internal environment cover
that case. Front Door Premium with a web application firewall (WAF) becomes relevant if the app
is exposed to external users or the public internet, or needs global load balancing, DDoS, bot
and OWASP rule protection, or Private Link from the edge to the app. Indicative list price: about
**$330/month base fee** for Premium, plus per-request and data-transfer charges. If you add it,
lock the Container App ingress so only Front Door can reach it: use Private Link, or IP
restrictions on the `AzureFrontDoor.Backend` service tag plus a check of the `X-Azure-FDID`
header.

## 5. Data protection and retention

- [ ] **Lock the WORM immutability policy** on container `sqldbledgerdigests` once the deployment
      is verified (`az storage container immutability-policy lock`). Locking is irreversible.
- [ ] Confirm the 6-year retention (`backupRetentionYears`, `ledgerDigestRetentionDays`) with
      the customer's finance and legal teams. See [decisions.md](decisions.md) #9.
- [ ] The first yearly long-term backup is taken in the first week of January. Until then,
      restore relies on the 7-day point-in-time window.
- [ ] Schedule `infra/sql/verify_ledger.sql`, e.g. monthly. It checks the database against the
      fingerprints in WORM storage.
- [ ] Schedule a regular audit-chain check (`GET /api/audit/verify`). Today it runs only when
      Finance clicks **Verify hash chain**, and a failure triggers the `audit-chain-invalid` alert.

## 6. Operations

- [ ] Deploy with `ALERT_EMAIL` set, or point the action group at your on-call tool.
- [ ] Deploy only specific image tags. `deploy.sh` tags images with the git commit SHA; don't
      deploy `latest`.
- [ ] Consider Microsoft Defender for SQL, Storage and Containers. These are priced separately.
- [ ] Use `minReplicas = 1` to avoid cold starts. Raise the SQL tier if the `sql-load` alert fires.

## 7. Tearing down a test deployment

- Unlock or delete the WORM policy first if it was never locked. A **locked** policy keeps the
  storage account until retention expires.
- **Long-term SQL backups outlive the server and keep billing.** After deleting the resource group,
  list and delete them:
  `az sql db ltr-backup list -l <sql-region>` and `az sql db ltr-backup delete ...`.
