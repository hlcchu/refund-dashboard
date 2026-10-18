#!/usr/bin/env bash
# Customer-run deployment of the Refund Dashboard into an Azure resource group.
# Prereqs: Azure CLI (`az login`), rights to create resources + role assignments in the RG,
# an Entra app registration (infra/entra/setup-app-registration.sh) and an Entra group of SQL
# admins. Nothing in this repo deploys automatically.
#
#   RG=rg-refunds LOCATION=eastus ENTRA_CLIENT_ID=... SQL_ADMIN_GROUP_ID=... \
#   SQL_ADMIN_GROUP_NAME="Refund SQL Admins" ./infra/deploy.sh
set -euo pipefail

: "${RG:?set RG}" "${LOCATION:?set LOCATION}" "${ENTRA_CLIENT_ID:?set ENTRA_CLIENT_ID}"
: "${SQL_ADMIN_GROUP_ID:?set SQL_ADMIN_GROUP_ID}" "${SQL_ADMIN_GROUP_NAME:?set SQL_ADMIN_GROUP_NAME}"
NAME_PREFIX="${NAME_PREFIX:-refunds}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
HERE="$(cd "$(dirname "$0")" && pwd)"

params=(
  namePrefix="$NAME_PREFIX"
  entraApiClientId="$ENTRA_CLIENT_ID"
  sqlAdminGroupObjectId="$SQL_ADMIN_GROUP_ID"
  sqlAdminGroupName="$SQL_ADMIN_GROUP_NAME"
)

az group create --name "$RG" --location "$LOCATION" -o none

echo "1/3 Infrastructure (registry, SQL ledger DB, WORM storage, Container Apps environment)"
acr=$(az deployment group create -g "$RG" -f "$HERE/main.bicep" \
  -p "${params[@]}" deployApp=false --query properties.outputs.acrName.value -o tsv)

echo "2/3 Build image in ACR (no local Docker needed): refund-dashboard:$IMAGE_TAG"
az acr build -r "$acr" -t "refund-dashboard:$IMAGE_TAG" "$HERE/.."

echo "3/3 Container App"
az deployment group create -g "$RG" -f "$HERE/main.bicep" \
  -p "${params[@]}" deployApp=true imageName="refund-dashboard:$IMAGE_TAG" \
  --query properties.outputs -o json > "$HERE/outputs.json.out"

app_url=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["appUrl"]["value"])' "$HERE/outputs.json.out")
identity=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["appIdentityName"]["value"])' "$HERE/outputs.json.out")
cat <<MSG

Deployed: $app_url

Remaining one-off steps (as a member of "$SQL_ADMIN_GROUP_NAME"):
  1. Run infra/sql/001_schema.sql against the 'refunds' database.
  2. Run infra/sql/002_app_user.sql with <app-identity-name> replaced by: $identity
  3. Add $app_url as a SPA redirect URI on the app registration.
  4. Assign users/groups to Refund.Maker / Checker / Finance / Admin in Entra ID.
  5. Once verified, lock the WORM policy on container 'sqldbledgerdigests' (irreversible).
MSG
