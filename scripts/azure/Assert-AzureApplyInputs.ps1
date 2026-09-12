[CmdletBinding()]
param(
  [string[]] $BudgetContactEmails = @(),
  [string] $ConfirmedBudgetCurrency = '',
  [string] $ApprovedWhatIfSha256 = ''
)

$ErrorActionPreference = 'Stop'
if ($BudgetContactEmails.Count -eq 0) {
  throw 'Applyには確認済みのBudget通知先が1件以上必要です。'
}
if ($ConfirmedBudgetCurrency -ne 'JPY') {
  throw 'Applyには実請求通貨JPYの確認が必要です。'
}
if ($ApprovedWhatIfSha256 -notmatch '^[0-9a-f]{64}$') {
  throw 'Applyには承認済みwhat-if SHA256が必要です。'
}

Write-Output 'Azure Apply inputs are complete.'
