from pathlib import Path

WORKFLOW = (Path(__file__).parents[2] / ".github" / "workflows" / "deploy-azure.yml").read_text(
    encoding="utf-8"
)
ENVIRONMENT_SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "azure" / "Set-GitHubEnvironmentVariables.ps1"
).read_text(encoding="utf-8")
DEPLOY_SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "azure" / "Deploy-AzureCore.ps1"
).read_text(encoding="utf-8")


def test_container_check_is_created_for_every_pull_request():
    """無関係なPRでも必須checkが欠落して永久にmerge不能になる回帰を防ぐ。"""
    pull_request_trigger = WORKFLOW.split("  pull_request:", 1)[1].split("  push:", 1)[0]

    assert "paths:" not in pull_request_trigger
    assert "edited" in pull_request_trigger
    assert "Detect container input changes" in WORKFLOW
    assert "if: steps.changes.outputs.container == 'true'" in WORKFLOW
    assert "if: steps.changes.outputs.container == 'false'" in WORKFLOW


def test_container_comparison_errors_fail_the_required_check():
    """git diffの比較失敗を変更ありへ丸め、build成功で隠す回帰を防ぐ。"""
    detection_step = WORKFLOW.split("      - name: Detect container input changes", 1)[1].split(
        "      - name: Build and exercise production container", 1
    )[0]

    assert "diff_status=$?" in detection_step
    assert '0) echo "container=false"' in detection_step
    assert '1) echo "container=true"' in detection_step
    assert 'exit "${diff_status}"' in detection_step


def test_image_mutation_requires_explicit_bootstrap_or_image_only_confirmation():
    """main mergeだけで承認前のGHCR package writeが始まる回帰を防ぐ。"""
    guard = WORKFLOW.index("      - name: Require approved deployment mode")
    image = WORKFLOW.index("      - name: Build and push immutable image")

    assert guard < image
    assert 'test "${DEPLOY_ENABLED}" = "true"' in WORKFLOW
    assert 'test "${IMAGE_CONFIRMATION}" = "publish-ghcr-image"' in WORKFLOW
    assert 'test("^[^[:space:]@]+@[^[:space:]@]+\\\\.[^[:space:]@]+$")' in WORKFLOW
    assert "AZURE_DEPLOY_ENABLED = 'true'" in ENVIRONMENT_SCRIPT
    assert "[Parameter(Mandatory)] [switch] $EnableDeployment" in ENVIRONMENT_SCRIPT


def test_image_only_bootstrap_does_not_attempt_azure_mutation():
    """初回image準備とAzure deployを同じ未確定前提で連鎖する回帰を防ぐ。"""
    assert WORKFLOW.count("if: steps.approval.outputs.image_only != 'true'") == 5
    assert "Azure deployment: not attempted" in WORKFLOW


def test_core_apply_requires_budget_plan_and_billing_guards():
    """Budgetや承認済みwhat-ifなしにAzure Applyへ進む回帰を防ぐ。"""
    assert "Assert-AzureApplyInputs.ps1" in DEPLOY_SCRIPT
    assert "Test-AzureBillingCurrency.ps1" in DEPLOY_SCRIPT
    assert "Assert-AzureInitialPlan.ps1" in DEPLOY_SCRIPT
    assert "Assert-AzureAuthPlan.ps1" in DEPLOY_SCRIPT
    assert "--result-format FullResourcePayloads" in DEPLOY_SCRIPT
    assert "Current what-if SHA256 does not match the approved plan" in DEPLOY_SCRIPT
    assert "Auth Apply requires the dedicated Resource Group to exist" in DEPLOY_SCRIPT


def test_external_auth_failure_triggers_single_containment_path():
    """公開直後のauth smoke失敗でexternal ingressを残す回帰を防ぐ。"""
    assert DEPLOY_SCRIPT.count("Disable-AzureExternalIngress.ps1") == 1
    assert DEPLOY_SCRIPT.count("Resolve-AzureExternalDeploymentFailure.ps1") == 1
    assert "az deployment sub create @common --name $deploymentName" in DEPLOY_SCRIPT
    assert "-DeploymentName $deploymentName" in DEPLOY_SCRIPT
    assert "POST-APPLY AUTH FAILURE" in DEPLOY_SCRIPT
