targetScope = 'resourceGroup'

param keyVaultName string
param appIdentityId string
param appIdentityPrincipalId string
param operatorPrincipalObjectId string

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource keyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, appIdentityId, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    principalId: appIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

resource operatorSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, operatorPrincipalObjectId, 'Key Vault Secrets Officer')
  scope: keyVault
  properties: {
    principalId: operatorPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}

output roleAssignmentId string = keyVaultSecretsUser.id
output operatorRoleAssignmentId string = operatorSecretsOfficer.id
