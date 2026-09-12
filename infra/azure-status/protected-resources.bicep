targetScope = 'resourceGroup'

param location string = resourceGroup().location
param appName string
param image string
param tenantId string
param authClientId string
param operatorObjectId string
param ingestObjectId string

var environmentName = '${appName}-env'
var storageName = take('awst${uniqueString(subscription().id, resourceGroup().id, appName)}', 24)
var keyVaultName = take('awst${uniqueString(resourceGroup().id, appName, 'kv')}', 24)
var containerName = 'status'
var blobName = 'current.json'
var authSecretName = 'microsoft-provider-authentication-secret'

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: '${appName}-identity'
}

resource storage 'Microsoft.Storage/storageAccounts@2025-06-01' existing = {
  name: storageName
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: environmentName
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
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: authSecretName
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${authSecretName}'
          identity: appIdentity.id
        }
      ]
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
              value: 'true'
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

resource auth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = {
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

output containerAppName string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
