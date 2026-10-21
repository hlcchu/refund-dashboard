// Refund Dashboard on Azure: Container Apps (API + web UI in one image) pulling from Azure
// Container Registry, Azure SQL ledger database with long-term backups, immutable (WORM) blob
// storage for ledger digests, and Log Analytics + Application Insights with alerts. Deploy with infra/deploy.sh (two passes: infra, then app).
targetScope = 'resourceGroup'

@description('Short prefix for resource names (lowercase letters and digits).')
@minLength(3)
@maxLength(12)
param namePrefix string = 'refunds'

param location string = resourceGroup().location

@description('Region for Azure SQL. Keep it equal to location; only override when location refuses new SQL servers (see README).')
param sqlLocation string = location

@description('Entra ID tenant that issues tokens for the API.')
param entraTenantId string = tenant().tenantId

@description('Application (client) ID of the "Refund Dashboard" app registration (see infra/entra).')
param entraApiClientId string

@description('Object ID of the Entra group that administers Azure SQL.')
param sqlAdminGroupObjectId string

@description('Display name of the Entra group that administers Azure SQL.')
param sqlAdminGroupName string

@description('Create the Container App. Set false on the first pass, before an image is pushed to ACR.')
param deployApp bool = true

@description('Image tag in ACR, e.g. refund-dashboard:1.0.0')
param imageName string = 'refund-dashboard:latest'

@allowed(['Basic', 'Standard'])
param acrSku string = 'Basic'

param sqlSkuName string = 'S0'

@description('0 scales to zero when idle (cheapest, cold starts); 1 keeps one replica warm.')
@minValue(0)
param minReplicas int = 1

@minValue(1)
param maxReplicas int = 3

@description('Initial default; Refund.Admin can change it at runtime (audited). Refunds strictly above it need two distinct checkers.')
param dualApprovalThreshold string = '50.00'

param maxRefundAmount string = '50000.00'

@description('Years to keep yearly full backups of the refund database (long-term retention). 0 disables. See docs/decisions.md.')
@minValue(0)
@maxValue(10)
param backupRetentionYears int = 6

@description('WORM retention for ledger digests; must cover backupRetentionYears. 2190 days = 6 years.')
param ledgerDigestRetentionDays int = 2190

@description('Email notified when an alert fires. Empty: alerts still fire and show in the portal.')
param alertEmail string = ''

param logRetentionDays int = 30

var suffix = uniqueString(resourceGroup().id)
var cleanPrefix = toLower(replace(namePrefix, '-', ''))
var sqlServerName = '${namePrefix}-sql-${suffix}'
var sqlDbName = 'refunds'
var acrName = take('${cleanPrefix}acr${suffix}', 50)
var storageName = take('${cleanPrefix}dig${suffix}', 24)
var appName = take('${namePrefix}-app-${suffix}', 32)
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
}

// ---- Identity & registry ---------------------------------------------------

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-id-${suffix}'
  location: location
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: acrSku }
  properties: {
    adminUserEnabled: false
  }
}

resource appCanPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, appIdentity.id, roles.acrPull)
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

// ---- Monitoring ------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-appi-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---- Azure SQL (ledger) ----------------------------------------------------

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: sqlLocation
  identity: { type: 'SystemAssigned' }
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'Group'
      login: sqlAdminGroupName
      sid: sqlAdminGroupObjectId
      tenantId: entraTenantId
      azureADOnlyAuthentication: true
    }
  }
}

// Lets Azure-hosted services (incl. Container Apps) reach SQL; auth is still Entra-only.
// For production, use a VNet-integrated Container Apps environment + private endpoint and
// set publicNetworkAccess to 'Disabled'.
resource sqlAllowAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: sqlDbName
  location: sqlLocation
  sku: { name: sqlSkuName }
  properties: {
    // Ledger database: every table is a ledger table and this cannot be turned off later.
    isLedgerOn: true
  }
}

// Yearly full backup (first full backup of January), kept backupRetentionYears. Point-in-time
// restore covers the last 7 days. LTR backups outlive the server: delete them explicitly on teardown.
resource sqlLongTermRetention 'Microsoft.Sql/servers/databases/backupLongTermRetentionPolicies@2023-08-01-preview' = {
  parent: sqlDb
  name: 'default'
  properties: {
    weeklyRetention: 'PT0S'
    monthlyRetention: 'PT0S'
    yearlyRetention: backupRetentionYears == 0 ? 'PT0S' : 'P${backupRetentionYears}Y'
    weekOfYear: 1
  }
}

// ---- WORM storage for ledger digests ---------------------------------------

resource digestStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_GRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: digestStorage
  name: 'default'
}

resource digestContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'sqldbledgerdigests'
}

// Time-based WORM retention. Left unlocked so the deployment is re-runnable; lock it once
// verified (az storage container immutability-policy lock) - locking is irreversible.
resource digestImmutability 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = {
  parent: digestContainer
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: ledgerDigestRetentionDays
    allowProtectedAppendWrites: true
  }
}

