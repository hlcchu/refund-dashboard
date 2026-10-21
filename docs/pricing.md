# Azure pricing estimate

Estimated monthly cost of running the stack in [`infra/main.bicep`](../infra/main.bicep). Numbers
come from [`scripts/azure_pricing.py`](../scripts/azure_pricing.py), which reads the public
[Azure Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)
(East US, USD, pay-as-you-go list prices, retrieved 2026-10-18). Re-run it for a different
region or after changing the scenarios:

```bash
python scripts/azure_pricing.py --region uksouth
```

| Component | Demo / pilot | Production (recommended) | Production, high volume |
|---|---:|---:|---:|
| Container Apps (API + web UI) | $0.00 | $16.90 | $56.79 |
| Container Registry | $5.07 | $5.07 | $20.28 |
| Azure SQL (ledger database) | $4.90 | $14.72 | $73.61 |
| Long-term SQL backups (6 yearly copies) | $0.15 | $0.60 | $3.00 |
| Logs + Application Insights | $0.00 | $11.50 | $80.50 |
| Alerts (1 log alert; metric alerts free) | $0.50 | $0.50 | $0.50 |
| WORM digest storage | $0.15 | $0.15 | $0.15 |
| **Total per month** | **~$11** | **~$49** | **~$235** |

## Scenario assumptions

| | Demo / pilot | Production (recommended) | High volume |
|---|---|---|---|
| Users | ~20 | ~200 | ~1,000 |
| Container replicas | scale to zero | 1 always warm, bursts to 3 | 2 always warm, bursts to 3 |
| Replica size | 0.5 vCPU / 1 GiB | 0.5 vCPU / 1 GiB | 0.5 vCPU / 1 GiB |
| Busy time | ~2 h/day | 10 h x 22 days | 12 h x 30 days |
| HTTP requests | 50k | 1.5M | 6M |
| Azure SQL | Basic (5 DTU) | S0 (10 DTU) | S2 (50 DTU) |
| Container Registry | Basic | Basic | Standard |
| Logs + telemetry ingested | 2 GB | 10 GB | 40 GB |
| Database size (per yearly backup) | 0.5 GB | 2 GB | 10 GB |

How the main lines are calculated:

- **Container Apps (Consumption plan):** billed per second for vCPU and memory. A warm replica
  that isn't serving traffic is charged at the lower idle rate. The monthly free grant per
  subscription (180,000 vCPU-seconds, 360,000 GiB-seconds and 2M requests, per
  [Container Apps billing](https://learn.microsoft.com/azure/container-apps/billing)) is
  included. The free grant applies once per subscription, so it won't cover several apps at once.
- **Azure SQL:** DTU-based single database billed daily. Ledger has no extra charge, but ledger
  history tables use storage that's included in the tier's allowance at this data volume.
- **Log Analytics:** first 5 GB/month ingestion free, then $2.30/GB; 30-day retention (in the
  free retention window).
- **Application Insights:** workspace-based, so telemetry is billed as Log Analytics ingestion
  (included in the log volumes above). Health-probe requests are not traced.
- **Long-term SQL backups:** one full copy per retained year (6), RA-GRS backup storage at
  $0.05/GB-month. The bill grows during the first 6 years, then stays flat. LTR backups keep
  billing after the server is deleted until you delete them.
- **Alerts:** the three metric alerts fall within the free 10 monitored time series. The
  audit-chain log alert runs every 15 minutes ($0.50/month).
- **WORM storage:** ledger digests are small (well under 1 GB), and there are a few thousand
  writes a month.

## Not included

- **Microsoft Entra ID licences.** Sign-in and app roles work with the Entra ID Free tier
  included with Microsoft 365. Assigning roles **to groups** (rather than to individual users) needs
  Microsoft Entra ID P1 or P2, which many Microsoft 365 E3/E5 customers already have.
- **Private networking:** VNet-integrated Container Apps environment, private endpoints for SQL and
  storage, and NAT gateway. Recommended for production; see
  [path-to-production.md](path-to-production.md).
- **Azure Front Door Premium + WAF** (out of scope): about $330/month base fee at list price.
- **Defender for Cloud, Azure Monitor alerts, backup retention beyond the default, custom domains,
  certificates, outbound data transfer, support plans, and taxes.**
- **EA/CSP discounts or reservations.** These lower the SQL cost in particular.

## Compared with Power Apps

For context only, check current Microsoft list prices and your agreement. Power Apps Premium
is licensed **per user per month**, so the cost grows with headcount. In this design, cost
depends on usage rather than headcount: adding the 201st user costs roughly nothing.

## Cost levers

- `minReplicas=0` saves ~$8/month of idle compute, but the first request after an idle period
  takes a few seconds (cold start).
- Azure SQL Basic tier is fine for demos and small teams. Move to S0/S1 for production SLAs.
- Reduce `logRetentionDays` or add sampling if log volume grows.
