# ADR 0013: 入金消込Agentはレビューpackageまでを読み取り専用で作る

状態: 設計Accepted／MVP実装中

## 文脈

経理担当は、銀行明細、請求、メール、調整履歴を照合して入金の対応先を準備する。公開例では70,000円の請求、50,000円の入金、取消済み20,000円値引きを使う。値引きを有効扱いして完済にすると危険である。

Agentの価値は、役名や会話量ではなく、観測結果に応じて次の資料、質問、保留、終了を選び、別担当が使える根拠付き成果を残せるかで測る。

## 決定

- 初版はレビューpackageの公開までとし、帳簿、銀行、メールを更新しない。`apps/world/accounting_simulator.py`が読み取り正本とsolver/validatorを持ち、modelやUIへ依存しない。
- 単一model Agentは資料目録だけから始め、allowlist toolの次の1手かpackage提案を返す。hostがtoolを実行し、modelへfile、shell、network、principal selector、write capabilityを渡さない。
- model出力を未信頼としてparseし、JPY整数、tenant、World revision、候補coverage、source version/span、typed factのsubject/value、配分額、重複、取消調整をserverで再検査する。
- run、decision、tool、evidence、artifactはSQLiteへ保存する。hidden chain-of-thoughtは保存しない。advance/answerはAction ID、canonical payload、step versionのreceiptで一意にし、pending/unknownを新Actionで再送しない。
- 1 runはdecision 11、tool 18、質問2、proposal 2、180秒を上限とする。初期actual評価全体は24 decisionで、timeout/schema不正もattemptへ含める。
- 比較対象は同じ合成資料、決定的fact抽出、read tool、候補solver、validator、権限、人回答、上限を使う固定workflowとする。Agentが負けた結果も表示する。
- known公開例と未知bundleを分離し、fixture/prompt/schema/tool/model/version/seedのdigestを評価前に固定する。期待値はevaluator側だけに置き、Agentの空cwd/context/toolから読めないようにする。

## セキュリティ境界

Codex CLIは既存ChatGPTログインの通常利用枠を使う。空の一時cwd、read-only sandbox、環境継承なし、built-in shell/browser/computer/apps/multi-agent無効、構造化出力で呼ぶ。内部認証情報を抽出・転用しない。

ローカルAPIはserver内でAgent用read capabilityとoperator回答を分けるが、本番identity認証ではない。127.0.0.1以外への公開、本番帳簿接続、新契約、追加課金、cloud deployは対象外である。

## 帰結

成果物は `ready_for_review` でもpostedではない。UIには合成・簡略・台帳非更新とactual/sampleを表示する。PBTはdomain遷移と不変条件、Agent evalは調査品質と費用を別に評価する。
