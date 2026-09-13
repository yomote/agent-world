import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PLAN_GUARD = ROOT / "scripts" / "azure" / "Assert-AzureInitialPlan.ps1"
AUTH_PLAN_GUARD = ROOT / "scripts" / "azure" / "Assert-AzureAuthPlan.ps1"
CONTAINMENT = ROOT / "scripts" / "azure" / "Disable-AzureExternalIngress.ps1"
APPLY_INPUT_GUARD = ROOT / "scripts" / "azure" / "Assert-AzureApplyInputs.ps1"
BILLING_CONFIRMATION = ROOT / "scripts" / "azure" / "Assert-AzureBillingCurrencyConfirmation.ps1"
ENTRA_RESUME = ROOT / "scripts" / "azure" / "Assert-EntraResumeState.ps1"
CONFIGURE_ENTRA = ROOT / "scripts" / "azure" / "Configure-Entra.ps1"
RESUME_AFTER_KEY_VAULT = ROOT / "scripts" / "azure" / "Resume-EntraAfterKeyVault.ps1"
COMPLETE_ENTRA_ASSIGNMENT = ROOT / "scripts" / "azure" / "Complete-EntraAssignmentAndAuth.ps1"
TEST_ENTRA_DIRECTORY = ROOT / "scripts" / "azure" / "Test-EntraDirectory.ps1"
PUBLISH_EXTERNAL_INGRESS = ROOT / "scripts" / "azure" / "Publish-AzureExternalIngress.ps1"
BUDGET_EMAILS = ROOT / "scripts" / "azure" / "Assert-BudgetContactEmails.ps1"
EXTERNAL_DEPLOYMENT_FAILURE = (
    ROOT / "scripts" / "azure" / "Resolve-AzureExternalDeploymentFailure.ps1"
)
PWSH = shutil.which("pwsh")

RESUME_CLIENT_ID = "11111111-1111-1111-1111-111111111111"
RESUME_SP_ID = "22222222-2222-2222-2222-222222222222"
RESUME_USER_ID = "33333333-3333-3333-3333-333333333333"


def _run_entra_directory(
    tmp_path: Path, assignment_payload
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the external-ingress directory guard without contacting Azure."""
    log = tmp_path / "entra-directory-az.log"
    assignment_json = json.dumps(assignment_payload, separators=(",", ":"))
    wrapper = tmp_path / "run-entra-directory.ps1"
    wrapper.write_text(
        f"""
function global:az {{
  $joined = [string]::Join(' ', $args)
  Add-Content -LiteralPath '{log.as_posix()}' -Value $joined
  $global:LASTEXITCODE = 0
  if ($joined -match '^account show .*--output json$') {{
    return '{{"id":"sub-1","state":"Enabled","tenantId":"{RESUME_USER_ID}"}}'
  }}
  if ($joined -match '^account show .*--query user.type') {{ return 'user' }}
  if ($joined -match '^account show .*--query tenantId') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad signed-in-user show') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad app show') {{
    return '{{"signInAudience":"AzureADMyOrg","web":{{"redirectUris":["https://internal.example/.auth/login/aad/callback"],"implicitGrantSettings":{{"enableIdTokenIssuance":true}}}}}}'
  }}
  if ($joined -match '^containerapp show') {{ return 'internal.example' }}
  if ($joined -match '^ad sp show') {{
    return '{{"id":"{RESUME_SP_ID}","appRoleAssignmentRequired":true}}'
  }}
  if ($joined -match '^rest ' -and
      $joined.Contains('--query value[].{{resourceId:resourceId,principalId:principalId}}')) {{
    return '{assignment_json}'
  }}
  if ($joined -match '^rest ') {{
    return '{{"value":[{{"principalDisplayName":"tenant\\user"}}]}}'
  }}
  throw "Unexpected az call: $joined"
}}
try {{
  & '{TEST_ENTRA_DIRECTORY.as_posix()}' `
    -SubscriptionId sub-1 `
    -ResourceGroupName rg-agent-world-jpe `
    -AppName agent-world-yomote-jpe `
    -TenantId {RESUME_USER_ID} `
    -EntraClientId {RESUME_CLIENT_ID} `
    -AllowedPrincipalObjectIds @('{RESUME_USER_ID}')
}} catch {{
  [Console]::Error.WriteLine(($_ | Out-String))
  exit 1
}}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, calls


