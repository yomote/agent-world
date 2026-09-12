[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string[]] $BudgetContactEmails
)

$ErrorActionPreference = 'Stop'
if ($BudgetContactEmails.Count -eq 0) {
  throw 'Budget通知先が1件以上必要です。'
}

foreach ($value in $BudgetContactEmails) {
  $address = $value.Trim()
  if (-not $address) {
    throw 'Budget通知先に空文字や空白は使えません。'
  }
  try {
    $parsed = [System.Net.Mail.MailAddress]::new($address)
  } catch {
    throw "Budget通知先のメール形式が不正です: $address"
  }
  if ($parsed.Address -ne $address) {
    throw "Budget通知先はメールアドレス単体で指定してください: $address"
  }
  Write-Output $address
}
