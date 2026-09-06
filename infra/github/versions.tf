terraform {
  required_version = ">= 1.16.1, < 2.0.0"
  required_providers {
    github = {
      source  = "integrations/github"
      version = "= 6.13.0"
    }
  }
  # ローカルstate。共有backendは導入時に移行し、同じstateを複数人で適用しない。
  backend "local" {}
}

provider "github" {
  owner = var.owner
  # 既存のgh認証、または実行プロセスのGITHUB_TOKENを使用する。
}
