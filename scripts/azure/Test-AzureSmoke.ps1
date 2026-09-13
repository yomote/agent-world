[CmdletBinding()]
param(
  [Parameter(Mandatory)] [ValidatePattern('^https://')] [string] $BaseUrl,
  [Parameter(Mandatory)] [ValidateSet('entra', 'public')] [string] $AuthMode
)

$ErrorActionPreference = "Stop"
$base = $BaseUrl.TrimEnd("/")
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(15)
try {
  $health = $client.GetAsync("$base/healthz").GetAwaiter().GetResult()
  try {
    $healthStatus = [int]$health.StatusCode
    $healthContent = $health.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if ($healthStatus -ne 200 -or ($healthContent | ConvertFrom-Json).status -ne "ok") {
      throw "HTTPS health check failed: HTTP $healthStatus"
    }
  } finally {
    $health.Dispose()
  }

  foreach ($path in @("/", "/api/world")) {
    $response = $client.GetAsync("$base$path").GetAwaiter().GetResult()
    try {
      $status = [int]$response.StatusCode
      if ($AuthMode -eq "public" -and $status -ne 200) {
        throw "Public smoke failed for $path`: HTTP $status"
      }
      if ($AuthMode -eq "entra") {
        $location = if ($response.Headers.Location) { $response.Headers.Location.ToString() } else { '' }
        if ($status -notin @(302, 401) -or ($status -eq 302 -and $location -notmatch '/\.auth/login/aad')) {
          throw "Unauthenticated request was not rejected by Entra for $path`: HTTP $status"
        }
      }
    } finally {
      $response.Dispose()
    }
  }
} finally {
  $client.Dispose()
  $handler.Dispose()
}

Write-Output "HTTPS smoke passed: health=public app/api=$AuthMode"