def _run_publish_external(
    tmp_path: Path,
    *,
    drift_after=False,
    smoke_fail=False,
    after_evidence_failure=False,
    external_exit=0,
    external_side_effect=True,
    callback_side_effect=True,
    callback_actual_read_failure=False,
    container_actual_read_failure=False,
    invalid_internal_fqdn=False,
    initial_provisioning_state="Succeeded",
    external_provisioning_state="Succeeded",
    baseline_drift="",
    auth_drift_after=False,
    app_drift_after=False,
    directory_drift_after=False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the two-write publication packet against a stateful fake Azure CLI."""
    log = tmp_path / "publish-calls.log"
    evidence = ROOT / "artifacts" / "test-publish" / tmp_path.name
    if evidence.is_dir():
        shutil.rmtree(evidence)
    elif evidence.exists():
        evidence.unlink()
    smoke = tmp_path / "smoke.ps1"
    smoke_action = "throw 'smoke failed'\n" if smoke_fail else "Write-Output 'smoke ok'\n"
    if after_evidence_failure:
        evidence_path = evidence.as_posix()
        smoke_action += f"Remove-Item -Recurse -Force '{evidence_path}'\n"
        smoke_action += f"Set-Content -LiteralPath '{evidence_path}' -Value blocked\n"
    smoke.write_text("param($BaseUrl,$AuthMode)\n" + smoke_action, encoding="utf-8")
    expected_identity = (
        "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
        "Microsoft.ManagedIdentity/userAssignedIdentities/agent-world-yomote-jpe-identity"
    )
    internal_fqdn = (
        "agent-world-yomote-jpe.internal.extra.env123.japaneast.azurecontainerapps.io"
        if invalid_internal_fqdn
        else "agent-world-yomote-jpe.internal.env123.japaneast.azurecontainerapps.io"
    )
    wrapper = tmp_path / "run-publish.ps1"
    wrapper.write_text(
        f"""
$global:external = $false
$global:internalFqdn = '{internal_fqdn}'
$global:callback = "https://$global:internalFqdn/.auth/login/aad/callback"
$global:image = 'ghcr.io/yomote/agent-world@sha256:{"1" * 64}'
$global:provisioning = '{initial_provisioning_state}'
$global:callbackWrites = 0
$global:baselineDrift = '{baseline_drift}'
function Get-ContainerJson {{
  $fqdn = if ($global:external) {{
    'agent-world-yomote-jpe.env123.japaneast.azurecontainerapps.io'
  }} else {{
    $global:internalFqdn
  }}
  @{{
    tags = @{{ application = 'agent-world'; managedBy = 'bicep' }}
    identity = @{{
      type = $(if ($global:baselineDrift -eq 'identity') {{
        'SystemAssigned'
      }} else {{
        'UserAssigned'
      }})
      userAssignedIdentities = @{{ '{expected_identity}' = @{{}} }}
    }}
    properties = @{{
      provisioningState = $global:provisioning
      environmentId = '/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/' +
        'Microsoft.App/managedEnvironments/agent-world-yomote-jpe-env'
      workloadProfileName = 'Consumption'
      configuration = @{{
        activeRevisionsMode = $(if ($global:baselineDrift -eq 'revision') {{
          'Multiple'
        }} else {{
          'Single'
        }})
        maxInactiveRevisions = 1
        secrets = @(@{{
          name = 'microsoft-provider-authentication-secret'
          keyVaultUrl = 'https://test-vault.vault.azure.net/secrets/easy-auth-client-secret'
          identity = '{expected_identity}'
        }})
        ingress = @{{
          external = $global:external; fqdn = $fqdn; targetPort = 8000
          transport = 'Auto'; allowInsecure = $false
          traffic = @(@{{ latestRevision = $true; weight = 100 }})
        }}
      }}
      template = @{{
        containers = @(@{{
          name = $(if ($global:baselineDrift -eq 'name') {{ 'foreign' }} else {{ 'agent-world' }})
          image = $global:image
          resources = @{{
            cpu = $(if ($global:baselineDrift -eq 'cpu') {{ 0.5 }} else {{ 0.25 }})
            memory = $(if ($global:baselineDrift -eq 'memory') {{ '1Gi' }} else {{ '0.5Gi' }})
          }}
          probes = @(
            @{{
              type=$(if ($global:baselineDrift -eq 'probe') {{'Liveness'}} else {{'Startup'}})
              httpGet=@{{path='/healthz';port=8000;scheme='HTTP'}}
              initialDelaySeconds=1;periodSeconds=2;timeoutSeconds=2;failureThreshold=30
            }},
            @{{
              type='Liveness';httpGet=@{{path='/healthz';port=8000;scheme='HTTP'}}
              periodSeconds=30;timeoutSeconds=3;failureThreshold=3
            }},
            @{{
              type='Readiness';httpGet=@{{path='/healthz';port=8000;scheme='HTTP'}}
              periodSeconds=10;timeoutSeconds=3;failureThreshold=3
            }}
          )
        }})
        scale = @{{
          minReplicas = 0
          maxReplicas = $(if ($global:baselineDrift -eq 'scale') {{ 2 }} else {{ 1 }})
          rules = @(@{{ name='http';http=@{{metadata=@{{concurrentRequests='10'}}}} }})
        }}
      }}
    }}
  }} | ConvertTo-Json -Depth 20 -Compress
}}
function global:az {{
  $joined = [string]::Join(' ', $args)
  Add-Content -LiteralPath '{log.as_posix()}' -Value $joined
  $global:LASTEXITCODE = 0
  if ($joined -match '^account show .*--output json$') {{
    return '{{"id":"sub-1","state":"Enabled","tenantId":"{RESUME_USER_ID}"}}'
  }}
  if ($joined -match '^account show .*--query user.type') {{ return 'user' }}
  if ($joined -match '^account show .*--query tenantId') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad signed-in-user show') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad app show') {{
    if (${str(callback_actual_read_failure).lower()} -and $global:callbackWrites -gt 0) {{
      $global:LASTEXITCODE = 1
      return
    }}
    return (@{{
      signInAudience = $(if ($global:external -and ${str(app_drift_after).lower()}) {{
        'AzureADMultipleOrgs'
      }} else {{
        'AzureADMyOrg'
      }})
      web = @{{
        redirectUris = @($global:callback)
        implicitGrantSettings = @{{ enableIdTokenIssuance = $true }}
      }}
    }} | ConvertTo-Json -Depth 8 -Compress)
  }}
  if ($joined -match '^ad app update') {{
    $global:callbackWrites++
    $index = [Array]::IndexOf($args, '--web-redirect-uris')
    if (${str(callback_side_effect).lower()}) {{
      $global:callback = $args[$index + 1]
    }} else {{
      $global:LASTEXITCODE = 1
    }}
    return
  }}
  if ($joined -match '^ad sp show') {{
    return '{{"id":"{RESUME_SP_ID}","appRoleAssignmentRequired":true}}'
  }}
  if ($joined -match '^rest ') {{
    if ($global:external -and ${str(directory_drift_after).lower()}) {{ return '[]' }}
    return '[{{"resourceId":"{RESUME_SP_ID}","principalId":"{RESUME_USER_ID}"}}]'
  }}
  if ($joined -match '^containerapp auth show') {{
    if ($global:external -and ${str(auth_drift_after).lower()}) {{
      return '{{"platform":{{"enabled":true}}}}'
    }}
    return '{{"platform":{{"enabled":true}},"globalValidation":{{"unauthenticatedClientAction":"RedirectToLoginPage","redirectToProvider":"azureactivedirectory","excludedPaths":["/healthz"]}},"httpSettings":{{"requireHttps":true}},"login":{{"tokenStore":{{"enabled":false}}}},"identityProviders":{{"azureActiveDirectory":{{"enabled":true,"registration":{{"clientId":"{RESUME_CLIENT_ID}","openIdIssuer":"https://login.microsoftonline.com/{RESUME_USER_ID}/v2.0","clientSecretSettingName":"microsoft-provider-authentication-secret"}},"validation":{{"defaultAuthorizationPolicy":{{"allowedPrincipals":{{"identities":["{RESUME_USER_ID}"]}}}}}}}}}}}}'
  }}
  if ($joined -match '^keyvault list') {{
    if ($global:baselineDrift -eq 'vault') {{ return '[]' }}
    return '[{{"name":"test-vault","id":"/subscriptions/sub-1/resourceGroups/' +
      'rg-agent-world-jpe/providers/Microsoft.KeyVault/vaults/test-vault",' +
      '"tags":{{"application":"agent-world"}}}}]'
  }}
  if ($joined -match '^containerapp show .*--query properties.configuration.ingress.fqdn') {{
    if ($global:external) {{
      return 'agent-world-yomote-jpe.env123.japaneast.azurecontainerapps.io'
    }}
    return $global:internalFqdn
  }}
  if ($joined -match '^containerapp show') {{
    if (${str(container_actual_read_failure).lower()} -and $global:external) {{
      $global:LASTEXITCODE = 1
      return
    }}
    return Get-ContainerJson
  }}
  if ($joined -match '^containerapp ingress enable') {{
    $typeIndex = [Array]::IndexOf($args, '--type')
    $requestedExternal = $args[$typeIndex + 1] -eq 'external'
    if (-not $requestedExternal -or ${str(external_side_effect).lower()}) {{
      $global:external = $requestedExternal
    }}
    $global:provisioning = if ($requestedExternal) {{
      '{external_provisioning_state}'
    }} else {{
      'Succeeded'
    }}
    if ($global:external -and ${str(drift_after).lower()}) {{
      $global:image = 'ghcr.io/yomote/agent-world@sha256:{"2" * 64}'
    }}
    if ($requestedExternal -and {external_exit}) {{ $global:LASTEXITCODE = {external_exit} }}
    return
  }}
  throw "Unexpected az call: $joined"
}}
try {{
  & '{PUBLISH_EXTERNAL_INGRESS.as_posix()}' `
    -SubscriptionId sub-1 `
    -ResourceGroupName rg-agent-world-jpe `
    -Location japaneast `
    -AppName agent-world-yomote-jpe `
    -TenantId {RESUME_USER_ID} `
    -EntraClientId {RESUME_CLIENT_ID} `
    -AllowedUserObjectId {RESUME_USER_ID} `
    -Image 'ghcr.io/yomote/agent-world@sha256:{"1" * 64}' `
    -EvidenceDirectory '{evidence.as_posix()}' `
    -SmokeScript '{smoke.as_posix()}'
}} catch {{
  [Console]::Error.WriteLine(($_ | Out-String))
  exit 1
}}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    if evidence.is_dir():
        shutil.rmtree(evidence)
    elif evidence.exists():
        evidence.unlink()
    return result, calls


def _resource_ids(subscription: str, group: str, app: str) -> list[str]:
    base = f"/subscriptions/{subscription}/resourceGroups/{group}"
    vault = f"{base}/providers/Microsoft.KeyVault/vaults/aw1234567890"
    return [
        base,
        f"{base}/providers/Microsoft.App/containerApps/{app}",
        f"{base}/providers/Microsoft.App/managedEnvironments/{app}-env",
        vault,
        f"{vault}/providers/Microsoft.Authorization/roleAssignments/11111111-1111-1111-1111-111111111111",
        f"{vault}/providers/Microsoft.Authorization/roleAssignments/22222222-2222-2222-2222-222222222222",
        f"{base}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{app}-identity",
        f"{base}/providers/Microsoft.OperationalInsights/workspaces/{app}-logs",
        f"{base}/providers/Microsoft.Consumption/budgets/{app}-monthly",
    ]


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_entra_directory_projects_ids_instead_of_parsing_display_names(tmp_path):
    """表示名にbackslashを含むfull Graph応答を読まず、必要ID projectionだけを検査する。"""
    payload = [
        {"resourceId": RESUME_SP_ID, "principalId": RESUME_USER_ID},
        {
            "resourceId": "44444444-4444-4444-4444-444444444444",
            "principalId": RESUME_USER_ID,
        },
    ]
    result, calls = _run_entra_directory(tmp_path, payload)
    assert result.returncode == 0, result.stderr
    assert "--query value[].{resourceId:resourceId,principalId:principalId}" in calls
    assert calls.count("rest --only-show-errors") == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_entra_directory_rejects_foreign_assignment(tmp_path):
    """本人に別app assignmentしかないstateを対象assignmentありへ丸めない。"""
    result, _ = _run_entra_directory(
        tmp_path,
        [
            {
                "resourceId": "44444444-4444-4444-4444-444444444444",
                "principalId": RESUME_USER_ID,
            }
        ],
    )
    assert result.returncode != 0
    assert "Allowed user is not assigned" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"resourceId": RESUME_SP_ID, "principalId": RESUME_USER_ID},
        [{"resourceId": RESUME_SP_ID}],
        [{"resourceId": "not-a-guid", "principalId": RESUME_USER_ID}],
    ],
)
def test_entra_directory_rejects_unknown_projection_shape(tmp_path, payload):
    """null/scalar/ID欠損をassignment無しまたは一致へ暗黙変換しない。"""
    result, _ = _run_entra_directory(tmp_path, payload)
    assert result.returncode != 0
    assert "projection JSON is unknown" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("external_exit", [0, 1])
def test_publish_external_changes_only_callback_and_ingress(tmp_path, external_exit):
    """公開writeの応答にかかわらずexact actualならcallback・ingress各1回で完了する。"""
    result, calls = _run_publish_external(tmp_path, external_exit=external_exit)
    assert result.returncode == 0, result.stderr
    assert calls.count("ad app update") == 1
    assert calls.count("containerapp ingress enable") == 1
    assert "--type external --allow-insecure false --target-port 8000 --transport auto" in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("failure", ["drift", "smoke"])
