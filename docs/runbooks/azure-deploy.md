# Azure deploy・検証・rollback

## deploy経路

`Deploy Azure` workflowはmain向けの全PRで起動し、必須`container-check`を必ず生成する。PRがReadyかつproduction containerへの入力が変わった場合だけ、実imageをbuildして1 workerで起動し、health、UI、World API、未知APIの404を確認する。入力が変わらないPRは差分判定だけで成功し、不要なcontainer buildを消費しない。DraftでskipされたrunはPASSとせず、merge前にcurrent headの成功を確認する。

`Deploy Azure`はmainへの通常push、手動実行、工場の`repository_dispatch: agent-world-merged`を受ける。dispatchはPR番号、PR head SHA、merge commit SHAをGitHub APIのmerged PRへ照合し、そのmergeがjob開始時に固定したcurrent mainの祖先であることを確認する。pushもevent commitがcurrent mainの祖先であることを確認する。どのeventでもcurrent mainからdeploy対象pathを最後に変更したcommitをdesired sourceにするため、後続のdocs-only mergeやconcurrencyのpending置換があっても対象変更を取りこぼさない。

source eventのread-only照合後、image mutationより先にmodeを検査する。通常deployは承認済みbootstrapが設定した`AZURE_DEPLOY_ENABLED=true`、Azure ID / RG / app、Entra auth、実測JPY、1件以上のBudget通知先を必須にする。初回image準備だけは手動dispatchの`image_only=true`と確認文字列`publish-ghcr-image`を必須にし、Azure login / deployをskipする。これによりPR mergeやmain pushだけではGHCR packageを作成・更新しない。

通常pushとdispatchは同じconcurrencyへ直列化する。desired sourceの公開GHCR SHA tagが既にあればそのdigestを再利用し、なければ1回だけbuild/pushする。既存`GITHUB_TOKEN`で認証したpublish用tokenによるmanifest取得の404だけを未作成と扱い、token取得失敗、認証拒否、通信エラー、digest欠損ではtagを上書きしない。現在Container Appのimageが同じdigestならrevision更新を省略してsmokeだけを行う。

工場が`GITHUB_TOKEN`でmergeすると通常push workflowが抑止されるため、merge成功応答を得た同じtrusted `workflow_dispatch`からrepository dispatchを1回だけ送る。dispatch結果不明時は再送せず、merge済み・deploy不明として失敗を残す。Azure workflowがdefault branchへ入る前のmergeは受信できないため、初回はAzure変更がmainへ入ったpushまたは手動実行で開始する。

## 自動smoke

deployは次を確認する。

1. GHCR digestが匿名取得可能。
2. OIDCで専用subscription / Resource Groupへ接続可能。
3. Container Appのimageが対象digest。既に同じなら更新しない。
4. HTTPS `/healthz`が`200 {"status":"ok"}`。
5. public modeはUIとAPIが200、Entra modeは未認証UI/APIがloginへredirectまたは401。

## 初回のlive validation

自動smoke後に本人がスマートフォンbrowserでURLを開き、Entra loginできること、別アカウントが拒否されること、AをmoveしてEventと確定位置が一致することを確認する。再起動でworld_id、revision、位置が初期化されることも実環境で1回確認する。これらを行う前は「スマートフォン利用可能」「本人認証完了」「World reset確認済み」と記録しない。

`Azure cost and drift`を手動実行し、OIDCのread、core what-if、auth、請求通貨、当月costがすべて成功することを確認する。Entra registration / assignmentはOIDCにDirectory Readerを与えないためworkflowでは`not_run`であり、本人user contextの`Test-EntraDirectory.ps1`成功を別の受入証跡にする。両方が揃ってから日次監視を受入れる。

## rollback

rollback条件は、新revisionのhealth失敗、認証境界の緩み、UI/API smoke失敗、Worldの契約回帰。直前に成功したGitHub runのdigestを指定し、1回だけ更新する。

```powershell
az containerapp update `
  --resource-group rg-agent-world-jpe `
  --name <app-name> `
  --image ghcr.io/yomote/agent-world@sha256:<last-known-good-digest>
./scripts/azure/Test-AzureSmoke.ps1 -BaseUrl https://<fqdn> -AuthMode entra
```

rollbackも新しいrevisionであり、メモリ内Worldはresetする。書き込み結果不明なら同じ更新を再送せず、actual imageとrevisionを読む。停止が必要ならmin replicasは既に0なのでtrafficを止めるかingressを無効化する。Resource Group削除は通常の停止手段にせず、削除対象と回収不能なKey Vault purge protectionを確認した別作業にする。

初回external ingress適用直後のFQDN取得またはauth smokeが失敗した場合、`Deploy-AzureCore.ps1`は`az containerapp ingress disable`を1回だけ実行し、externalでないactualを読む。disableの応答が失敗でもactualが閉じていれば封じ込め済みとして記録し、成功・失敗のどちらでも元の公開検証は失敗として停止する。actualを読めない、またはexternalのままなら結果不明または封じ込め失敗として停止し、自動再送しない。
