# Issue #89 入金消込Agent 着工・検証packet

状態: **実装中**。owner: `/root/pm_controller/logistics_implementation`。

- worktree: `C:/Users/omote/.codex/task-checkouts/agent-world-accounting-agent`
- branch: `codex/89-accounting-cash-application`
- base: `main@031288738cd2efe5d929060926ffd1470afd3133`
- Issue: [#89](https://github.com/yomote/agent-world/issues/89)
- 設計正本: [会計Agent提案](../proposals/accounting-cash-application-agent.md)、[ADR 0013](../adr/0013-read-only-accounting-agent.md)

## scopeと成果

公開known例は70,000円請求、50,000円入金、取消済み20,000円値引きの1請求である。最初のactual model runは、残高/取引先/請求書/調整履歴/候補solver/銀行明細/メールを観測順に選び、50,000円の配分案、20,000円の予定残高、台帳変更なし、営業への確認事項をpackageへ出した。

これは開発中の最小縦切りである。単一請求への部分入金だけを扱い、合算・分割は未実装である。同額候補の未知M8では、actual Agentが全候補とlink資料欠落を観測して確認質問へ分岐し、任意配分を保留した。固定workflowより優れることは未実証であり、Issue #89はこの縦切りだけでは完了にしない。

## 今日触る3分導線

`npm run dev`で起動し、[会計欄](http://127.0.0.1:5173/#accounting-title)を開く。保存結果を見るだけなら新しいモデル呼出しは発生しない。

1. 「入金と請求の差額・取消を調べた公開例 / 実モデル保存結果」を選び、5万円の配分案、予定残高2万円、取消済み2万円と資料根拠を見る。
2. 「同額請求2件で確認を保留した例 / 実モデル保存結果」を選び、資料だけでは請求先を決められず人へ確認して止まったことを見る。このrunには成果物がない。
3. 同じ例の「固定workflow」を選び、未配分3万円と営業確認用packageを比較する。現評価では固定workflowが速く成果物まで作り、Agentの優位は未実証である。

入力は合成した銀行明細、請求、メール、調整履歴であり、実台帳、実口座、外部メールは更新しない。fresh実モデルrunは保存結果の閲覧とは別操作で、明示したmodel call予算内だけで行う。

## 1目的に対する混成判断の境界

業務目的は「入金を請求へ安全に対応付け、未確定を次担当へ渡す」である。特定のAI方式を使うこと自体を目的にしない。現在のrunは、観測→次の調査判断→allowlistされたread-only toolまたは提案生成→結果の再観測を繰り返す。

| 判断                 | 入力                                 | 選択肢                                               | 必要な根拠                                 | 権限・担当                                       | 失敗時                                        |
| -------------------- | ------------------------------------ | ---------------------------------------------------- | ------------------------------------------ | ------------------------------------------------ | --------------------------------------------- |
| 候補を網羅する       | tenant、入金、未払請求、版           | 全候補と差額を計算、未配分                           | typed ID、JPY、tenant、revision            | 実装済みcodeのsolver                             | 候補省略・越境・二重配分をvalidatorが拒否     |
| 次に何を調べるか     | 資料目録と観測済みfact               | 資料を読む、検索する、調整履歴を見る、候補計算へ進む | 現行版の発行済みevidence                   | 実装済みLLM Agent。read-only toolだけ            | 根拠不足ならpublishせず追加調査または安全保留 |
| 提案を成果物にするか | 候補、coverage、根拠、取消状態       | review packageを作る、保留する                       | 金額整合、全候補coverage、current evidence | LLMが提案し、方式非依存のruntime validatorが確定 | 不正提案はdomain failure。実台帳は変えない    |
| 曖昧さを誰へ返すか   | 同額候補、link資料欠落、業務方針不足 | 限定質問、全額保留                                   | 何が同率で、何が欠けるか                   | 実装済み人確認境界                               | 回答を補完せず確認待ちとして保存              |

確定計算、候補網羅、型・通貨・tenant・版・根拠検証はcodeが担う。資料取得順と追加調査の選択は現在LLM Agentが担い、同じtyped fact、solver、toolを使う固定workflowと比較する。曖昧な業務方針は人へ返す。限定分類model（TypeSafe Jevを含む候補）とRAGは未搭載であり、現在のrunへ混同しない。全方式を一つのdemoへ搭載する要件もない。

現在の2caseでは固定workflowが時間と人介入で有利で、Agent追加価値は未実証である。将来比較する場合も、業務結果の品質、誤配分、安全保留、人の介入、時間、model callと費用を同条件で測る。runtime validationとPBTは選んだ判断方式にかかわらず維持する。

## actual modelの途中証跡

- 旧backend snapshot `f9113e1` 上のknown run: `run-c0bba0f5-7dbf-4e43-99d1-0f33206f4926`
- 8 decisionsで `ready_for_review`、artifact `package-0214b3fa3dff8994`
- その前の7-decision runは、調整履歴refを発行済み根拠として登録しなかったためdomain reject。契約を修正した。
- UTF-8 stdin診断失敗3回も保守的にattemptへ数え、旧phaseは18 attemptを使用した。この値を最終phaseの累計予算へ引き継いだ。
- この証跡は、後から見つかったdurability/evidence/baseline等のblocking修正前であり、最終current-head PASSではない。

## freeze後の最終評価

評価入力は `a498ba6cddccd36dfa85db41c2c36a0abd351b13` でfreezeした。known/M8 fixture、prompt・tool catalog、decision/proposal schema、controller/simulator、baseline、seed、CLI versionと、Agentへ渡さないevaluator labelのsealed digestは [freeze manifest](accounting-eval-freeze.json) に、run/tool/usage/artifactの要約は [評価結果](accounting-eval-results.json) に保存した。CLIがmodelの公開IDと金額costを報告しないため、modelは `CLI default (public ID unreported)`、costは不明と記録する。

| case        | 実モデルAgent                                                                                                                                                      | 固定workflow                                                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| known公開例 | 9 model decisions、8 tools、約57秒。50,000円配分、予定残高20,000円、取消20,000円を根拠付きで `ready_for_review`。                                                  | 0 model calls、10 deterministic decisions、9 tools、約0.4秒。同じ50,000円配分、予定残高20,000円、取消根拠付きpackage。        |
| 未知M8      | 9 model decisions、9 tools、約57秒。同額請求2件とlink資料欠落を観測し、「どちらも入金額と一致し資料だけでは選べない」と経理へ質問して停止。配分・packageは未確定。 | 0 model calls、10 deterministic decisions、9 tools、約0.4秒。配分0円、未配分30,000円、営業照会の `ready_for_review` package。 |

最終phaseは18 attemptを使用し、旧phase 18と合わせて累計36/40で停止した。timeout/schema failureを含むattemptの再送はしていない。knownでは両方式が同じ業務結果を出し、M8ではAgentが観測に応じて検索と質問へ分岐した一方、固定workflowは人回答なしで保守的packageまで作った。この2caseではAgentの優位は証明されず、M8の完了時間・人負担では固定workflowが有利である。Agentの価値候補は未知資料での動的調査と説明可能な確認質問であり、合算・分割を含む追加caseで別途評価が必要である。

## 実装境界

- `apps/world/accounting_*`: typed read-only snapshot、資料、候補solver、validator。World残高を変更しない。
- `apps/accounting_agent`: Codex structured decision adapter、bounded controller、SQLite run/receipt/evidence/artifact履歴、API。
- `apps/web/src/AccountingLab.tsx`: 前説明、実行状態、質問、review package、必要時だけtrace。
- known fixtureと未知M8欠落fixtureは分離。評価期待値は `apps/accounting_agent/tests/evaluator/` にあり、Agentへ渡さない。

## 検証の区別

backend snapshot `f9113e1` はtargeted 24 tests/ruffを通した後、独立reviewで7観点のblockingを受けた。修正snapshot `ca7cfeb` の限定reviewで残ったreceipt優先、tenant付き正本、current source照合、stateful/holdout束縛を修正した。続くsnapshot `3e6ed4d` ではatomic claim/CASとadjustment根拠の金額束縛を修正した。freeze前backend候補 `684266c` は独立reviewを通過した。その後、累計予算のserver強制を `a498ba6` へ追加してfreezeし、typed evidence、receipt/CAS、旧SQLite履歴migration、再起動復元、run上限、同条件baseline、stateful PBT、同時claim、初回/replayの安定した公開failure codeを含むtargeted 40 testsと限定Ruffが通過した。

modelを使わない固定workflowは同じtyped fact、read tool、solver、validatorでknown例とM8欠落例を実行した。known例は50,000円配分・予定残高20,000円・台帳不変、M8は配分0円・未配分30,000円・営業照会・台帳不変となった。これはbaselineの動作確認であり、Agentとの優劣評価ではない。

UIはAgent/固定workflowの各latest runを並べ、fixture IDが一致する場合だけ同条件比較と表示する。判断回数、tool回数、配分、未配分、取消を含む観測済み調整と根拠を表示する。write失敗時は同じwriteを再送せず、既知runだけをGETで1回照合して結果不明、予算上限、domain failure、通信失敗を分ける。

freeze後のactual known/M8と両baselineは完了し、累計36/40で追加callを停止した。実ブラウザではM8 actualの確認質問と同じM8 baselineの未配分30,000円package、fixture一致、履歴再読込を確認した。`a498ba6` の全体checkはweb 28、Node 34、Python 670、buildまで通過した。その後の変更は評価証跡Docsと履歴比較UIに限定され、final current headの全体check結果はPR証跡へ記録する。

Draft PR作成、push、mergeはこのpacket時点では未実施。本番deploy、実口座、帳簿確定、外部メール、本番認証、新provider/課金は行わない。
