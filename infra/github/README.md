# GitHub設定のIaC

このディレクトリのTerraformがGitHub設定の正本。Mind Inboxの自作YAML同期とは併用しない。宣言を検証するCIは[ci.yml](../../.github/workflows/ci.yml)、採否の背景は[工場の棚卸し](../../docs/factory-adoption.md)。

## 管理範囲

| 対象           | 宣言                                                                                                        |
| -------------- | ----------------------------------------------------------------------------------------------------------- |
| リポジトリ     | 公開範囲は明示入力、Issues有効、squashのみ、自動merge無効、マージ後のブランチ削除                           |
| default branch | 存在するmainを指定。新規作成・renameはしない                                                                |
| main ruleset   | PR経由、force push・削除禁止、linear history、未解決スレッド禁止、CIのcheck jobを必須化、管理者バイパスなし |
| Actions        | 有効、許可するActionの制限、SHA固定必須、既定tokenはread、PRの自己approve不可                               |
| 依存脆弱性     | alertsとDependabot security updatesを有効化                                                                 |
| 更新対象と頻度 | [.github/dependabot.yml](../../.github/dependabot.yml)。GitHubが読む設定なのでTerraformに重複転記しない     |

独立AIレビュー、CodeQL設定、Pages、GitHub App、organization ruleset、クラウド基盤は管理対象外。既存の別rulesetやclassic branch protectionはこの宣言で消えないため、初回に重複や競合を確認する。単独開発では自分のPRをapproveできないため、承認数0を宣言している。独立レビューが自動で保証されるという意味ではない。

## ローカル検証

Terraform 1.16.1とprovider `integrations/github` 6.13.0を使用する。

```text
terraform -chdir=infra/github fmt -check -recursive
terraform -chdir=infra/github init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/github validate
terraform -chdir=infra/github test
```

`npm run iac:check`も同じ4段を実行する。mock providerによるtestはGitHubへ接続せず、必須チェック名とCI job名、保護設定を検証する。**validate/mock testの成功は、実環境planや適用の成功ではない。**

provider更新時は以下でWindows/Linuxのハッシュをロックし、`.terraform.lock.hcl`をコミットする。

```text
terraform -chdir=infra/github providers lock -platform=windows_amd64 -platform=linux_amd64
```

## 初回の導入

2026-09-06にユーザーが指定した公開先は **Publicの `yomote/agent-world`**。`terraform.tfvars.example`にも反映した。これは適用先の指定であり、repo作成やTerraform適用の完了を示すものではない。

1. 対象repoと公開範囲を確認する。現在のローカルフォルダから接続先を推測して適用しない。このTerraformは既存repoをimportする方式で、新規repo作成は別途行う。
2. mainにアプリ・workflow・IaCの初期版を置き、CIのcheck jobが完走したことを確認する。必須化だけ先に適用すると未実行のチェック待ちになる。
3. 管理権限のある既存の`gh`ログインを使用する。providerは`gh auth token`へフォールバックできる。トークンをtfvars・リポジトリ・CI artifactへ保存しない。`GITHUB_OWNER`など別のowner設定を環境変数で上書きしていないことも確認する。
4. `terraform.tfvars.example`を`terraform.tfvars`へコピーし、owner/name/現状のvisibilityを記入する。private repoでrulesetが利用できるかはアカウントのプランに依存する。利用できない場合に公開範囲を自動変更しない。
5. `gh api repos/OWNER/REPO/rulesets` と既存のbranch protectionを確認する。`agent-world-main`が既にあれば`existing_ruleset_id`へ実IDを記入し、二重作成を防ぐ。
6. 以下を順番に実行し、作成・変更・削除の差分を読む。

```text
terraform -chdir=infra/github init -input=false -lockfile=readonly
terraform -chdir=infra/github plan -input=false -out=settings.tfplan
terraform -chdir=infra/github show settings.tfplan
```

`imports.tf`はrepoと既存設定を取り込む。未存在のrepoはimportが失敗するため、入力ミスで別repoを作成しない。rulesetだけは未作成なら新規に作成する。既存のルールを取得できなかった場合は未確認と扱う。

確認済みのplanを適用する操作は次の通り。

```text
terraform -chdir=infra/github apply settings.tfplan
terraform -chdir=infra/github plan -input=false -detailed-exitcode
```

最後の終了コードは0=差分なし、2=差分あり、1=取得または評価の失敗。適用後のGitHub API再読取を伴う再planで一致を確かめる。変更を戻すときも宣言を修正してplan→applyする。

## stateと継続運用

初期backendはローカル。`*.tfstate*`、`*.tfplan`、`*.tfvars*`、`.terraform/`はGit管理外。stateを失うと所有関係を失うため、暗号化したアクセス制限付きバックアップを保持する。OneDriveなどのファイル同期はTerraformの共有ロックの代わりにならない。同じstateを複数端末で同時に適用しない。

共有運用に移るときは、アクセス制限・暗号化・ロックを備えたbackendを決め、`terraform init -migrate-state`で移行する。Azure等の配備先が決まってから、そのstate用基盤とOIDCの権限を別のbootstrapとしてIaC化する。

CIのPR検査に管理tokenは不要。定期drift検知は未接続で、認証経路と共有stateが揃ってから、信頼済みブランチの宣言に対するplanとして追加する。現在のworkflowの緑を「GitHub設定にdriftがない」と読まない。

参照: [GitHub provider](https://github.com/integrations/terraform-provider-github/blob/v6.13.0/docs/index.md)、[ruleset](https://github.com/integrations/terraform-provider-github/blob/v6.13.0/docs/resources/repository_ruleset.md)、[Actions権限](https://github.com/integrations/terraform-provider-github/blob/v6.13.0/docs/resources/actions_repository_permissions.md)、[Dependabot設定](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)。
