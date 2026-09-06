# 0005. 信頼済みmainからcurrent headだけを自動mergeする

- Status: Accepted
- Date: 2026-09-06

## 背景

手作業の単一課題ループはreviewと検証をcurrent headへ結び付けるが、証跡が揃った後も担当者がGitHub状態を読み直してmergeする必要があった。Mind Inboxではnative auto-mergeが最後のstatus更新後に発火せず、条件が緑のまま止まった実績がある。一方、PR由来コードを特権contextで実行するgate、自己発行statusだけで通るgate、コメントやCI完了から連鎖するbotは採用できない。

## 決定

- 信頼済み`main`の`workflow_dispatch`だけを入口にする。PR番号、40桁のexpected head、merge実行の真偽を明示入力し、`ref=main`以外ではjobを実行しない。
- gateはopenかつReady、base=`main`、同一repo、merge可能、対象外labelなしを検査する。`needs-human`と`release`は対象外とし、merge可否が未知の間も実行しない。
- 独立Sol reviewは、信頼できる投稿者がPRコメントの先頭へ次の証跡を記録する。40桁headが変われば失効する。

  ```text
  <!-- agent-world-independent-review -->
  head: <40桁SHA>
  verdict: pass
  reviewer: <reviewer識別子>
  ```

  同じGitHubアカウントから投稿するmarkerはreviewerのidentityや独立性を認証しない。PMが別会話へ割り当てた独立Sol reviewの報告を統合workerが転記したcurrent-head受入記録である。同じheadを取り消すときは新しいtrusted declarationを`verdict: fail`で投稿し、gateは最新の宣言を優先する。

- current headの`CI` workflow runが`pull_request`イベントで`completed/success`になったことをAPIから確認する。`skipped`、古いhead、別PR、自己発行commit statusは根拠にしない。
- activeなmain rulesetに管理者を含むbypassがなく、PR必須、未解決thread禁止、strictな`check`必須が適用済みであることをAPIから確認する。
- すべて揃った時だけexpected SHA付きsquash merge APIを1回呼ぶ。native auto-mergeは条件変化後も有効状態が残る可能性があるため無効のままにする。API結果が不明なら再送せず、同じheadのmerge完了をread-onlyで1回だけ確認する。
- `GITHUB_TOKEN`によるmerge pushは別workflowを起動しないため、merge成功後にmain CIを`workflow_dispatch`し、`agent-world-merged` repository dispatchへPR番号、PR head、merge commit SHAを渡す。Azure deployはpayloadと現行main/PRを再検証する。どちらかのdispatchが失敗または不明なら再送せず、merge済みであることと各dispatchの`sent` / `failed` / `unknown` / `not_run`を明示して止める。
- CI待ちは60秒以上の間隔で最大10回とする。権限不足、取得不能、100件を超えて全量を確認できないコメント／thread、書き込み結果不明、head/baseのraceは失敗として止め、書き込みを再送しない。

## 初回bootstrap

空の`main`にはgate workflowがないため、PR #1だけはreview済み固定headの`scripts/merge_gate.py`を統合workerがローカルから実行する。先にPRをReady化してcurrent-head CIを成功させ、Terraformでrepo設定とrulesetをapplyし、再planで差分なしを確認する。同じreview証跡とexpected headを使い、cleanなcheckoutの`HEAD`一致を`--bootstrap-source`で強制してから`--execute`を一度だけ実行する。Terraform plan/applyも同じcleanなreview済みcheckoutから行う。以後は`main`上のworkflowを使う。

## トレードオフ

常駐sweepやコメント起点の連鎖がないため、dispatchを開始する主体は必要である。一度開始すればCI待ちからmergeまで有界に完走する。独立性そのものはGitHubアカウントで証明せず、PMが別会話で割り当てたreviewerの報告を統合workerが証跡化する運用と組み合わせる。gateはその証跡の投稿者、書式、current headとの一致を検査する。ruleset詳細の`bypass_actors`はwrite accessがないidentityには返らないため、workflow tokenで空配列を取得できることを初回live実行で確認する。欠落時はmergeせず、より広いtokenへ自動fallbackしない。
