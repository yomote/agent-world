targetScope = 'subscription'

@description('既存と重複しない専用Resource Group名')
param resourceGroupName string

@description('利用者が確認したAzure region')
param location string

@description('Container App名。Azure全体でFQDNが一意になる接頭辞を使う')
param appName string

@description('公開済みGHCR imageのdigest参照')
param image string

@minValue(1)
param budgetAmount int = 1000

@description('月初UTCのRFC 3339日時。例: 2026-09-01T00:00:00Z')
param budgetStartDate string

@description('請求通貨がJPYであることを確認した通知先。空配列ならBudgetをまだ作らない')
param budgetContactEmails array = []

@description('初回coreはfalse。Key VaultへEntra secret登録後のproduction desired stateはtrue')
param enableEntraAuth bool = false

@description('初回bootstrapはfalse。Entra auth actual確認後のproduction desired stateだけtrue')
param externalIngress bool = false

@description('Key Vault secretを初回登録・年次rotationする本人のEntra object ID')
param operatorPrincipalObjectId string

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    application: 'agent-world'
    managedBy: 'bicep'
    environment: 'production'
  }
}

module app 'app.bicep' = {
  name: 'agent-world-core'
  scope: resourceGroup
  params: {
    location: location
    appName: appName
    image: image
    budgetAmount: budgetAmount
    budgetStartDate: budgetStartDate
    budgetContactEmails: budgetContactEmails
    enableEntraAuth: enableEntraAuth
    externalIngress: externalIngress
  }
}

module access 'access.bicep' = {
  name: 'agent-world-bootstrap-access'
  scope: resourceGroup
  params: {
    keyVaultName: app.outputs.keyVaultName
    appIdentityId: app.outputs.appIdentityId
    appIdentityPrincipalId: app.outputs.appIdentityPrincipalId
    operatorPrincipalObjectId: operatorPrincipalObjectId
  }
}

output containerAppName string = app.outputs.containerAppName
output fqdn string = app.outputs.fqdn
output resourceGroupId string = resourceGroup.id
output keyVaultName string = app.outputs.keyVaultName
