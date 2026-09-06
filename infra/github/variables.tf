variable "owner" {
  type        = string
  description = "適用先のGitHubユーザーまたは組織。"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]*$", var.owner))
    error_message = "GitHubのowner名を指定してください。"
  }
}

variable "repository_name" {
  type        = string
  description = "既存リポジトリ名。初回はimportしてから管理する。"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.repository_name))
    error_message = "ownerを含まないリポジトリ名を指定してください。"
  }
}

variable "visibility" {
  type        = string
  description = "現状確認済みの公開範囲。推測でpublicへ変更しない。"
  validation {
    condition     = contains(["private", "public"], var.visibility)
    error_message = "privateまたはpublicを明示してください。"
  }
}

variable "existing_ruleset_id" {
  type        = number
  default     = null
  nullable    = true
  description = "既存のagent-world-main rulesetがあればIDを指定してimportする。無ければnull。"
}
