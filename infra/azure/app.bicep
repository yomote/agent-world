targetScope = 'resourceGroup'

param location string = resourceGroup().location
param appName string

@description('公開済みGHCR imageのdigest参照。tagだけの参照は禁止')
param image string

@minValue(1)
param budgetAmount int = 1000
param budgetStartDate string
param budgetContactEmails array = []
param enableEntraAuth bool = false
param externalIngress bool = false

var environmentName = '${appName}-env'
var workspaceName = '${appName}-logs'
var hasBudgetContact = length(budgetContactEmails) > 0
var keyVaultName = 'aw${uniqueString(subscription().id, resourceGroup().id, appName)}'
var authSecretName = 'easy-auth-client-secret'

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${appName}-identity'
  location: location
  tags: {
    application: 'agent-world'
    managedBy: 'bicep'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: {
    application: 'agent-world'
    managedBy: 'bicep'
  }
  properties: {
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: {
    application: 'agent-world'
    managedBy: 'bicep'
  }
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    sku: {
      name: 'PerGB2018'
    }
    workspaceCapping: {
      dailyQuotaGb: json('0.023')
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: environmentName
  location: location
  tags: {
    application: 'agent-world'
    managedBy: 'bicep'
  }
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  tags: {
    application: 'agent-world'
    managedBy: 'bicep'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      ingress: {
        external: externalIngress
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: enableEntraAuth ? [
        {
          name: 'microsoft-provider-authentication-secret'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${authSecretName}'
          identity: appIdentity.id
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'agent-world'
          image: image
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 1
              periodSeconds: 2
              timeoutSeconds: 2
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              periodSeconds: 30
              timeoutSeconds: 3
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = if (hasBudgetContact) {
  name: '${appName}-monthly'
  properties: {
    amount: budgetAmount
    category: 'Cost'
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      Actual50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: budgetContactEmails
        contactGroups: []
        contactRoles: []
        locale: 'ja-jp'
      }
      Forecast80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Forecasted'
        contactEmails: budgetContactEmails
        contactGroups: []
        contactRoles: []
        locale: 'ja-jp'
      }
      Actual100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: budgetContactEmails
        contactGroups: []
        contactRoles: []
        locale: 'ja-jp'
      }
    }
  }
}

output containerAppName string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output logWorkspaceName string = workspace.name
output budgetEnabled bool = hasBudgetContact
output keyVaultName string = keyVault.name
output authSecretName string = authSecretName
output appIdentityId string = appIdentity.id
output appIdentityPrincipalId string = appIdentity.properties.principalId
