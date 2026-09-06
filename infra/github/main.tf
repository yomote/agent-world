resource "github_repository" "project" {
  name                        = var.repository_name
  description                 = "AgentのActionとWorldの変化を観察するSandbox"
  visibility                  = var.visibility
  has_issues                  = true
  has_projects                = false
  has_wiki                    = false
  has_discussions             = false
  allow_squash_merge          = true
  allow_merge_commit          = false
  allow_rebase_merge          = false
  allow_auto_merge            = false
  allow_update_branch         = true
  delete_branch_on_merge      = true
  squash_merge_commit_title   = "PR_TITLE"
  squash_merge_commit_message = "PR_BODY"
  archive_on_destroy          = true

  security_and_analysis {
    secret_scanning {
      status = "enabled"
    }
    secret_scanning_push_protection {
      status = "enabled"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "github_branch_default" "main" {
  repository = github_repository.project.name
  branch     = "main"
  rename     = false
}

resource "github_actions_repository_permissions" "project" {
  repository           = github_repository.project.name
  enabled              = true
  allowed_actions      = "selected"
  sha_pinning_required = true
  allowed_actions_config {
    github_owned_allowed = true
    verified_allowed     = false
    patterns_allowed = [
      "Azure/login@*",
      "hashicorp/setup-terraform@*",
      "lycheeverse/lychee-action@*",
    ]
  }
}

resource "github_workflow_repository_permissions" "project" {
  repository                       = github_repository.project.name
  default_workflow_permissions     = "read"
  can_approve_pull_request_reviews = false
}

resource "github_repository_vulnerability_alerts" "project" {
  repository = github_repository.project.name
  enabled    = true
}

resource "github_repository_dependabot_security_updates" "project" {
  repository = github_repository.project.name
  enabled    = true
  depends_on = [github_repository_vulnerability_alerts.project]
}

resource "github_repository_ruleset" "main" {
  name        = "agent-world-main"
  repository  = github_repository.project.name
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/heads/main"]
      exclude = []
    }
  }

  # 管理者バイパスなし。単独開発では自己approveできないため承認数は0。
  rules {
    deletion                = true
    non_fast_forward        = true
    required_linear_history = true
    pull_request {
      required_approving_review_count   = 0
      required_review_thread_resolution = true
      dismiss_stale_reviews_on_push     = true
      require_code_owner_review         = false
      require_last_push_approval        = false
    }
    required_status_checks {
      strict_required_status_checks_policy = true
      dynamic "required_check" {
        # workflowのjob名と照合する (tests/policy.tftest.hcl)。
        for_each = toset(["check"])
        content {
          context = required_check.value
        }
      }
    }
  }
}
