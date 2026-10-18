#!/usr/bin/env bash
# Creates the "Refund Dashboard" app registration: app roles, v2 access tokens, a delegated
# scope, and a SPA redirect URI for the web UI (MSAL.js, auth code + PKCE). Role assignment is
# required for sign-in. Group-based role assignment needs Microsoft Entra ID P1 or P2. Run with `az login` as a user
# allowed to create app registrations. Then assign users/groups to roles in
# Entra ID > Enterprise applications > Refund Dashboard API > Users and groups.
set -euo pipefail

APP_NAME="${APP_NAME:-Refund Dashboard}"
# Space-separated. Re-run `az ad app update --id <id> --web-redirect-uris ...` style PATCH below
# with the Container App URL once deployed (infra/deploy.sh prints it).
REDIRECT_URIS="${REDIRECT_URIS:-http://localhost:8000}"
HERE="$(cd "$(dirname "$0")" && pwd)"

app_id=$(az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --app-roles @"$HERE/app-roles.json" \
  --query appId -o tsv)
object_id=$(az ad app show --id "$app_id" --query id -o tsv)

az ad app update --id "$app_id" --identifier-uris "api://$app_id"

scope_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$object_id" \
  --headers "Content-Type=application/json" \
  --body "{
    \"spa\": { \"redirectUris\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1].split()))' "$REDIRECT_URIS") },
    \"api\": {
      \"requestedAccessTokenVersion\": 2,
      \"oauth2PermissionScopes\": [{
        \"id\": \"$scope_id\",
        \"value\": \"Refunds.Access\",
        \"type\": \"User\",
        \"isEnabled\": true,
        \"adminConsentDisplayName\": \"Access the Refund Dashboard API\",
        \"adminConsentDescription\": \"Call the Refund Dashboard API as the signed-in user.\",
        \"userConsentDisplayName\": \"Access the Refund Dashboard API\",
        \"userConsentDescription\": \"Call the Refund Dashboard API as you.\"
      }]
    }
  }"

sp_id=$(az ad sp create --id "$app_id" --query id -o tsv)
az ad sp update --id "$sp_id" --set appRoleAssignmentRequired=true

echo "API application (client) ID: $app_id"
echo "Scope used by the web UI: api://$app_id/Refunds.Access"
echo "Assign users/groups: Entra ID > Enterprise applications > $APP_NAME > Users and groups"