resource sqlCanWriteDigests 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: digestStorage
  name: guid(digestStorage.id, sqlServer.id, roles.storageBlobDataContributor)
  properties: {
    principalId: sqlServer.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      roles.storageBlobDataContributor
    )
  }
}

resource ledgerDigests 'Microsoft.Sql/servers/databases/ledgerDigestUploads@2023-08-01-preview' = {
  parent: sqlDb
  name: 'current'
  properties: {
    digestStorageEndpoint: digestStorage.properties.primaryEndpoints.blob
  }
  dependsOn: [sqlCanWriteDigests, digestImmutability]
}

// ---- Container Apps --------------------------------------------------------

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env-${suffix}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var odbc = 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDbName};Encrypt=yes;TrustServerCertificate=no;'

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        { name: 'appinsights-connection-string', value: appInsights.properties.ConnectionString }
      ]
      registries: [
        {
          server: acr.properties.loginServer
          identity: appIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'refund-dashboard'
          image: '${acr.properties.loginServer}/${imageName}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'REFUND_ENVIRONMENT', value: 'production' }
            { name: 'REFUND_AUTH_MODE', value: 'entra' }
            { name: 'REFUND_ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'REFUND_ENTRA_API_CLIENT_ID', value: entraApiClientId }
            { name: 'REFUND_AUTO_CREATE_SCHEMA', value: 'false' }
            { name: 'REFUND_DUAL_APPROVAL_THRESHOLD', value: dualApprovalThreshold }
            { name: 'REFUND_MAX_REFUND_AMOUNT', value: maxRefundAmount }
            { name: 'REFUND_DATABASE_URL', value: 'mssql+pyodbc:///?odbc_connect=${uriComponent(odbc)}' }
            { name: 'REFUND_SQL_MANAGED_IDENTITY_CLIENT_ID', value: appIdentity.properties.clientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8000 }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
  dependsOn: [appCanPull]
}

// ---- Alerts ----------------------------------------------------------------

resource alertGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmail)) {
  name: '${namePrefix}-alerts-${suffix}'
  location: 'global'
  properties: {
    groupShortName: 'refunds'
    enabled: true
    emailReceivers: [{ name: 'ops', emailAddress: alertEmail, useCommonAlertSchema: true }]
  }
}

var metricAlertActions = empty(alertEmail) ? [] : [{ actionGroupId: alertGroup.id }]
// DTU tiers (Basic, S0, P1...) report DTU %; vCore SKUs (GP_Gen5_2...) report CPU %.
var sqlLoadMetric = contains(sqlSkuName, '_') ? 'cpu_percent' : 'dtu_consumption_percent'

var metricAlerts = [
  {
    name: 'http-5xx'
    description: 'More than 5 failed (5xx) API requests in 5 minutes.'
    severity: 2
    scope: appInsights.id
    namespace: 'microsoft.insights/components'
    metric: 'requests/failed'
    aggregation: 'Count'
    threshold: 5
    window: 'PT5M'
  }
  {
    name: 'sql-load'
    description: 'Refund database above 80% of its compute limit for 15 minutes; consider a larger SKU.'
    severity: 2
    scope: sqlDb.id
    namespace: 'Microsoft.Sql/servers/databases'
    metric: sqlLoadMetric
    aggregation: 'Average'
    threshold: 80
    window: 'PT15M'
  }
]

resource metricAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = [
  for a in metricAlerts: {
    name: '${namePrefix}-${a.name}-${suffix}'
    location: 'global'
    properties: {
      description: a.description
      severity: a.severity
      enabled: true
      scopes: [a.scope]
      evaluationFrequency: 'PT5M'
      windowSize: a.window
      criteria: {
        'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
        allOf: [
          {
            name: a.name
            criterionType: 'StaticThresholdCriterion'
            metricNamespace: a.namespace
            metricName: a.metric
            operator: 'GreaterThan'
            threshold: a.threshold
            timeAggregation: a.aggregation
          }
        ]
      }
      actions: metricAlertActions
    }
  }
]

// Crash-looping replicas never reach Application Insights (e.g. SQL login failures at startup).
resource restartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (deployApp) {
  name: '${namePrefix}-restarts-${suffix}'
  location: 'global'
  properties: {
    description: 'A replica has restarted more than twice; check the container console logs.'
    severity: 1
    enabled: true
    scopes: [app.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'restarts'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.App/containerApps'
          metricName: 'RestartCount'
          operator: 'GreaterThan'
          threshold: 2
          timeAggregation: 'Maximum'
        }
      ]
    }
    actions: metricAlertActions
  }
}

// Fires when GET /api/audit/verify finds a broken hash chain (logged by app/routers/audit.py).
resource auditChainAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-audit-chain-invalid-${suffix}'
  location: location
  properties: {
    displayName: 'Audit chain verification failed'
    description: 'The audit hash chain failed verification: possible tampering. Investigate immediately.'
    severity: 0
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: 'traces | where message startswith "audit chain verification failed"'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: empty(alertEmail) ? [] : [alertGroup.id] }
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output appIdentityName string = appIdentity.name
output appIdentityClientId string = appIdentity.properties.clientId
output appUrl string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDbName
output digestStorageAccount string = digestStorage.name