def test_publish_external_restores_internal_before_callback_on_failure(tmp_path, failure):
    """公開後の構成drift・smoke失敗では外部到達を先に閉じてcallbackも復旧する。"""
    result, calls = _run_publish_external(
        tmp_path,
        drift_after=failure == "drift",
        smoke_fail=failure == "smoke",
    )
    assert result.returncode != 0
    assert "internal ingress and callback were restored" in result.stderr
    assert calls.count("containerapp ingress enable") == 2
    assert calls.count("ad app update") == 2
    assert calls.index("--type internal") < calls.rindex("ad app update")


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_stops_before_ingress_when_callback_update_does_not_apply(tmp_path):
    """callback writeがnonzeroかつactual internalならingress公開へ進まない。"""
    result, calls = _run_publish_external(tmp_path, callback_side_effect=False)
    assert result.returncode != 0
    assert calls.count("ad app update") == 1
    assert "containerapp ingress enable" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_stops_unknown_when_callback_actual_cannot_be_read(tmp_path):
    """callback write後のactual読取不能は状態を推測せずingress前に停止する。"""
    result, calls = _run_publish_external(tmp_path, callback_actual_read_failure=True)
    assert result.returncode != 0
    assert "EXTERNAL PUBLICATION UNKNOWN" in result.stderr
    assert calls.count("ad app update") == 1
    assert "containerapp ingress enable" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_restores_callback_without_rewriting_internal_ingress(tmp_path):
    """external writeが効かずinternal actualならingressを再送せずcallbackだけ戻す。"""
    result, calls = _run_publish_external(
        tmp_path,
        external_exit=1,
        external_side_effect=False,
    )
    assert result.returncode != 0
    assert "ingress stayed internal" in result.stderr
    assert "callback was restored" in result.stderr
    assert calls.count("containerapp ingress enable") == 1
    assert calls.count("ad app update") == 2


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_rejects_unapproved_internal_fqdn_shape_before_writes(tmp_path):
    """extra label等の未知FQDNからpublic callbackを推測してwriteしない。"""
    result, calls = _run_publish_external(tmp_path, invalid_internal_fqdn=True)
    assert result.returncode != 0
    assert "6-label" in result.stderr
    assert "ad app update" not in calls
    assert "containerapp ingress enable" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("initial_state", ["Failed", "Canceled", "InProgress"])
def test_publish_external_requires_succeeded_baseline_before_writes(tmp_path, initial_state):
    """terminal失敗を含む非Succeeded baselineから公開writeを始めない。"""
    result, calls = _run_publish_external(
        tmp_path,
        initial_provisioning_state=initial_state,
    )
    assert result.returncode != 0
    assert "baseline" in result.stderr
    assert "ad app update" not in calls
    assert "containerapp ingress enable" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    "baseline_drift",
    ["identity", "revision", "name", "cpu", "memory", "probe", "scale", "vault"],
)
def test_publish_external_rejects_baseline_drift_before_writes(tmp_path, baseline_drift):
    """承認済みsingle-worker baselineの各driftを既存状態として公開しない。"""
    result, calls = _run_publish_external(tmp_path, baseline_drift=baseline_drift)
    assert result.returncode != 0
    assert "ad app update" not in calls
    assert "containerapp ingress enable" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_rejects_evidence_path_outside_private_artifacts(tmp_path):
    """snapshotをdocs等のtracked領域やrepository外へ保存させない。"""
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(PUBLISH_EXTERNAL_INGRESS),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-Location",
            "japaneast",
            "-AppName",
            "agent-world-yomote-jpe",
            "-TenantId",
            RESUME_USER_ID,
            "-EntraClientId",
            RESUME_CLIENT_ID,
            "-AllowedUserObjectId",
            RESUME_USER_ID,
            "-Image",
            f"ghcr.io/yomote/agent-world@sha256:{'1' * 64}",
            "-EvidenceDirectory",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "artifacts" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_rejects_mutable_image_reference_before_execution(tmp_path):
    """tagや不正digestを公開対象として受理しない。"""
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(PUBLISH_EXTERNAL_INGRESS),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-Location",
            "japaneast",
            "-AppName",
            "agent-world-yomote-jpe",
            "-TenantId",
            RESUME_USER_ID,
            "-EntraClientId",
            RESUME_CLIENT_ID,
            "-AllowedUserObjectId",
            RESUME_USER_ID,
            "-Image",
            "ghcr.io/yomote/agent-world:latest",
            "-EvidenceDirectory",
            str(ROOT / "artifacts" / "test-publish-invalid-image"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "Cannot validate argument" in result.stderr
    assert "Image" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("post_drift", ["auth", "app", "directory"])
def test_publish_external_contains_postcondition_drift(tmp_path, post_drift):
    """公開後のauth/app/directory driftを成功扱いせずexternalを閉じる。"""
    result, calls = _run_publish_external(
        tmp_path,
        auth_drift_after=post_drift == "auth",
        app_drift_after=post_drift == "app",
        directory_drift_after=post_drift == "directory",
    )
    assert result.returncode != 0
    assert calls.count("containerapp ingress enable") == 2
    assert calls.count("ad app update") == 2


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_does_not_republish_when_after_evidence_cannot_be_saved(tmp_path):
    """auth/smoke成功後のsnapshot失敗で正常な公開writeを巻き戻さない。"""
    result, calls = _run_publish_external(tmp_path, after_evidence_failure=True)
    assert result.returncode == 0
    output = result.stdout + result.stderr
    assert "EXTERNAL PUBLICATION SUCCEEDED" in output
    assert "Do not republish" in output
    assert calls.count("containerapp ingress enable") == 1
    assert calls.count("ad app update") == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_stops_unknown_nonterminal_without_competing_writes(tmp_path):
    """nonterminal ingressは後から公開へ変わり得るためcontainmentやcallbackを競合送信しない。"""
    result, calls = _run_publish_external(
        tmp_path,
        external_exit=1,
        external_side_effect=False,
        external_provisioning_state="InProgress",
    )
    assert result.returncode != 0
    assert "EXTERNAL PUBLICATION UNKNOWN" in result.stderr
    assert calls.count("containerapp ingress enable") == 1
    assert calls.count("ad app update") == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("terminal_state", ["Failed", "Canceled"])
def test_publish_external_contains_external_after_terminal_failure(tmp_path, terminal_state):
    """terminal失敗でexternalなら成功扱いせずinternal化後にcallbackを復旧する。"""
    result, calls = _run_publish_external(
        tmp_path,
        external_provisioning_state=terminal_state,
    )
    assert result.returncode != 0
    assert "PUBLICATION FAILURE" in result.stderr
    assert "callback" in result.stderr
    assert calls.count("containerapp ingress enable") == 2
    assert calls.count("ad app update") == 2


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_publish_external_stops_unknown_when_ingress_actual_cannot_be_read(tmp_path):
    """ingress write後のactual読取不能ではcontainmentもcallback復旧も競合送信しない。"""
    result, calls = _run_publish_external(
        tmp_path,
        external_exit=1,
        container_actual_read_failure=True,
    )
    assert result.returncode != 0
    assert "EXTERNAL PUBLICATION UNKNOWN" in result.stderr
    assert calls.count("containerapp ingress enable") == 1
    assert calls.count("ad app update") == 1


def _run_plan_guard(
    tmp_path: Path, changes: list[dict[str, str]]
) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "what-if.json"
    plan.write_text(json.dumps({"status": "Succeeded", "changes": changes}), encoding="utf-8")
    return subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(PLAN_GUARD),
            "-WhatIfPath",
            str(plan),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _run_apply_input_guard(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(APPLY_INPUT_GUARD), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _write_billing_confirmation(
    tmp_path: Path,
    *,
    subscription: str = "sub-1",
    resource_group: str = "rg-agent-world-jpe",
    currency: str = "JPY",
    **overrides,
) -> Path:
    confirmation = tmp_path / "billing-currency-confirmation.private.json"
    confirmation.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "method": "Azure BillingProperty REST 2024-04-01",
                "subscriptionId": subscription,
                "budgetScopeResourceId": (
                    f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
                ),
                "currency": currency,
                "currencyConfirmedAtUtc": "2026-09-07T15:09:46.8412110Z",
                "budgetScopeConfirmationMethod": (
                    "Azure Portal Budgets list: same-subscription resource-group "
                    "budgets displayed in JPY; target resource group not created"
                ),
                "budgetScopeConfirmedAtUtc": "2026-09-13T00:00:00Z",
            }
            | overrides
        ),
        encoding="utf-8",
    )
    return confirmation


