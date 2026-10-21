"""Monthly Azure cost estimate for infra/main.bicep, from the public Azure Retail Prices API.

    python scripts/azure_pricing.py --region eastus

Retail (pay-as-you-go, USD) list prices only: no EA/CSP discounts, reservations, tax or
support plans. Edit SCENARIOS to match expected usage.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://prices.azure.com/api/retail/prices"
HOURS_PER_MONTH = 730
DAYS_PER_MONTH = HOURS_PER_MONTH / 24
SECONDS_PER_MONTH = HOURS_PER_MONTH * 3600

# Container Apps Consumption plan free grant, per subscription per month
# (https://learn.microsoft.com/azure/container-apps/billing).
FREE_VCPU_SECONDS = 180_000
FREE_GIB_SECONDS = 360_000
FREE_REQUESTS = 2_000_000
# Log Analytics: first 5 GB/month ingestion free per billing account; 31 days retention free.
FREE_LOG_GB = 5

METERS = {
    "aca_vcpu_active": ("Azure Container Apps", "Standard", "Standard vCPU Active Usage"),
    "aca_vcpu_idle": ("Azure Container Apps", "Standard", "Standard vCPU Idle Usage"),
    "aca_mem_active": ("Azure Container Apps", "Standard", "Standard Memory Active Usage"),
    "aca_mem_idle": ("Azure Container Apps", "Standard", "Standard Memory Idle Usage"),
    "aca_requests": ("Azure Container Apps", "Standard", "Standard Requests"),
    "acr_basic_day": ("Container Registry", "Basic", "Basic Registry Unit"),
    "acr_standard_day": ("Container Registry", "Standard", "Standard Registry Unit"),
    "sql_basic_day": ("SQL Database", "B", "B DTU"),
    "sql_s0_day": ("SQL Database", "S0", "S0 DTUs"),
    "sql_s1_day": ("SQL Database", "S1", "S1 DTUs"),
    "sql_s2_day": ("SQL Database", "S2", "S2 DTUs"),
    "log_ingest_gb": ("Log Analytics", "Analytics Logs", "Analytics Logs Data Ingestion"),
    "blob_grs_gb": ("Storage", "Hot GRS", "Hot GRS Data Stored"),
    "blob_grs_write_10k": ("Storage", "Hot GRS", "Hot GRS Write Operations"),
    "sql_ltr_ragrs_gb": ("SQL Database", "Backup RA-GRS", "Backup RA-GRS Data Stored"),
    "log_alert_15min": (
        "Azure Monitor",
        "Alerts",
        "Alerts System Log Monitored at 15 Minute Frequency",
    ),
}
# Some meter names are shared by several products; pin the one infra/main.bicep uses.
PRODUCTS = {
    "Storage": "General Block Blob v2",
    "SQL Database": "SQL Database - LTR Backup Storage",
}


def fetch_prices(region: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for key, (service, sku, meter) in METERS.items():
        flt = (
            f"armRegionName eq '{region}' and serviceName eq '{service}' "
            f"and skuName eq '{sku}' and meterName eq '{meter}' and priceType eq 'Consumption'"
        )
        url = f"{API}?currencyCode=USD&$filter={urllib.parse.quote(flt)}"
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https URL)
            items = json.load(resp)["Items"]
        if key in ("blob_grs_gb", "blob_grs_write_10k", "sql_ltr_ragrs_gb"):
            items = [i for i in items if i["productName"] == PRODUCTS[service]]
        # Lowest tier above zero (tiered meters such as Log Analytics list a free tier first).
        paid = sorted(
            (i for i in items if i["retailPrice"] > 0), key=lambda i: i["tierMinimumUnits"]
        )
        if not paid:
            raise SystemExit(f"No price found for {key} in {region}")
        prices[key] = paid[0]["retailPrice"]
    return prices


@dataclass
class Scenario:
    name: str
    description: str
    min_replicas: int
    busy_hours: float  # hours/month at least one replica is serving traffic
    extra_replica_hours: float  # replica-hours above the first (bursts)
    requests_millions: float
    sql: str
    acr: str
    log_gb: float
    vcpu: float = 0.5
    memory_gib: float = 1.0
    digest_gb: float = 1.0
    digest_writes_10k: float = 1.0  # ledger digests are uploaded every few minutes
    db_gb: float = 1.0  # database size; one yearly LTR copy per retained year
    ltr_years: int = 6
    # Metric alerts (HTTP 5xx, SQL load, restarts) fall in the free 10 time series; one log alert.
    log_alerts: int = 1


SCENARIOS = [
    Scenario(
        name="Demo / pilot",
        description="Scale to zero, ~2 h/day of use, 20 users",
        min_replicas=0,
        busy_hours=44,
        extra_replica_hours=0,
        requests_millions=0.05,
        sql="sql_basic_day",
        acr="acr_basic_day",
        log_gb=2,
        db_gb=0.5,
    ),
    Scenario(
        name="Production (recommended)",
        description="1 warm replica, busy 10 h x 22 days, ~200 users",
        min_replicas=1,
        busy_hours=220,
        extra_replica_hours=40,
        requests_millions=1.5,
        sql="sql_s0_day",
        acr="acr_basic_day",
        log_gb=10,
        db_gb=2,
    ),
    Scenario(
        name="Production, high volume",
        description="2 warm replicas, busy 12 h x 30 days, ~1,000 users",
        min_replicas=2,
        busy_hours=360,
        extra_replica_hours=180,
        requests_millions=6,
        sql="sql_s2_day",
        acr="acr_standard_day",
        log_gb=40,
        db_gb=10,
    ),
]


def estimate(s: Scenario, p: dict[str, float]) -> dict[str, float]:
    replicas = max(s.min_replicas, 1)
    active_replica_s = (s.busy_hours * replicas + s.extra_replica_hours) * 3600
    idle_replica_s = (
        max(SECONDS_PER_MONTH - s.busy_hours * 3600, 0) * s.min_replicas if s.min_replicas else 0
    )
    # Free grants are applied to active usage first (conservative: idle is cheaper per second).
    active_vcpu = max(active_replica_s * s.vcpu - FREE_VCPU_SECONDS, 0)
    active_mem = max(active_replica_s * s.memory_gib - FREE_GIB_SECONDS, 0)
    left_vcpu = max(FREE_VCPU_SECONDS - active_replica_s * s.vcpu, 0)
    left_mem = max(FREE_GIB_SECONDS - active_replica_s * s.memory_gib, 0)
    idle_vcpu = max(idle_replica_s * s.vcpu - left_vcpu, 0)
    idle_mem = max(idle_replica_s * s.memory_gib - left_mem, 0)
    compute = (
        active_vcpu * p["aca_vcpu_active"]
        + active_mem * p["aca_mem_active"]
        + idle_vcpu * p["aca_vcpu_idle"]
        + idle_mem * p["aca_mem_idle"]
        + max(s.requests_millions - FREE_REQUESTS / 1e6, 0) * p["aca_requests"]
    )
    return {
        "Container Apps": compute,
        "Container Registry": p[s.acr] * DAYS_PER_MONTH,
        "Azure SQL (ledger)": p[s.sql] * DAYS_PER_MONTH,
        "Long-term SQL backups": s.db_gb * s.ltr_years * p["sql_ltr_ragrs_gb"],
        "Logs + Application Insights": max(s.log_gb - FREE_LOG_GB, 0) * p["log_ingest_gb"],
        "Alerts": s.log_alerts * p["log_alert_15min"],
        "WORM digest storage": s.digest_gb * p["blob_grs_gb"]
        + s.digest_writes_10k * p["blob_grs_write_10k"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="eastus")
    args = parser.parse_args()
    prices = fetch_prices(args.region)

    names = [s.name for s in SCENARIOS]
    results = [estimate(s, prices) for s in SCENARIOS]
    print(f"Region: {args.region} | USD retail list prices | per month (730 h)\n")
    print("| Component | " + " | ".join(names) + " |")
    print("|---|" + "---:|" * len(names))
    for component in results[0]:
        cells = " | ".join(f"${r[component]:,.2f}" for r in results)
        print(f"| {component} | {cells} |")
    totals = " | ".join(f"**${sum(r.values()):,.2f}**" for r in results)
    print(f"| **Total** | {totals} |\n")
    for s in SCENARIOS:
        print(
            f"- **{s.name}:** {s.description}; SQL {s.sql.split('_')[1].upper()}, "
            f"ACR {s.acr.split('_')[1].title()}, {s.log_gb:g} GB logs/month, "
            f"{s.db_gb:g} GB database x {s.ltr_years} yearly backups."
        )
    print("\nUnit prices used:")
    for key, value in prices.items():
        print(f"- {key}: {value:g}")


if __name__ == "__main__":
    main()
