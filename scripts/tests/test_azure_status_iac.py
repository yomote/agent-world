import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_status_container_context_excludes_local_runtime_data():
    """local mapping、session記録、artifact、credentialをimageへ混入する回帰を防ぐ。"""
    dockerfile = (ROOT / "Dockerfile.status").read_text(encoding="utf-8")
    ignore = (ROOT / "Dockerfile.status.dockerignore").read_text(encoding="utf-8")

    assert "COPY ." not in dockerfile
    assert "COPY apps/ops_status" in dockerfile
    assert "COPY docs/status" in dockerfile
    assert ignore.splitlines()[0] == "*"
    assert "!artifacts/" not in ignore
    assert "!.codex/" not in ignore


def test_status_iac_keeps_storage_private_and_capacity_bounded():
    """snapshot匿名公開や無制限scaleを構成に持ち込む回帰を防ぐ。"""
    bicep = (ROOT / "infra/azure-status/resources.bicep").read_text(encoding="utf-8")

    assert "allowBlobPublicAccess: false" in bicep
    assert "allowSharedKeyAccess: false" in bicep
    assert "publicAccess: 'None'" in bicep
    assert "minReplicas: 0" in bicep
    assert "maxReplicas: 1" in bicep
    assert "external: enableProtectedIngress" in bicep
    assert "'/healthz'" in bicep


def test_deploy_gate_rejects_unreviewed_or_broad_changes():
    """reviewしていないplanやDeleteをapplyする回帰を防ぐ。"""
    script = (ROOT / "scripts/azure-status/Deploy-ManagementStatus.ps1").read_text(encoding="utf-8")
    gate = (ROOT / "scripts/azure-status/Assert-ManagementStatusPlan.ps1").read_text(
        encoding="utf-8"
    )

    assert "ApprovedPlanSha256" in script
    assert "ApprovedConfirmationSha256" in script
    assert "CostConfirmationSource" in script
    assert "OperatorConfirmedBudgetContact" in script
    assert "ad signed-in-user show" in script
    assert "Signed-in Azure user object ID does not match OperatorObjectId" in script
    assert "Assert-ManagementStatusPlan" in script
    assert "coreResourcePatterns" in gate
    assert "OutOfScope" in gate
    assert "ApproveReviewedPlan" in script
    assert "changeType -eq 'Create'" in gate
    assert "What-if contains changes outside" in gate
    assert "deployment sub create" in script
    assert "changeType -in @('NoChange', 'Ignore')" in gate
    assert gate.index("OutOfScope") < gate.index("changeType -in @('NoChange', 'Ignore')")


def test_protected_gate_accepts_known_ignores_and_rejects_unknown_ignore(tmp_path):
    """既知existingのIgnoreだけを非writeとして扱い、外scopeのIgnoreを拒否する。"""
    subscription = "11111111-1111-1111-1111-111111111111"
    resource_group = "rg-agent-world-mgmt-jpe"
    app = "agent-world-status-yomote-jpe"
    root = f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
    known = [
        root,
        f"{root}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{app}-identity",
        f"{root}/providers/Microsoft.Storage/storageAccounts/awst1234567890123",
        f"{root}/providers/Microsoft.KeyVault/vaults/awst1234567890123",
        f"{root}/providers/Microsoft.OperationalInsights/workspaces/{app}-logs",
        f"{root}/providers/Microsoft.App/managedEnvironments/{app}-env",
    ]
    app_id = f"{root}/providers/Microsoft.App/containerApps/{app}"
    accepted = [{"changeType": "Ignore", "resourceId": value} for value in known]
    accepted += [
        {"changeType": "Modify", "resourceId": app_id},
        {"changeType": "Create", "resourceId": f"{app_id}/authConfigs/current"},
    ]

    gate = ROOT / "scripts/azure-status/Assert-ManagementStatusPlan.ps1"

    def run_gate(changes):
        fixture = tmp_path / "plan.json"
        fixture.write_text(json.dumps({"changes": changes}), encoding="utf-8")
        command = (
            f". '{gate}'; $p=Get-Content -LiteralPath '{fixture}' -Raw|ConvertFrom-Json; "
            f"Assert-ManagementStatusPlan -Changes @($p.changes) -Phase Protected "
            f"-SubscriptionId {subscription} -ResourceGroupName {resource_group} -AppName {app}"
        )
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )

    assert run_gate(accepted).returncode == 0
    unknown = accepted + [
        {
            "changeType": "Ignore",
            "resourceId": f"{root}/providers/Contoso.Unknown/widgets/ignored",
        }
    ]
    rejected = run_gate(unknown)
    assert rejected.returncode != 0
    assert "OutOfScope Ignore" in rejected.stderr


def test_deploy_gate_does_not_print_budget_contact_confirmation():
    """本人のBudget通知先をwhat-if/applyの標準出力へ残す回帰を防ぐ。"""
    script = (ROOT / "scripts/azure-status/Deploy-ManagementStatus.ps1").read_text(encoding="utf-8")

    assert 'Write-Host "Confirmation record: $confirmationJson"' not in script
    assert 'Write-Host "Confirmation SHA-256: $confirmationHash"' in script


def test_protected_phase_redeclares_only_app_and_auth():
    """Protected公開時にCore資源のprovider既定値を再送する回帰を防ぐ。"""
    script = (ROOT / "scripts/azure-status/Deploy-ManagementStatus.ps1").read_text(encoding="utf-8")
    protected = (ROOT / "infra/azure-status/protected-resources.bicep").read_text(encoding="utf-8")

    assert "if ($protected) { 'protected-main.bicep' } else { 'main.bicep' }" in script
    assert "resource app 'Microsoft.App/containerApps@" in protected
    assert "resource auth 'Microsoft.App/containerApps/authConfigs@" in protected
    assert "resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@" in protected
    identity_declaration = (
        "resource appIdentity "
        "'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing"
    )
    assert identity_declaration in protected
    assert "resource storage 'Microsoft.Storage/storageAccounts@2025-06-01' existing" in protected
    assert "resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing" in protected
    assert (
        "resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing" in protected
    )
    assert "roleAssignments@" not in protected
    assert "Microsoft.Consumption/budgets@" not in protected
    assert "blobServices@" not in protected
    assert "name: 'AGENT_WORLD_STATUS_REQUIRE_AUTH'\n              value: 'true'" in protected
    assert "value: 'True'" not in protected


def test_status_image_publish_is_manual_and_head_pinned():
    """未承認branchや自動triggerから管理status imageを公開する回帰を防ぐ。"""
    workflow = (ROOT / ".github/workflows/status-image.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"' in workflow
    assert "if: inputs.publish" in workflow
