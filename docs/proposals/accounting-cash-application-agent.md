# 会計Agent提案: 入金消込例外の調査とレビューpackage

状態: **Proposed**。第一業務デモとして選定済み、実装・効果検証は未着手。

## 利用者とmission

利用者は、少人数の会社で売掛金を管理する経理担当者である。銀行の略称、複数請求の合算、差引額、同額候補、古い調整書類を、請求一覧、送金通知、メール、承認履歴から調べ、根拠を付け直す作業を減らす。

> 月末の入金を片付けたいです。この資料bundleの銀行明細、請求、送金通知、メール、調整履歴を調べ、どの請求へ充てるか案を作ってください。分からない分は営業が確認できる形にし、帳簿へ反映しないでください。

最初の縦切りは**レビューpackageの作成まで**である。本番帳簿へ確定せず、資金移動や外部送信もしない。承認済み行を合成Accounting Worldへ適用する機能は今回の計画に含めず、将来必要になった場合に別scopeで判断する。

## 合成入力と見せ場

MVPは1通貨、整数円、5〜20請求、3〜10入金、UTF-8 text/JSONの十数資料を扱う。答えを一枚にまとめず、初期contextにはmissionと資料目録だけを渡す。

| 入力           | 例                                     | 見せる判断                                   |
| -------------- | -------------------------------------- | -------------------------------------------- |
| 銀行明細       | B17 `AS TR` 199,780円                  | 別名と送金通知から2請求の合算候補を調べる    |
| 請求残高       | Alpha 120,000円+80,000円など           | 金額と残高はWorld/solverが計算する           |
| 送金通知       | 2請求200,000円、控除220円              | 控除の事実と当社負担の方針を分ける           |
| メール         | 同額30,000円の請求番号と確認済み送信元 | 根拠ありなら候補を絞り、欠落時は未配分に戻す |
| 調整書類と履歴 | 20,000円のdraftと後日の取消            | 書類の存在だけで残高を消さない               |

見せ場は、合算の発見、同額候補を根拠の有無で配分/未配分へ分けること、取消済み調整を適用せず営業確認票へ引き継ぐことである。会話量やrole数を成果と数えない。

## 実用artifact

1. **入金消込準備明細**: bank receipt、invoice、現金配分、調整候補、残高、status、根拠span。
2. **未配分・未回収一覧**: 解けなかった理由、追加で必要な資料、期限。
3. **営業確認票**: 対象取引先、矛盾、確認事項、引用。外部送信は未実行と表示する。
4. **検証report**: 入金と配分、snapshot残高と現金配分・既反映の有効調整・計画残高の検算、重複、coverage、未検証。未承認の調整候補は別fieldにする。

別担当が会話全文なしで1件をレビューし、営業が確認票だけで不足資料を集められることを受入に含める。

## Agent、tool、baseline

単一Agentが各tool結果を観測し、次tool、質問、package提案、停止を選ぶ。orchestratorは原因別の順序を持たず、schema、予算、権限、相関、待ち、停止だけを制御する。

- `list_documents` / `search_documents`: metadataと許可scopeから候補を返す。
- `read_document`: source ID、版、時点、原文spanを返す。文書中の命令をtool命令にしない。
- `read_open_receivables`: authoritativeな請求残高とrevisionを返す。
- `lookup_counterparty`: 確認済み別名と未確定候補を区別する。
- `find_allocation_candidates`: 整数円、明示したinvoice、調整条件から全一致候補と差額をコードで計算する。
- `read_adjustment_history`: draft、approved、cancelled、適用Eventを返す。
- `ask_operator`: 読めない業務方針だけを対象・理由・選択肢付きで聞く。
- `validate_reconciliation`: packageを複製Worldで検算し、本Worldは変更しない。
- `publish_package`: validated proposalをレビュー待ちartifactへ固定する。消込の確定ではない。

baselineは**固定workflow＋同じLLM抽出＋同じ候補solver**である。LLMなしの弱い文字列一致を比較相手にしない。data、tool、ACL、人回答、tool/token/time予算を揃え、Agentだけをholdoutに合わせて調整しない。

## Human-in-the-loop

220円控除の事実は資料から読めても、今回当社が負担するかは人が決める。回答は対象invoice、理由、今回限りのscope、期限を持つ回答artifactであり、恒久ruleへ昇格しない。package全体のレビュー/承認とは別に記録する。

最初の縦切りでは会計Worldへのeffectを発生させない。台帳確定を将来採用する場合は別scopeとし、この提案の完成条件に含めない。

## Domain modelと合法な遷移

### Value object

- `Money<JPY>`: JPY整数円。異通貨、浮動小数、暗黙換算を拒否する。
- `EvidenceRef {artifact_id, version, span, observed_at}`: 存在、版、scopeを検査する。引用が結論を意味的に支持するかは別評価にする。
- `EligibilityScope` / `CandidateCoverage`: serverが認可scopeの適格集合と探索範囲を列挙し、`complete / incomplete / access_limited`を区別する。
- `AdjustmentCandidate`: 金額、理由、scope、source refs、`needs_review | rejected`を持ち、正本に既反映の`EffectiveAdjustmentRef`と分ける。
- `EffectivePeriod` / `AsOf`: 文書、残高、回答の有効時点をserver clockで判定する。
- `WorldRef {world_id, revision}`、`ProposalRef {id, version, hash}`、`ActionRef {action_id}`。