def _run_entra_resume_guard(tmp_path: Path, **overrides):
    guard_arguments = overrides.pop("_guard_arguments", [])
    actual = {
        "application": {
            "appId": RESUME_CLIENT_ID,
            "displayName": "agent-world-yomote-jpe-login",
            "signInAudience": "AzureADMyOrg",
            "web": {
                "redirectUris": ["https://internal.example/.auth/login/aad/callback"],
                "implicitGrantSettings": {"enableIdTokenIssuance": True},
            },
        },
        "servicePrincipal": {
            "id": RESUME_SP_ID,
            "appId": RESUME_CLIENT_ID,
            "displayName": "agent-world-yomote-jpe-login",
            "servicePrincipalType": "Application",
            "appRoleAssignmentRequired": False,
        },
        "credentials": [],
        "assignments": [],
        "auth": {"platform": {}, "identityProviders": {"azureActiveDirectory": {}}},
    }
    omit_assignments = overrides.pop("_omit_assignments", False)
    actual.update(overrides)
    if omit_assignments:
        del actual["assignments"]
    path = tmp_path / "entra-resume-actual.json"
    path.write_text(json.dumps(actual), encoding="utf-8")
    return subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ENTRA_RESUME),
            "-ActualPath",
            str(path),
            "-AppName",
            "agent-world-yomote-jpe",
            "-ExpectedRedirectUri",
            "https://internal.example/.auth/login/aad/callback",
            "-ClientId",
            RESUME_CLIENT_ID,
            "-ServicePrincipalObjectId",
            RESUME_SP_ID,
            "-AllowedUserObjectId",
            RESUME_USER_ID,
            *guard_arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _run_configure_entra_resume(
    tmp_path: Path,
    external,
    *,
    recover_credential=False,
    service_principal_assignment_required=None,
    metadata_count=1,
    foreign_metadata=False,
):
    log = tmp_path / "az-calls.log"
    ingress_external = "null" if external is None else str(external).lower()
    if service_principal_assignment_required is None:
        service_principal_assignment_required = recover_credential
    service_principal_json = json.dumps(
        {
            "id": RESUME_SP_ID,
            "appId": RESUME_CLIENT_ID,
            "displayName": "agent-world-yomote-jpe-login",
            "servicePrincipalType": "Application",
            "appRoleAssignmentRequired": service_principal_assignment_required,
        },
        separators=(",", ":"),
    )
    auth_json = json.dumps(
        {"platform": {}, "identityProviders": {"azureActiveDirectory": {}}},
        separators=(",", ":"),
    )
    identity_json = json.dumps(
        {
            "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
            "Microsoft.ManagedIdentity/userAssignedIdentities/"
            "agent-world-yomote-jpe-identity": {}
        },
        separators=(",", ":"),
    )
    vault_json = json.dumps(
        [
            {
                "name": "test-vault",
                "id": "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/"
                "providers/Microsoft.KeyVault/vaults/test-vault",
                "tags": {"application": "agent-world"},
            }
        ],
        separators=(",", ":"),
    )
    assignment_projection = "--query value[].{resourceId:resourceId,principalId:principalId}"
    unprojected_assignment = r'{"value":[{"principalDisplayName":"tenant\user"}]}'
    orphan_display = "agent-world-easy-auth-20260913085447-1cdb7362"
    orphan_key = "55555555-5555-5555-5555-555555555555"
    initial_credentials = json.dumps(
        [
            {
                "keyId": orphan_key,
                "displayName": orphan_display,
                "endDateTime": "2027-09-13T08:54:47Z",
            }
        ]
        if recover_credential
        else [],
        separators=(",", ":"),
    )
    recovery_arguments = (
        f" -ResumeOrphanCredentialKeyId {orphan_key}"
        f" -ResumeOrphanCredentialDisplayName {orphan_display}"
        if recover_credential
        else ""
    )
    metadata_entries = ",".join(
        [
            '{`"keyId`":`"66666666-6666-6666-6666-666666666666`",'
            '`"displayName`":`"$script:newDisplay`",'
            '`"endDateTime`":`"2027-09-13T09:00:00Z`"}'
        ]
        * metadata_count
    )
    if foreign_metadata:
        foreign = (
            '{`"keyId`":`"77777777-7777-7777-7777-777777777777`",'
            '`"displayName`":`"foreign`",'
            '`"endDateTime`":`"2027-09-13T09:00:00Z`"}'
        )
        metadata_entries = f"{metadata_entries},{foreign}"
    wrapper = tmp_path / "run-configure-resume.ps1"
    wrapper.write_text(
        f"""
function global:Invoke-RestMethod {{ return @{{ id = 'fake-secret-version' }} }}
function global:az {{
  $joined = [string]::Join(' ', $args)
  Add-Content -LiteralPath '{log.as_posix()}' -Value $joined
  $global:LASTEXITCODE = 0
  if ($joined -match '^account show .*--output json$') {{
    return '{{"id":"sub-1","state":"Enabled","tenantId":"{RESUME_USER_ID}"}}'
  }}
  if ($joined -match '^account show .*--query user.type') {{ return 'user' }}
  if ($joined -match '^account show .*--query tenantId') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad signed-in-user show') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^containerapp show .*properties.configuration.ingress') {{
    return '{{"fqdn":"internal.example","external":{ingress_external}}}'
  }}
  if ($joined -match '^ad app list') {{ return '{RESUME_CLIENT_ID}' }}
  if ($joined -match '^ad app show') {{
    return '{{"appId":"{RESUME_CLIENT_ID}","displayName":"agent-world-yomote-jpe-login","signInAudience":"AzureADMyOrg","web":{{"redirectUris":["https://internal.example/.auth/login/aad/callback"],"implicitGrantSettings":{{"enableIdTokenIssuance":true}}}}}}'
  }}
  if ($joined -match '^ad sp show') {{
    return '{service_principal_json}'
  }}
  if ($joined -match '^ad app credential delete') {{ $script:deleted = $true; return }}
  if ($joined -match '^ad app credential reset') {{
    $script:created = $true
    $script:newDisplay = ([regex]::Match($joined, '--display-name ([^ ]+)')).Groups[1].Value
    return '{{"password":"not-a-real-secret"}}'
  }}
  if ($joined -match '^ad app credential list' -and
      $joined.Contains('--query [].{{keyId:keyId}}')) {{ return '[]' }}
  if ($joined -match '^ad app credential list') {{
    if ($script:created) {{ return "[{metadata_entries}]" }}
    return '{initial_credentials}'
  }}
  if ($joined -match '^rest .*--method post') {{ return }}
  if ($joined -match '^rest .*appRoleAssignments' -and
      $joined.Contains('{assignment_projection}')) {{
    return '[]'
  }}
  if ($joined -match '^rest .*appRoleAssignments') {{ return '{unprojected_assignment}' }}
  if ($joined -match '^containerapp auth show') {{ return '{auth_json}' }}
  if ($joined -match '^ad sp update') {{ $global:LASTEXITCODE = 1; return }}
  if ($joined -match '^keyvault list') {{ return '{vault_json}' }}
  if ($joined -match '^containerapp show' -and
      $joined.Contains('identity.userAssignedIdentities')) {{
    return '{identity_json}'
  }}
  if ($joined -match '^account get-access-token') {{ return 'fake-token' }}
  if ($joined -match '^containerapp secret set') {{ return }}
  if ($joined -match '^deployment group create') {{ return }}
  throw "Unexpected az call: $joined"
}}
try {{
  & '{CONFIGURE_ENTRA.as_posix()}' `
    -SubscriptionId sub-1 `
    -ResourceGroupName rg-agent-world-jpe `
    -AppName agent-world-yomote-jpe `
    -AllowedUserObjectId {RESUME_USER_ID} `
    -ResumeTenantId {RESUME_USER_ID} `
    -ResumeClientId {RESUME_CLIENT_ID} `
    -ResumeServicePrincipalObjectId {RESUME_SP_ID}{recovery_arguments}
}} catch {{
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, calls


def _run_resume_after_key_vault(
    tmp_path: Path,
    *,
    identities=None,
    secret_matches=True,
    vault_count=1,
    secret_version_count=1,
    container_secret_exists=False,
    credential_root="array",
    secret_version_root="array",
    container_secret_root="empty",
    vault_root="array",
    ingress_external=False,
    secret_enabled=True,
    resume_existing_ref=False,
    fail_stage=None,
):
    log = tmp_path / "az-after-key-vault.log"
    credential_display = "agent-world-easy-auth-20260913090000-abcdef12"
    credential_key = "66666666-6666-6666-6666-666666666666"
    credential_expiry = "2027-09-13T09:00:00Z"
    secret_version = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    expected_identity = (
        "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
        "Microsoft.ManagedIdentity/userAssignedIdentities/agent-world-yomote-jpe-identity"
    )
    if identities is None:
        identities = {expected_identity: {}}
    secret_key = credential_key if secret_matches else RESUME_SP_ID
    credential_entry = {
        "keyId": credential_key,
        "displayName": credential_display,
        "endDateTime": credential_expiry,
    }
    credential_payload = {
        "array": [credential_entry],
        "scalar": credential_entry,
        "null": None,
    }[credential_root]
    credential_json = json.dumps(credential_payload, separators=(",", ":"))
    secret_versions = [
        {
            "name": "easy-auth-client-secret",
            "id": f"https://test-vault.vault.azure.net/secrets/easy-auth-client-secret/{secret_version}",
            "attributes": {"enabled": secret_enabled},
            "tags": {
                "entraCredentialKeyId": secret_key,
                "entraCredentialDisplayName": credential_display,
                "expiresOn": credential_expiry,
            },
        }
    ] * secret_version_count
    secret_version_payload = {
        "array": secret_versions,
        "scalar": secret_versions[0] if secret_versions else {},
        "null": None,
    }[secret_version_root]
    secret_json = json.dumps(secret_version_payload, separators=(",", ":"))
    base_secret_json = json.dumps(
        [
            {
                "name": "easy-auth-client-secret",
                "id": "https://test-vault.vault.azure.net/secrets/easy-auth-client-secret",
                "attributes": {"enabled": True},
                "tags": {},
            }
        ],
        separators=(",", ":"),
    )
    identity_json = json.dumps(identities, separators=(",", ":"))
    vaults = [
        {
            "name": f"test-vault-{index}" if index else "test-vault",
            "id": (
                "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
                f"Microsoft.KeyVault/vaults/{'test-vault-' + str(index) if index else 'test-vault'}"
            ),
            "tags": {"application": "agent-world"},
        }
        for index in range(vault_count)
    ]
    vault_payload = {
        "array": vaults,
        "scalar": vaults[0] if vaults else {},
        "null": None,
    }[vault_root]
    vault_json = json.dumps(vault_payload, separators=(",", ":"))
    account_json = json.dumps({"id": "sub-1", "state": "Enabled"}, separators=(",", ":"))
    sp_json = json.dumps(
        {
            "id": RESUME_SP_ID,
            "appId": RESUME_CLIENT_ID,
            "displayName": "agent-world-yomote-jpe-login",
            "servicePrincipalType": "Application",
            "appRoleAssignmentRequired": True,
        },
        separators=(",", ":"),
    )
    auth_json = json.dumps(
        {"platform": {}, "identityProviders": {"azureActiveDirectory": {}}},
        separators=(",", ":"),
    )
    container_secrets = (
        [
            {
                "name": "microsoft-provider-authentication-secret",
                "keyVaultUrl": "https://test-vault.vault.azure.net/secrets/easy-auth-client-secret",
                "identity": expected_identity,
            }
        ]
        if container_secret_exists or resume_existing_ref
        else []
    )
    effective_container_root = (
        "array"
        if (container_secret_exists or resume_existing_ref) and container_secret_root == "empty"
        else container_secret_root
    )
    container_secret_payload = {
        "array": container_secrets,
        "empty": "",
        "scalar": {"name": "foreign"},
        "null": None,
    }[effective_container_root]
    container_secrets_json = (
        ""
        if effective_container_root == "empty"
        else json.dumps(container_secret_payload, separators=(",", ":"))
    )
    existing_ref_argument = " -ResumeExistingSecretReference" if resume_existing_ref else ""
    wrapper = tmp_path / "run-resume-after-key-vault.ps1"
    wrapper.write_text(
        f"""
function global:az {{
  $joined = [string]::Join(' ', $args)
  Add-Content -LiteralPath '{log.as_posix()}' -Value $joined
  $global:LASTEXITCODE = 0
  if ($joined -match '^account show .*--output json$') {{ return '{account_json}' }}
  if ($joined -match '^account show .*--query user.type') {{ return 'user' }}
  if ($joined -match '^account show .*--query tenantId') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad signed-in-user show') {{ return '{RESUME_USER_ID}' }}
  if ($joined -match '^ad app list') {{ return '{RESUME_CLIENT_ID}' }}
  if ($joined -match '^containerapp show .*properties.configuration.ingress') {{
    return '{{"fqdn":"internal.example","external":{json.dumps(ingress_external)}}}'
  }}
  if ($joined -match '^ad app show') {{
    return '{{"appId":"{RESUME_CLIENT_ID}","displayName":"agent-world-yomote-jpe-login","signInAudience":"AzureADMyOrg","web":{{"redirectUris":["https://internal.example/.auth/login/aad/callback"],"implicitGrantSettings":{{"enableIdTokenIssuance":true}}}}}}'
  }}
  if ($joined -match '^ad sp show') {{
    return '{sp_json}'
  }}
  if ($joined -match '^ad app credential list') {{ return '{credential_json}' }}
  if ($joined -match '^rest .*--method post') {{
    $bodyIndex = [Array]::IndexOf($args, '--body')
    $headerIndex = [Array]::IndexOf($args, '--headers')
    if ($bodyIndex -lt 0 -or $headerIndex -lt 0 -or
        $args[$headerIndex + 1] -ne 'Content-Type=application/json' -or
        -not $args[$bodyIndex + 1].StartsWith('@')) {{
      $global:LASTEXITCODE = 2
      return
    }}
    $assignmentPath = $args[$bodyIndex + 1].Substring(1)
    $assignment = Get-Content -Raw -LiteralPath $assignmentPath | ConvertFrom-Json
    if ($assignment.principalId -ne '{RESUME_USER_ID}' -or
        $assignment.resourceId -ne '{RESUME_SP_ID}' -or
        $assignment.appRoleId -ne '00000000-0000-0000-0000-000000000000') {{
      $global:LASTEXITCODE = 2
      return
    }}
    if ('{fail_stage}' -eq 'assignment') {{ $global:LASTEXITCODE = 1 }}
    return
  }}
  if ($joined -match '^rest .*appRoleAssignments') {{ return '[]' }}
  if ($joined -match '^containerapp auth show') {{ return '{auth_json}' }}
  if ($joined -match '^keyvault list') {{ return '{vault_json}' }}
  if ($joined -match '^keyvault secret list-versions') {{ return '{secret_json}' }}
  if ($joined -match '^keyvault secret list') {{ return '{base_secret_json}' }}
  if ($joined -match '^containerapp secret list') {{
    return '{container_secrets_json}'
  }}
  if ($joined -match '^containerapp show .*identity.userAssignedIdentities') {{
    return '{identity_json}'
  }}
  if ($joined -match '^containerapp secret set') {{
    if ('{fail_stage}' -eq 'secret-ref') {{ $global:LASTEXITCODE = 1 }}
    return
  }}
  if ($joined -match '^deployment group create') {{
    if ('{fail_stage}' -eq 'auth') {{ $global:LASTEXITCODE = 1 }}
    return
  }}
  throw "Unexpected az call: $joined"
}}
try {{
  & '{RESUME_AFTER_KEY_VAULT.as_posix()}' `
    -SubscriptionId sub-1 `
    -ResourceGroupName rg-agent-world-jpe `
    -AppName agent-world-yomote-jpe `
    -TenantId {RESUME_USER_ID} `
    -ClientId {RESUME_CLIENT_ID} `
    -ServicePrincipalObjectId {RESUME_SP_ID} `
    -AllowedUserObjectId {RESUME_USER_ID} `
    -CredentialKeyId {credential_key} `
    -CredentialDisplayName {credential_display} `
    -CredentialExpiresOn '{credential_expiry}' `
    -KeyVaultSecretVersion {secret_version}{existing_ref_argument}
}} catch {{
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, calls


def _run_budget_email_guard(*addresses: str) -> subprocess.CompletedProcess[str]:
    values = ",".join("'" + address.replace("'", "''") + "'" for address in addresses)
    command = f"& '{BUDGET_EMAILS.as_posix()}' -BudgetContactEmails @({values})"
    return subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_budget_email_guard_accepts_and_trims_multiple_addresses():
    """複数の妥当な通知先をtrim済み値として渡す。"""
    result = _run_budget_email_guard(" owner@example.invalid ", "ops@example.invalid")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["owner@example.invalid", "ops@example.invalid"]


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("address", ["", "   ", "not-an-email", "Owner <owner@example.invalid>"])
def test_budget_email_guard_rejects_blank_or_invalid_address(address):
    """空白・空文字・不正形式でApplyやGitHub書き込みへ進まない。"""
    result = _run_budget_email_guard(address)
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_apply_input_guard_accepts_complete_approved_inputs(tmp_path):
    """通知先・初回通貨確認記録・承認済みplanが揃う正常stageを通す。"""
    confirmation = _write_billing_confirmation(tmp_path)
    result = _run_apply_input_guard(
        "-BudgetContactEmails",
        "owner@example.invalid",
        "-ApprovedWhatIfSha256",
        "a" * 64,
        "-BillingCurrencyConfirmationPath",
        str(confirmation),
        "-SubscriptionId",
        "sub-1",
        "-ResourceGroupName",
        "rg-agent-world-jpe",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("missing", ["contact", "confirmation", "hash"])
def test_apply_input_guard_rejects_incomplete_approval(tmp_path, missing):
    """Budgetやplanの未承認入力をAzure照会より前に停止する。"""
    confirmation = _write_billing_confirmation(tmp_path)
    complete = [
        "-BudgetContactEmails",
        "owner@example.invalid",
        "-ApprovedWhatIfSha256",
        "a" * 64,
        "-BillingCurrencyConfirmationPath",
        str(confirmation),
        "-SubscriptionId",
        "sub-1",
        "-ResourceGroupName",
        "rg-agent-world-jpe",
    ]
    arguments = {
        "contact": complete[2:],
        "confirmation": complete[:4] + complete[6:],
        "hash": complete[:2] + complete[4:],
    }[missing]
    result = _run_apply_input_guard(*arguments)
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("mismatch", ["currency", "subscription", "scope"])
def test_billing_confirmation_rejects_unapproved_target(tmp_path, mismatch):
    """非JPYや別subscription・Budget scopeの記録を承認済み扱いしない。"""
    confirmation = _write_billing_confirmation(
        tmp_path,
        currency="USD" if mismatch == "currency" else "JPY",
        subscription="sub-2" if mismatch == "subscription" else "sub-1",
        resource_group="other-rg" if mismatch == "scope" else "rg-agent-world-jpe",
    )
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(BILLING_CONFIRMATION),
            "-ConfirmationPath",
            str(confirmation),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", "1"),
        ("method", "operator guess"),
        ("budgetScopeConfirmationMethod", "operator guess"),
        ("currencyConfirmedAtUtc", "2026-02-31T00:00:00Z"),
        ("budgetScopeConfirmedAtUtc", "2999-01-01T00:00:00Z"),
    ],
)
def test_billing_confirmation_rejects_invalid_schema_method_or_time(tmp_path, field, value):
    """型違い、未承認の確認方法、不正・未来時刻を初回証拠として通さない。"""
    confirmation = _write_billing_confirmation(tmp_path, **{field: value})
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(BILLING_CONFIRMATION),
            "-ConfirmationPath",
            str(confirmation),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    "field",
    [
        "method",
        "budgetScopeConfirmationMethod",
        "currencyConfirmedAtUtc",
        "budgetScopeConfirmedAtUtc",
    ],
)
def test_billing_confirmation_rejects_missing_method_or_time(tmp_path, field):
    """確認方法・時刻が欠けた記録を暗黙の既定値で補わない。"""
    confirmation = _write_billing_confirmation(tmp_path)
    record = json.loads(confirmation.read_text(encoding="utf-8"))
    del record[field]
    confirmation.write_text(json.dumps(record), encoding="utf-8")
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(BILLING_CONFIRMATION),
            "-ConfirmationPath",
            str(confirmation),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_entra_resume_guard_accepts_only_known_partial_state(tmp_path):
    """Graph伝播失敗後のapp/SPだけが残る既知stateからだけ再開する。"""
    result = _run_entra_resume_guard(tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("unexpected", ["app", "credential", "assignment", "auth"])
def test_entra_resume_guard_rejects_target_drift_or_unknown_side_effect(tmp_path, unexpected):
    """対象違いと既credential・assignment・authを新規発行で上書きしない。"""
    overrides = {}
    if unexpected == "app":
        overrides["application"] = {
            "appId": "44444444-4444-4444-4444-444444444444",
            "displayName": "other",
            "signInAudience": "AzureADMultipleOrgs",
            "web": {"redirectUris": [], "implicitGrantSettings": {}},
        }
    elif unexpected == "credential":
        overrides["credentials"] = [{"keyId": "unknown"}]
    elif unexpected == "assignment":
        overrides["assignments"] = [{"resourceId": RESUME_SP_ID, "principalId": RESUME_USER_ID}]
    else:
        overrides["auth"] = {
            "platform": {"enabled": True},
            "identityProviders": {"azureActiveDirectory": {"enabled": True}},
        }
    result = _run_entra_resume_guard(tmp_path, **overrides)
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    "assignments",
    [
        None,
        {},
        [{"resourceId": RESUME_SP_ID}],
        [{"resourceId": "not-a-guid", "principalId": RESUME_USER_ID}],
    ],
)
def test_entra_resume_guard_rejects_unknown_assignment_shape(tmp_path, assignments):
    """null・object・必要ID欠損をassignmentなしと解釈しない。"""
    result = _run_entra_resume_guard(tmp_path, assignments=assignments)
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_entra_resume_guard_rejects_missing_assignments_property(tmp_path):
    """projection結果property欠損を空arrayとして補わない。"""
    result = _run_entra_resume_guard(tmp_path, _omit_assignments=True)
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_entra_resume_guard_allows_well_formed_foreign_assignment(tmp_path):
    """本人の別enterprise app assignmentは対象SPへの割当てと混同しない。"""
    result = _run_entra_resume_guard(
        tmp_path,
        assignments=[
            {
                "resourceId": "44444444-4444-4444-4444-444444444444",
                "principalId": RESUME_USER_ID,
            }
        ],
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_entra_resume_guard_accepts_only_the_exact_orphan_credential(tmp_path):
    """今回作成済みの一意な未使用credentialだけを削除対象にする。"""
    display_name = "agent-world-easy-auth-20260913085447-1cdb7362"
    result = _run_entra_resume_guard(
        tmp_path,
        servicePrincipal={
            "id": RESUME_SP_ID,
            "appId": RESUME_CLIENT_ID,
            "displayName": "agent-world-yomote-jpe-login",
            "servicePrincipalType": "Application",
            "appRoleAssignmentRequired": True,
        },
        credentials=[
            {
                "keyId": "55555555-5555-5555-5555-555555555555",
                "displayName": display_name,
                "endDateTime": "2027-09-13T08:54:47Z",
            }
        ],
        _guard_arguments=[
            "-OrphanCredentialKeyId",
            "55555555-5555-5555-5555-555555555555",
            "-OrphanCredentialDisplayName",
            display_name,
        ],
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_configure_entra_resume_accepts_single_scalar_app_and_reaches_update(tmp_path):
    """1件だけのTSV appIdをUUID先頭文字に崩さず、検証後の同SP更新へ進む。"""
    result, calls = _run_configure_entra_resume(tmp_path, external=False)
    assert result.returncode != 0
    assert "Entra assignment requirement update failed" in result.stderr
    assert "ad sp update" in calls
    assert "ad app create" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_configure_entra_resume_continues_from_required_sp_without_redundant_update(tmp_path):
    """SP更新済み・credential 0の実partial stateは重複更新せず残stageを完了する。"""
    result, calls = _run_configure_entra_resume(
        tmp_path,
        external=False,
        service_principal_assignment_required=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ad sp update" not in calls
    assert calls.count("ad app credential reset") == 1
    assert "containerapp secret set" in calls
    assert "--method post" in calls
    assert "deployment group create" in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("assignment_required", [None, "true"])
def test_entra_resume_guard_rejects_unknown_assignment_required_shape(
    tmp_path, assignment_required
):
    """SP更新状態がnull・文字列なら既知partial stateとみなさず全write前に停止する。"""
    result = _run_entra_resume_guard(
        tmp_path,
        servicePrincipal={
            "id": RESUME_SP_ID,
            "appId": RESUME_CLIENT_ID,
            "displayName": "agent-world-yomote-jpe-login",
            "servicePrincipalType": "Application",
            "appRoleAssignmentRequired": assignment_required,
        },
    )
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("external", [True, None])
def test_configure_entra_resume_requires_known_internal_ingress(tmp_path, external):
    """externalまたは未知ingressのままEntra writeを再開しない。"""
    result, calls = _run_configure_entra_resume(tmp_path, external=external)
    assert result.returncode != 0
    assert "ingressはinternalのactualが必須" in result.stderr
    assert "ad sp update" not in calls
    assert "ad app credential reset" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_configure_entra_recovers_exact_orphan_and_completes_owned_writes(tmp_path):
    """exact orphan削除後の新credentialを一意化し、KV・本人assignment・authまで完了する。"""
    result, calls = _run_configure_entra_resume(tmp_path, external=False, recover_credential=True)
    assert result.returncode == 0, result.stderr
    assert calls.count("ad app credential delete") == 1
    assert calls.count("ad app credential reset") == 1
    assert "containerapp secret set" in calls
    assert "--method post" in calls
    assert "deployment group create" in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("metadata_count", [0, 2])
def test_configure_entra_stops_when_new_credential_metadata_is_not_unique(tmp_path, metadata_count):
    """新credential metadataが0件・複数ならKVやassignmentへ進まない。"""
    result, calls = _run_configure_entra_resume(
        tmp_path,
        external=False,
        recover_credential=True,
        metadata_count=metadata_count,
    )
    assert result.returncode != 0
    assert "Credential metadataを一意に特定できません" in result.stderr
    assert "containerapp secret set" not in calls
    assert "--method post" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_configure_entra_stops_when_new_and_foreign_credentials_remain(tmp_path):
    """post-resetがnew1件とforeign1件なら事前0/事後1契約に反するため停止する。"""
    result, calls = _run_configure_entra_resume(
        tmp_path,
        external=False,
        recover_credential=True,
        foreign_metadata=True,
    )
    assert result.returncode != 0
    assert "Credential metadataを一意に特定できません" in result.stderr
    assert "containerapp secret set" not in calls
    assert "--method post" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_resume_after_key_vault_completes_only_the_remaining_owned_writes(tmp_path):
    """既credential/KV metadataを照合し、secret値なしでref・本人assignment・authだけを完了する。"""
    result, calls = _run_resume_after_key_vault(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ad app credential reset" not in calls
    assert "keyvault secret show" not in calls
    assert "keyvault secret list-versions" in calls
    assert "keyvault secret show" not in calls
    assert "containerapp secret list" in calls
    assert "--show-values" not in calls
    assert calls.count("containerapp secret set") == 1
    assert calls.count("--method post") == 1
    assert calls.count("deployment group create") == 1
    assert "keys(identity.userAssignedIdentities)" not in calls
    assert "identity.userAssignedIdentities" in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    "identities",
    [
        {},
        {
            "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
            "Microsoft.ManagedIdentity/userAssignedIdentities/foreign": {},
        },
        {
            "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
            "Microsoft.ManagedIdentity/userAssignedIdentities/agent-world-yomote-jpe-identity": {},
            "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
            "Microsoft.ManagedIdentity/userAssignedIdentities/foreign": {},
        },
    ],
)
@pytest.mark.parametrize("resume_existing_ref", [False, True])
def test_resume_after_key_vault_rejects_missing_or_multiple_identities(
    tmp_path, identities, resume_existing_ref
):
    """UAMIが0件・別物・複数ならsecret refや後続writeを始めない。"""
    result, calls = _run_resume_after_key_vault(
        tmp_path,
        identities=identities,
        resume_existing_ref=resume_existing_ref,
    )
    assert result.returncode != 0
    assert "user-assigned identityが承認済みのexact 1件" in result.stderr
    assert "containerapp secret set" not in calls
    assert "--method post" not in calls
    assert "deployment group create" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_resume_after_key_vault_rejects_mismatched_secret_metadata(tmp_path):
    """KV metadataがcredentialと違えば参照・assignment・authへ進まない。"""
    result, calls = _run_resume_after_key_vault(tmp_path, secret_matches=False)
    assert result.returncode != 0
    assert "Key Vault secret metadataが承認済みcredential/versionと一致しません" in result.stderr
    assert "identity.userAssignedIdentities" not in calls
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_resume_after_key_vault_rejects_existing_container_secret_reference(tmp_path):
    """ref設定済みまたは結果不明のstateへ同じwriteを再送しない。"""
    result, calls = _run_resume_after_key_vault(tmp_path, container_secret_exists=True)
    assert result.returncode != 0
    assert "secret metadataが0件ではありません" in result.stderr
    assert "identity.userAssignedIdentities" not in calls
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_resume_after_existing_secret_reference_skips_ref_and_completes(tmp_path):
    """exact ref作成済みstateはrefを再送せずassignment fileとauthだけを実行する。"""
    result, calls = _run_resume_after_key_vault(tmp_path, resume_existing_ref=True)
    assert result.returncode == 0, result.stderr
    assert "containerapp secret set" not in calls
    assert calls.count("--method post") == 1
    assert "--headers Content-Type=application/json" in calls
    assert "--body @" in calls
    assert calls.count("deployment group create") == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("vault_count", [0, 2])
def test_resume_after_key_vault_rejects_missing_or_multiple_tagged_vaults(tmp_path, vault_count):
    """対象RGのapplication tag付きVaultが0件・複数なら参照writeを始めない。"""
    result, calls = _run_resume_after_key_vault(tmp_path, vault_count=vault_count)
    assert result.returncode != 0
    assert "Key Vaultが承認済みのexact 1件ではありません" in result.stderr
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("secret_version_count", [0, 2])
def test_resume_after_key_vault_requires_exactly_one_secret_version(tmp_path, secret_version_count):
    """versionless参照が別versionを指し得る0件・複数versionでは停止する。"""
    result, calls = _run_resume_after_key_vault(tmp_path, secret_version_count=secret_version_count)
    assert result.returncode != 0
    assert "Key Vault secret metadataが承認済みcredential/versionと一致しません" in result.stderr
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("ingress_external", [0, "false", None])
def test_resume_after_key_vault_requires_boolean_false_ingress(tmp_path, ingress_external):
    """数値・文字列・nullのexternalをinternal actualへ暗黙変換しない。"""
    result, calls = _run_resume_after_key_vault(tmp_path, ingress_external=ingress_external)
    assert result.returncode != 0
    assert "ingressはinternalのactualが必須" in result.stderr
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_resume_after_key_vault_requires_boolean_true_secret_enabled(tmp_path):
    """文字列falseをenabled=trueとして扱わず参照write前に停止する。"""
    result, calls = _run_resume_after_key_vault(tmp_path, secret_enabled="false")
    assert result.returncode != 0
    assert "Key Vault secret metadataが承認済みcredential/versionと一致しません" in result.stderr
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("credential_root", "scalar"),
        ("credential_root", "null"),
        ("secret_version_root", "scalar"),
        ("secret_version_root", "null"),
        ("container_secret_root", "scalar"),
        ("container_secret_root", "null"),
        ("vault_root", "scalar"),
        ("vault_root", "null"),
    ],
)
def test_resume_after_key_vault_rejects_non_array_collection_roots(tmp_path, argument, value):
    """collectionのscalar/nullを1件または0件へ丸めず全write前に停止する。"""
    result, calls = _run_resume_after_key_vault(tmp_path, **{argument: value})
    assert result.returncode != 0
    assert "JSON root is unknown" in result.stderr
    assert "containerapp secret set" not in calls


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    ("fail_stage", "expected_post_count", "expected_auth_count"),
    [("secret-ref", 0, 0), ("assignment", 1, 0), ("auth", 1, 1)],
)
def test_resume_after_key_vault_stops_at_each_unknown_write(
    tmp_path, fail_stage, expected_post_count, expected_auth_count
):
    """残工程のwrite失敗を後続成功へ丸めず、同じ処理を再送しない。"""
    result, calls = _run_resume_after_key_vault(tmp_path, fail_stage=fail_stage)
    assert result.returncode != 0
    assert calls.count("containerapp secret set") == 1
    assert calls.count("--method post") == expected_post_count
    assert calls.count("deployment group create") == expected_auth_count


@pytest.mark.skipif(
    PWSH is None or os.name != "nt", reason="Windows az.cmd argument regression requires pwsh"
)
def test_windows_az_cmd_reproduces_parenthesized_query_and_accepts_safe_query(tmp_path):
    """az.cmdのbatch blockで旧keys(...)が壊れ、安全なJSON queryはargvを通ることを固定する。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "az.cmd").write_text(
        '@echo off\n@if exist "%ComSpec%" (\n  echo %*\n) else (\n  exit /b 1\n)\n',
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    old = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-Command",
            "az containerapp show --query 'keys(identity.userAssignedIdentities)[0]' --output tsv",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    safe = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-Command",
            "az containerapp show --query identity.userAssignedIdentities --output json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    assert old.returncode != 0
    assert "[0] was unexpected at this time" in old.stderr
    assert safe.returncode == 0, safe.stderr
    assert "identity.userAssignedIdentities --output json" in safe.stdout


@pytest.mark.skipif(
    PWSH is None or os.name != "nt", reason="Windows az.cmd argument regression requires pwsh"
)
def test_windows_az_cmd_preserves_entra_assignment_id_projection(tmp_path):
    """Directory guardのID-only JMESPathがWindows batch経由でも同じargvとして届く。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "az.cmd").write_text(
        '@echo off\n@if exist "%ComSpec%" (\n  echo %*\n) else (\n  exit /b 1\n)\n',
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-Command",
            "az rest --method get --query "
            "'value[].{resourceId:resourceId,principalId:principalId}' --output json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "value[].{resourceId:resourceId,principalId:principalId}" in result.stdout


@pytest.mark.skipif(
    PWSH is None or os.name != "nt", reason="Windows az.cmd argument regression requires pwsh"
)
def test_windows_az_cmd_receives_assignment_file_and_auth_parameter_array(tmp_path):
    """残工程のJSONを@fileで渡し、az.cmd越しでも3 IDと本人arrayを崩さない。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "fake-az.log"
    fake_cli = fake_bin / "fake-az.ps1"
    fake_cli.write_text(
        f"""
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Remaining)
$joined = [string]::Join(' ', $Remaining)
if ($joined -match '^account show') {{
  Write-Output '{{"id":"sub-1","state":"Enabled","tenantId":"{RESUME_USER_ID}"}}'
  exit 0
}}
if ($joined -match '^rest .*--method post') {{
  $bodyIndex = [Array]::IndexOf($Remaining, '--body')
  $headerIndex = [Array]::IndexOf($Remaining, '--headers')
  if ($bodyIndex -lt 0 -or $headerIndex -lt 0 -or
      $Remaining[$headerIndex + 1] -ne 'Content-Type=application/json' -or
      -not $Remaining[$bodyIndex + 1].StartsWith('@')) {{ exit 2 }}
  $body = Get-Content -Raw -LiteralPath $Remaining[$bodyIndex + 1].Substring(1) | ConvertFrom-Json
  if ($body.principalId -ne '{RESUME_USER_ID}' -or
      $body.resourceId -ne '{RESUME_SP_ID}' -or
      $body.appRoleId -ne '00000000-0000-0000-0000-000000000000') {{ exit 3 }}
  Add-Content -LiteralPath '{log.as_posix()}' -Value 'ASSIGNMENT_FILE_OK'
  exit 0
}}
if ($joined -match '^deployment group create') {{
  $parameterIndex = [Array]::IndexOf($Remaining, '--parameters')
  if ($parameterIndex -lt 0 -or -not $Remaining[$parameterIndex + 1].StartsWith('@')) {{ exit 4 }}
  $parameterPath = $Remaining[$parameterIndex + 1].Substring(1)
  $parameters = Get-Content -Raw -LiteralPath $parameterPath | ConvertFrom-Json
  if ($parameters.parameters.tenantId.value -ne '{RESUME_USER_ID}' -or
      $parameters.parameters.clientId.value -ne '{RESUME_CLIENT_ID}' -or
      @($parameters.parameters.allowedPrincipalObjectIds.value).Count -ne 1 -or
      $parameters.parameters.allowedPrincipalObjectIds.value[0] -ne '{RESUME_USER_ID}') {{ exit 5 }}
  Add-Content -LiteralPath '{log.as_posix()}' -Value 'AUTH_PARAMETER_ARRAY_OK'
  exit 0
}}
exit 6
""",
        encoding="utf-8",
    )
    pwsh_executable = str(PWSH).replace("%", "%%")
    (fake_bin / "az.cmd").write_text(
        '@echo off\n@if exist "%ComSpec%" (\n'
        f'  "{pwsh_executable}" -NoProfile -File "%~dp0fake-az.ps1" %*\n'
        ") else (\n  exit /b 1\n)\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(COMPLETE_ENTRA_ASSIGNMENT),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
            "-TenantId",
            RESUME_USER_ID,
            "-ClientId",
            RESUME_CLIENT_ID,
            "-ServicePrincipalObjectId",
            RESUME_SP_ID,
            "-AllowedUserObjectId",
            RESUME_USER_ID,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "ASSIGNMENT_FILE_OK",
        "AUTH_PARAMETER_ARRAY_OK",
    ]


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize(
    "invalid_args",
    [
        ["-ResumeTenantId", RESUME_USER_ID],
        ["-ResumeTenantId", RESUME_USER_ID, "-ResumeClientId", RESUME_CLIENT_ID],
        ["-ResumeOrphanCredentialKeyId", "55555555-5555-5555-5555-555555555555"],
        [
            "-ResumeOrphanCredentialKeyId",
            "55555555-5555-5555-5555-555555555555",
            "-ResumeOrphanCredentialDisplayName",
            "orphan",
        ],
    ],
)
def test_configure_entra_rejects_invalid_resume_argument_shapes_before_az(invalid_args):
    """許可した3形以外はAzure read/writeを始める前に拒否する。"""
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(CONFIGURE_ENTRA),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
            "-AllowedUserObjectId",
            RESUME_USER_ID,
            *invalid_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "Entra引数はnew create" in result.stderr
    assert "Azure context verified" not in result.stdout


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_initial_plan_guard_accepts_only_the_expected_create_set(tmp_path):
    """承認対象の専用RG内Createだけを初回applyへ渡す。"""
    changes = [
        {"changeType": "Create", "resourceId": resource_id}
        for resource_id in _resource_ids("sub-1", "rg-agent-world-jpe", "agent-world-yomote-jpe")
    ]
    result = _run_plan_guard(tmp_path, changes)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("failure", ["modify", "outside", "missing_budget"])
def test_initial_plan_guard_rejects_unapproved_or_incomplete_changes(tmp_path, failure):
    """Modify・対象外resource・Budget欠落をapply前に停止する。"""
    ids = _resource_ids("sub-1", "rg-agent-world-jpe", "agent-world-yomote-jpe")
    changes = [{"changeType": "Create", "resourceId": resource_id} for resource_id in ids]
    if failure == "modify":
        changes[1]["changeType"] = "Modify"
    elif failure == "outside":
        changes.append(
            {
                "changeType": "Create",
                "resourceId": (
                    "/subscriptions/sub-1/resourceGroups/other/providers/"
                    "Microsoft.Storage/storageAccounts/unapproved"
                ),
            }
        )
    else:
        changes.pop()

    result = _run_plan_guard(tmp_path, changes)
    assert result.returncode != 0


def _run_auth_plan_guard(
    tmp_path: Path, changes: list[dict[str, str]]
) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "auth-what-if.json"
    plan.write_text(json.dumps({"status": "Succeeded", "changes": changes}), encoding="utf-8")
    return subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(AUTH_PLAN_GUARD),
            "-WhatIfPath",
            str(plan),
            "-SubscriptionId",
            "sub-1",
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_auth_plan_guard_accepts_only_existing_container_app_update(tmp_path):
    """初回core後のEntra/external段階を正しく通す。"""
    result = _run_auth_plan_guard(
        tmp_path,
        [
            {
                "changeType": "Modify",
                "resourceId": (
                    "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
                    "Microsoft.App/containerApps/agent-world-yomote-jpe"
                ),
            }
        ],
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("change_type", ["Create", "Delete"])
def test_auth_plan_guard_rejects_non_modify_stage(tmp_path, change_type):
    """external段階で再作成や削除へ広がるplanを停止する。"""
    result = _run_auth_plan_guard(
        tmp_path,
        [
            {
                "changeType": change_type,
                "resourceId": (
                    "/subscriptions/sub-1/resourceGroups/rg-agent-world-jpe/providers/"
                    "Microsoft.App/containerApps/agent-world-yomote-jpe"
                ),
            }
        ],
    )
    assert result.returncode != 0


def _fake_az_script(tmp_path: Path, external: str) -> tuple[Path, Path]:
    log = tmp_path / "az.log"
    fake = tmp_path / "fake-az.ps1"
    fake.write_text(
        "param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)\n"
        f"Add-Content -LiteralPath '{log.as_posix()}' -Value ($Args -join ' ')\n"
        f"if (($Args -join ' ') -match 'containerapp show') {{ Write-Output '{external}' }}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return fake, log


def _fake_external_state_az_script(
    tmp_path: Path,
    *,
    initial: str,
    deployment_state: str = "Failed",
    deployment_read_exit_code: int = 0,
    ingress_read_exit_code: int = 0,
) -> tuple[Path, Path]:
    log = tmp_path / "external-state-az.log"
    marker = tmp_path / "ingress-disabled"
    fake = tmp_path / "fake-external-state-az.ps1"
    fake.write_text(
        "param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)\n"
        f"Add-Content -LiteralPath '{log.as_posix()}' -Value ($Args -join ' ')\n"
        "$command = $Args -join ' '\n"
        "if ($command -match 'deployment sub show') {\n"
        f"  if ({deployment_read_exit_code} -ne 0) {{ exit {deployment_read_exit_code} }}\n"
        f"  Write-Output '{deployment_state}'\n"
        "  exit 0\n"
        "}\n"
        "if ($command -match 'ingress disable') {\n"
        f"  Set-Content -LiteralPath '{marker.as_posix()}' -Value disabled\n"
        "  exit 0\n"
        "}\n"
        "if ($command -match 'containerapp show') {\n"
        f"  if ({ingress_read_exit_code} -ne 0) {{ exit {ingress_read_exit_code} }}\n"
        f"  if (Test-Path -LiteralPath '{marker.as_posix()}') {{ Write-Output 'false' }} "
        f"else {{ Write-Output '{initial}' }}\n"
        "  exit 0\n"
        "}\n"
        "exit 64\n",
        encoding="utf-8",
    )
    return fake, log


def _run_external_deployment_resolution(fake: Path) -> subprocess.CompletedProcess[str]:
    script = str(EXTERNAL_DEPLOYMENT_FAILURE).replace("'", "''")
    fake_cli = str(fake).replace("'", "''")
    command = (
        "try { "
        f"& '{script}' -ResourceGroupName 'rg-agent-world-jpe' "
        "-AppName 'agent-world-yomote-jpe' "
        "-DeploymentName 'agent-world-core-20260913000000' "
        f"-AzureCli '{fake_cli}' "
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
    )
    return subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_external_deployment_nonzero_or_timeout_after_side_effect_contains_once(tmp_path):
    """nonzero/timeout相当の応答不明でexternal actualを1回だけ封じ込める。"""
    fake, log = _fake_external_state_az_script(
        tmp_path, initial="true", deployment_state="Succeeded"
    )
    result = _run_external_deployment_resolution(fake)
    assert result.returncode != 0
    assert "containment was verified" in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len([call for call in calls if "deployment sub show" in call]) == 1
    assert len([call for call in calls if "ingress disable" in call]) == 1
    assert len([call for call in calls if "containerapp show" in call]) == 2


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_external_deployment_nonzero_preserves_internal_actual(tmp_path):
    """deployment応答不明でinternalなactualに余計なwriteを行わない。"""
    fake, log = _fake_external_state_az_script(tmp_path, initial="false", deployment_state="Failed")
    result = _run_external_deployment_resolution(fake)
    assert result.returncode != 0
    assert "remained internal" in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len([call for call in calls if "deployment sub show" in call]) == 1
    assert not [call for call in calls if "ingress disable" in call]
    assert len([call for call in calls if "containerapp show" in call]) == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_external_deployment_nonzero_stops_if_ingress_actual_is_unreadable(tmp_path):
    """actualを読めない結果不明を成功と推測せず再送なしで停止する。"""
    fake, log = _fake_external_state_az_script(
        tmp_path,
        initial="",
        deployment_state="Canceled",
        ingress_read_exit_code=1,
    )
    result = _run_external_deployment_resolution(fake)
    assert result.returncode != 0
    assert "actual could not be verified" in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len([call for call in calls if "deployment sub show" in call]) == 1
    assert len([call for call in calls if "containerapp show" in call]) == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_running_deployment_does_not_claim_internal_actual_is_final(tmp_path):
    """Runningで後external化し得るとき、現在internalでも確定としない。"""
    fake, log = _fake_external_state_az_script(
        tmp_path, initial="false", deployment_state="Running"
    )
    result = _run_external_deployment_resolution(fake)
    assert result.returncode != 0
    assert "no terminal provisioning state was verified" in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len([call for call in calls if "deployment sub show" in call]) == 1
    assert len(calls) == 1
    assert not [call for call in calls if "containerapp show" in call]


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_unreadable_deployment_state_stops_before_ingress_actual(tmp_path):
    """deployment stateを読めない場合はingressを確定値に使わない。"""
    fake, log = _fake_external_state_az_script(
        tmp_path,
        initial="false",
        deployment_read_exit_code=1,
    )
    result = _run_external_deployment_resolution(fake)
    assert result.returncode != 0
    assert "no terminal provisioning state was verified" in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_external_ingress_containment_writes_once_then_reads_actual(tmp_path):
    """auth failure時の封じ込めを再送せずactual確認で終える。"""
    fake, log = _fake_az_script(tmp_path, "false")
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(CONTAINMENT),
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
            "-AzureCli",
            str(fake),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len([call for call in calls if "ingress disable" in call]) == 1
    assert len([call for call in calls if "containerapp show" in call]) == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_external_ingress_containment_fails_if_actual_remains_public(tmp_path):
    """封じ込め後もexternalなら公開失敗を成功扱いしない。"""
    fake, _ = _fake_az_script(tmp_path, "true")
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(CONTAINMENT),
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
            "-AzureCli",
            str(fake),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.skipif(PWSH is None, reason="PowerShell containment test requires pwsh")
def test_external_ingress_containment_fails_if_actual_is_unknown(tmp_path):
    """actualが空なら封じ込め済みと推測せず停止する。"""
    fake, _ = _fake_az_script(tmp_path, "")
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(CONTAINMENT),
            "-ResourceGroupName",
            "rg-agent-world-jpe",
            "-AppName",
            "agent-world-yomote-jpe",
            "-AzureCli",
            str(fake),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
