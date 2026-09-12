targetScope = 'subscription'

param resourceGroupName string
param location string
param appName string
param image string
param tenantId string
param authClientId string
param operatorObjectId string
param ingestObjectId string

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: resourceGroupName
}

module protectedResources 'protected-resources.bicep' = {
  name: 'management-status-protected-resources'
  scope: resourceGroup
  params: {
    location: location
    appName: appName
    image: image
    tenantId: tenantId
    authClientId: authClientId
    operatorObjectId: operatorObjectId
    ingestObjectId: ingestObjectId
  }
}

output containerAppName string = protectedResources.outputs.containerAppName
output fqdn string = protectedResources.outputs.fqdn
