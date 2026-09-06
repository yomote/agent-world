targetScope = 'resourceGroup'

param location string = resourceGroup().location
param appName string
param image string
param confirmedBillingCurrency string
param budgetContactEmail string
param budgetAmount int
param budgetStartDate string
param enableProtectedIngress bool
param tenantId string
param authClientId string
param operatorObjectId string
param ingestObjectId string

var environmentName = '${appName}-env'
var workspaceName = '${appName}-logs'
var storageName = take('awst${uniqueString(subscription().id, resourceGroup().id, appName)}', 24)
var keyVaultName = take('awst${uniqueString(resourceGroup().id, appName, 'kv')}', 24)
var containerName = 'status'
var blobName = 'current.json'
var authSecretName = 'microsoft-provider-authentication-secret'
var blobContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var keyVaultSecretsOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${appName}-identity'
  location: location
  tags: {
    application: 'agent-world-management-status'
    managedBy: 'bicep'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2025-06-01' = {
  name: storageName
  location: location
  tags: {
    application: 'agent-world-management-status'
    managedBy: 'bicep'
  }
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-06-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: false
    }
    isVersioningEnabled: false
  }
}

resource statusContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource blobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(statusContainer.id, appIdentity.id, 'status-blob-contributor')
  scope: statusContainer
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobContributorRoleId
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: {
    application: 'agent-world-management-status'
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

resource keyVaultReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, appIdentity.id, 'status-key-vault-reader')
  scope: keyVault
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

resource keyVaultOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, operatorObjectId, 'status-key-vault-operator')
  scope: keyVault
  properties: {
    principalId: operatorObjectId
    roleDefinitionId: keyVaultSecretsOfficerRoleId
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: {
    application: 'agent-world-management-status'
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
  }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: environmentName
  location: location
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

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
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
        external: enableProtectedIngress
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: enableProtectedIngress ? [
        {
          name: authSecretName
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${authSecretName}'
          identity: appIdentity.id
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'management-status'
          image: image
          env: [
            {
              name: 'AGENT_WORLD_STATUS_BLOB_URL'
              value: '${storage.properties.primaryEndpoints.blob}${containerName}/${blobName}'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: appIdentity.properties.clientId
            }
            {
              name: 'AGENT_WORLD_STATUS_REQUIRE_AUTH'
              value: string(enableProtectedIngress)
            }
            {
              name: 'AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID'
              value: operatorObjectId
            }
            {
              name: 'AGENT_WORLD_STATUS_INGEST_OBJECT_ID'
              value: ingestObjectId
            }
          ]
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

resource auth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = if (enableProtectedIngress) {
  parent: app
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: [
        '/healthz'
      ]
    }
    httpSettings: {
      requireHttps: true
      routes: {
        apiPrefix: '/.auth'
      }
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: authClientId
          clientSecretSettingName: authSecretName
          openIdIssuer: '${az.environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedPrincipals: {
              identities: [
                operatorObjectId
                ingestObjectId
              ]
            }
          }
        }
      }
    }
    login: {
      tokenStore: {
        enabled: false
      }
    }
  }
}

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: '${appName}-monthly-${toLower(confirmedBillingCurrency)}'
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
        contactEmails: [budgetContactEmail]
        contactGroups: []
        contactRoles: []
        locale: 'ja-jp'
      }
      Forecast80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Forecasted'
        contactEmails: [budgetContactEmail]
        contactGroups: []
        contactRoles: []
        locale: 'ja-jp'
      }
      Actual100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: [budgetContactEmail]
        contactGroups: []
        contactRoles: []
        locale: 'ja-jp'
      }
    }
  }
}

output containerAppName string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output storageAccountName string = storage.name
output keyVaultName string = keyVault.name
output appIdentityClientId string = appIdentity.properties.clientId
output authSecretName string = authSecretName
