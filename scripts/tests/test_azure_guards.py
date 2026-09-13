import json
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
BUDGET_EMAILS = ROOT / "scripts" / "azure" / "Assert-BudgetContactEmails.ps1"
EXTERNAL_DEPLOYMENT_FAILURE = (
    ROOT / "scripts" / "azure" / "Resolve-AzureExternalDeploymentFailure.ps1"
)
PWSH = shutil.which("pwsh")

RESUME_CLIENT_ID = "11111111-1111-1111-1111-111111111111"
RESUME_SP_ID = "22222222-2222-2222-2222-222222222222"
RESUME_USER_ID = "33333333-3333-3333-3333-333333333333"


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
  if ($joined -match '^keyvault list') {{ return 'test-vault' }}
  if ($joined -match '^containerapp show' -and $joined.Contains('keys(identity')) {{
    return '/identity/id'
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
