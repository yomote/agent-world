targetScope = 'resourceGroup'

@description('既にcore templateで作成済みのContainer App')
param appName string

@description('single-tenant Entra tenant ID')
param tenantId string

@description('Easy Auth用app registrationのclient ID')
param clientId string

@minLength(1)
@description('許可する本人のEntra user object ID。一人以上を明示する')
param allowedPrincipalObjectIds array

resource containerApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: appName
}

resource auth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = {
  parent: containerApp
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
          clientId: clientId
          clientSecretSettingName: 'microsoft-provider-authentication-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedPrincipals: {
              identities: allowedPrincipalObjectIds
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

output authConfigId string = auth.id
output allowedPrincipalObjectIds array = allowedPrincipalObjectIds
