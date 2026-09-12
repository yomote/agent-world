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
BILLING_CURRENCY = ROOT / "scripts" / "azure" / "Test-AzureBillingCurrency.ps1"
PWSH = shutil.which("pwsh")


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


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
def test_apply_input_guard_accepts_complete_approved_inputs():
    """通知先・実通貨・承認済みplanが揃う正常stageを通す。"""
    result = _run_apply_input_guard(
        "-BudgetContactEmails",
        "owner@example.invalid",
        "-ConfirmedBudgetCurrency",
        "JPY",
        "-ApprovedWhatIfSha256",
        "a" * 64,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell guard test requires pwsh")
@pytest.mark.parametrize("missing", ["contact", "currency", "hash"])
def test_apply_input_guard_rejects_incomplete_approval(missing):
    """Budgetやplanの未承認入力をAzure照会より前に停止する。"""
    arguments = {
        "contact": [],
        "currency": ["-BudgetContactEmails", "owner@example.invalid"],
        "hash": [
            "-BudgetContactEmails",
            "owner@example.invalid",
            "-ConfirmedBudgetCurrency",
            "JPY",
        ],
    }[missing]
    result = _run_apply_input_guard(*arguments)
    assert result.returncode != 0


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


def _fake_cost_az_script(tmp_path: Path, *, currency: str, exit_code: int = 0) -> tuple[Path, Path]:
    log = tmp_path / "cost-az.log"
    fake = tmp_path / "fake-cost-az.ps1"
    response = json.dumps(
        {
            "properties": {
                "columns": [{"name": "PreTaxCost"}, {"name": "Currency"}],
                "rows": [[0, currency]],
            }
        }
    )
    fake.write_text(
        "param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)\n"
        f"Add-Content -LiteralPath '{log.as_posix()}' -Value ($Args -join ' ')\n"
        f"Write-Output '{response}'\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    return fake, log


@pytest.mark.skipif(PWSH is None, reason="PowerShell billing test requires pwsh")
def test_billing_currency_guard_accepts_one_jpy_actual_row(tmp_path):
    """実請求応答がJPYと確認できた正常stageを通す。"""
    fake, log = _fake_cost_az_script(tmp_path, currency="JPY")
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(BILLING_CURRENCY),
            "-SubscriptionId",
            "sub-1",
            "-ExpectedCurrency",
            "JPY",
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
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.skipif(PWSH is None, reason="PowerShell billing test requires pwsh")
@pytest.mark.parametrize(("currency", "exit_code"), [("USD", 0), ("JPY", 1)])
def test_billing_currency_guard_stops_without_retry(tmp_path, currency, exit_code):
    """通貨不一致や429相当の照会失敗を1回で停止する。"""
    fake, log = _fake_cost_az_script(tmp_path, currency=currency, exit_code=exit_code)
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(BILLING_CURRENCY),
            "-SubscriptionId",
            "sub-1",
            "-ExpectedCurrency",
            "JPY",
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
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


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
