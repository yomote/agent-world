[CmdletBinding()]
param(
  [string[]] $BudgetContactEmails = @(),
  [string] $ApprovedWhatIfSha256 = '',
  [string] $BillingCurrencyConfirmationPath = '',
  [string] $SubscriptionId = '',
  [string] $ResourceGroupName = ''
)

$ErrorActionPreference = 'Stop'
if ($BudgetContactEmails.Count -eq 0) {
  throw 'Applyには確認済みのBudget通知先が1件以上必要です。'
}
$null = & "$PSScriptRoot/Assert-BudgetContactEmails.ps1" -BudgetContactEmails $BudgetContactEmails
if ($ApprovedWhatIfSha256 -notmatch '^[0-9a-f]{64}$') {
  throw 'Applyには承認済みwhat-if SHA256が必要です。'
}
$null = & "$PSScriptRoot/Assert-AzureBillingCurrencyConfirmation.ps1" `
  -ConfirmationPath $BillingCurrencyConfirmationPath `
  -SubscriptionId $SubscriptionId `
  -ResourceGroupName $ResourceGroupName

Write-Output 'Azure Apply inputs are complete.'
