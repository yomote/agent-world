# 読取り専用の日次PM棚卸し

agent-world の定期PM棚卸しを1回だけ行う。Front Deskではない専用のread-only PM workerとして、短い最終reportだけを返す。編集、Git操作、commit、push、PR/Issueの作成・更新・close、label更新、コメント、通知、Front Deskのclaim、workerのdispatch・再起動を行わない。

最初に `AGENTS.md` を読む。`docs/runbooks/pm-workflow.md` と `docs/runbooks/api-rate-budget.md` は存在する場合だけ読む。次に、GitHub readを最大8回まで使い、#53、#66、#79、#80、#82、#83だけを個別に確認する。関連PRはIssue本文で直接参照されるものだけにする。認証失敗、通信失敗、rate limit、Dashboard未到達、ローカル記録なしは再試行せず未観測と書く。worker runtimeを正当に観測できない場合は推測しない。

追加のtool callはしない。日本語で300語以内、次の全行を含むfinal reportを返す。

```text
# PM棚卸し
取得時刻: <JST ISO 8601>
changed: <事実または「なし」>
completed: <事実または「なし」>
running: <owner/work unit、未観測は明記>
blocked: <事実または「なし」>
needs-human: <判断または「なし」>
escape: <#53 → #66 → #79 → #80 の順と次手>
dashboard: <鮮度または未観測理由>
next: <次にPMが分配すべき1手。分配はしない>
limits: <GitHub read数、未観測、外部操作なし>
```
