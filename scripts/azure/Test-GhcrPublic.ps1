[CmdletBinding()]
param(
  [Parameter(Mandatory)] [ValidatePattern('^ghcr\.io/.+@sha256:[0-9a-f]{64}$')] [string] $Image
)

$ErrorActionPreference = "Stop"
$withoutRegistry = $Image.Substring("ghcr.io/".Length)
$parts = $withoutRegistry.Split("@", 2)
$repository = $parts[0].ToLowerInvariant()
$digest = $parts[1]
$tokenUri = "https://ghcr.io/token?service=ghcr.io&scope=repository:$repository`:pull"
$token = (Invoke-RestMethod -Method Get -Uri $tokenUri).token
if (-not $token) {
  throw "GHCR anonymous pull token を取得できませんでした。package visibility を public にしてください。"
}
$headers = @{
  Authorization = "Bearer $token"
  Accept = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json"
}
$response = Invoke-WebRequest -Method Head -Uri "https://ghcr.io/v2/$repository/manifests/$digest" -Headers $headers -SkipHttpErrorCheck
if ($response.StatusCode -ne 200) {
  throw "GHCR image は anonymous pull できません (HTTP $($response.StatusCode))。package visibility を public にしてください。"
}
Write-Output "GHCR anonymous pull verified: $Image"
