targetScope = 'subscription'

@description('Worldアプリと共有しない管理status専用Resource Group')
param resourceGroupName string = 'rg-agent-world-mgmt-jpe'

param location string = 'japaneast'
param appName string = 'agent-world-status-yomote-jpe'

@description('公開済みcontainer imageのdigest参照。tagだけの指定は禁止')
param image string

@allowed(['JPY'])
@description('Cost Managementで実請求通貨を確認してからJPYを渡す')
param confirmedBillingCurrency string

@minLength(3)
@description('本人が確認したBudget通知先')
param budgetContactEmail string

@minValue(1)
param budgetAmount int = 1000

@description('月初UTCのRFC 3339日時')
param budgetStartDate string

@description('Key Vault secret登録後だけtrueにする')
param enableProtectedIngress bool = false
param tenantId string = ''
param authClientId string = ''
param operatorObjectId string
param ingestObjectId string = ''

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    application: 'agent-world-management-status'
    managedBy: 'bicep'
    environment: 'production'
  }
}

module resources 'resources.bicep' = {
  name: 'management-status-resources'
  scope: resourceGroup
  params: {
    location: location
    appName: appName
    image: image
    confirmedBillingCurrency: confirmedBillingCurrency
    budgetContactEmail: budgetContactEmail
    budgetAmount: budgetAmount
    budgetStartDate: budgetStartDate
    enableProtectedIngress: enableProtectedIngress
    tenantId: tenantId
    authClientId: authClientId
    operatorObjectId: operatorObjectId
    ingestObjectId: ingestObjectId
  }
}

output resourceGroupId string = resourceGroup.id
output containerAppName string = resources.outputs.containerAppName
output fqdn string = resources.outputs.fqdn
output storageAccountName string = resources.outputs.storageAccountName
output keyVaultName string = resources.outputs.keyVaultName
output appIdentityClientId string = resources.outputs.appIdentityClientId
output authSecretName string = resources.outputs.authSecretName
