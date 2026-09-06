# GitHubにはアクセスせず、宣言されたポリシーとCIの接続を検査する。
mock_provider "github" {}

# import対象もfakeの既存リソースとして与える。実GitHubへのimportは実行しない。
override_resource {
  target = github_repository.project
  values = { name = "test-repository" }
}
override_resource {
  target = github_branch_default.main
}
override_resource {
  target = github_actions_repository_permissions.project
}
override_resource {
  target = github_workflow_repository_permissions.project
}
override_resource {
  target = github_repository_vulnerability_alerts.project
}
override_resource {
  target = github_repository_dependabot_security_updates.project
}

variables {
  owner           = "test-owner"
  repository_name = "test-repository"
  visibility      = "private"
}

run "factory_policy" {
  command = plan

  assert {
    condition     = github_workflow_repository_permissions.project.default_workflow_permissions == "read" && !github_workflow_repository_permissions.project.can_approve_pull_request_reviews
    error_message = "検査workflowに既定の書き込み権限や自己承認権限を与えない。"
  }
  assert {
    condition     = github_repository_ruleset.main.enforcement == "active" && length(github_repository_ruleset.main.bypass_actors) == 0
    error_message = "mainのrulesetは有効で、バイパスがないこと。"
  }
  assert {
    condition     = github_repository_ruleset.main.rules[0].pull_request[0].required_review_thread_resolution && github_repository_ruleset.main.rules[0].pull_request[0].required_approving_review_count == 0
    error_message = "未解決スレッドは止め、単独開発の自己承認待ちは作らない。"
  }
  assert {
    condition     = toset([for check in github_repository_ruleset.main.rules[0].required_status_checks[0].required_check : check.context]) == toset([for key, job in yamldecode(file("${path.module}/../../.github/workflows/ci.yml")).jobs : try(job.name, key)])
    error_message = "rulesetの必須チェックとCI workflowのjob名が一致していません。"
  }
  assert {
    condition     = github_actions_repository_permissions.project.sha_pinning_required && github_repository_vulnerability_alerts.project.enabled && github_repository_dependabot_security_updates.project.enabled
    error_message = "ActionのSHA固定と依存脆弱性の検出・修正提案を有効にする。"
  }
  assert {
    condition = (
      length(yamldecode(file("${path.module}/../../.github/workflows/ci.yml")).jobs) == 1 &&
      yamldecode(file("${path.module}/../../.github/workflows/ci.yml")).jobs.check.timeout-minutes == 10 &&
      yamldecode(file("${path.module}/../../.github/workflows/ci.yml")).permissions.contents == "read" &&
      yamldecode(file("${path.module}/../../.github/workflows/ci.yml")).concurrency.cancel-in-progress
    )
    error_message = "CIの1job・10分・read-only・古い実行のキャンセルという予算を維持する。"
  }
}