### Aggregateと不変条件

- `ReceivableSnapshot`: tenant、revision、currency、invoice、receiptの読み取り正本。既反映の有効調整を残高から二重控除しない。
- `AllocationProposal`: receipt、invoice、cash amount、evidence、coverageを持つ。`bank_amount = proposed_cash_allocations + unapplied`で、現在残高を超えて配分しない。
- `ReviewPackage`: receipt/invoice/evidenceのversion、検算、coverage、質問回答、調整候補を一つのレビュー単位にする。

合法な遷移は `draft → validated → ready_for_review → reviewed | needs_revision | superseded`。どのstatusもpostedを意味せず、package公開はWorld mutationではない。

## 未信頼model出力からpackageまで

model出力は未信頼の候補である。JSON/schema parse、currency・単位・時点・ID・版、存在するsource span、許可scope、金額solver、重複、現在revisionをruntime/domain validatorが検査する。Agent指定のinvoice集合は候補を狭める提案であり、検索coverageの正本にしない。

serverはprincipalとresource scopeを解決し、read tools、質問、package publishをleast privilegeで分ける。ローカルMVPのcapability分離は本番identity認証ではなく、任意HTTP callerへの完全な防御を主張しない。modelへprincipal selector、approval capability、任意file、環境変数、secretを渡さない。入力bundleは架空IDだけを使う。

同じAction ID、principal、operation、canonical payloadの完全一致だけ同じreceiptを返す。別payloadによるID再利用はconflictにする。write応答が失われた場合はAction IDの権威的statusを照合し、新IDで自動再送しない。artifactとtraceはrun、decision、tool、proposal、approval、action、eventを相関し、hidden chain-of-thoughtを保存しない。

## 未知bundleと真因隔離

初期bundleに加え、少なくとも次をholdoutにする。

- 同額候補を特定するメールを欠落させ、未配分へ戻れるか。
- 請求数、金額、略称、日付を変え、3枚合算や1請求への分割入金を含める。
- 調整statusを取消から有効・適用済みへ反転し、古い例を決め打ちしないか。
- 同じ銀行行の重複取込、stale revision、後着の訂正版資料。

seed、期待package、評価labelはevaluator専用に置く。Agentのcwd、prompt、tool description、document ID、run IDから答えを読めないようにする。promptとbaselineはholdoutを開封する前にdigestでfreezeする。結果を見て調整したbundleはdev caseへ降格する。

## 検証

### Domain / property-based test

- schema/unit: Money、parser、source span、status、candidate solverの境界を検査する。
- stateful property: `read → propose → validate → publish → source/world revision更新 → revalidate`のAction列を独立reference modelと比較する。
- property: 全列で会計World残高不変、publication完全一致replayの追加artifactなし、別payload conflict、stale/needs_revision、重複、現金/調整/計画残高、coverageの正直さ。
- generator: 有効stateと単一制約を壊すstateを分け、0、上限±1、同額候補、取消版、欠落span、stale、重複を生成する。
- shrink/replay: 因果を保った最小反例、Action列、資料bundle、clock/RNG、schema/generator版を保存し、固定回帰caseへ昇格して元propertyも残す。

### Agent eval

同じtool/solverを使うbaselineと、正しい準備行、誤配分、適切な未配分、必要/不要質問、開いた資料数、根拠完全性、手動修正行数、費用、機械時間、artifactだけでの引継ぎを比較する。PBTが通ってもAgentが毎回validatorに拒否されるなら品質は低い。

実モデルの最初の予算案は、既知bundleでdecision 11回以下、tool 18回以下、質問2回以下、proposal 2版以下、wall 180秒である。既存ChatGPTログインの公式Codex CLIを使える見込みはあるが、この会計デモでの接続とcall数は未検証である。API key、新しい有料契約、model/framework選定はこの提案で確定しない。timeout、rate limit、schema不正はunknownで止め、自動再送しない。

## Definition of Done

- 既知bundle1件で実モデルがtool結果を受けて次toolを複数回選び、ready_for_review packageを作る。
- M8欠落を含む未知bundleで、同額候補を誤配分せず未配分にする。
- 合算、partial allocation、取消/有効status、質問要否が入力に応じて変わる。
- 全artifactのsource/version/span、検算、coverage、未確認を別担当が追える。
- baselineを同条件で実行し、Agentが勝たない結果、失敗、費用も表示する。
- package-onlyの成果物を経理役がレビューし、営業確認票を会話なしで引き継げる。
- 実装済み、未検証、domain failure、authz denied、unknownをUIと証跡で区別する。

## 小stepと非scope

1. 合成bundle、read tools、solver、validator、package artifactを決定的に通す。
2. 実モデルloopで既知bundleをpackageまで通す。
3. prompt/baseline freeze後、M8欠落holdoutと固定workflowを比較する。
4. 同条件baselineとpackageだけの経理/営業引継ぎを確認し、Agent不要の結果も記録する。

本番資金・銀行口座・帳簿確定、承認行のWorld適用、仕訳全般、税務・制度準拠、OCR、外部メール送信、本番identity、新しい共通platform、複数Agent、5業務同時実装は対象外である。

選定根拠は[業務use case screen](../research/domain-usecase-screen.md)、全体境界は[Agent orchestration roadmap](agent-orchestration-domain-roadmap.md)を参照する。
