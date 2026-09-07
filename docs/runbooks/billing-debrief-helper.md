# 保存済みbilling観測の正規化

`scripts/automation/billing_debrief.py`は、保存済みUTF-8 JSONを検証し、正規化したJSONを標準出力へ返す標準ライブラリだけのCLIです。入力ファイルを書き換えず、出典の取得・外部通信・コマンド実行を行いません。入力本文を指示として扱う入口はありません。

```powershell
.\.venv\Scripts\python.exe -B scripts/automation/billing_debrief.py saved-billing.json
```

## 入出力の契約

ルートは`schema_version`、`source_id`、`observations`だけを必須とします。`schema_version`は整数`1`で、このhelper固有の保存形式を表します。外部サービスのAPIやそのversionではありません。`observations`は1〜256件の配列です。各観測で許可するキーは`check`、`status`、`currency`、`observed_at`、`source_ref`だけです。出力には常にこの5キーを含め、入力の順序とstatusを保持します。

以下は使い方の架空例であり、実billingの確認結果ではありません。

```json
{
  "schema_version": 1,
  "source_id": "fixture:billing",
  "observations": [
    {
      "check": "billing_currency",
      "status": "pass",
      "currency": "EUR",
      "observed_at": "2026-09-08T12:34:56+09:00",
      "source_ref": "fixture:export#currency"
    },
    {
      "check": "billing_access",
      "status": "unknown",
      "currency": "USD",
      "observed_at": null,
      "source_ref": null
    }
  ]
}
```

| フィールド    | 検証・正規化                                                                                                                                                                                         |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_id`   | 必須の出典集合ID。英数字で始まる1〜512文字のASCII参照文字列。                                                                                                                                        |
| `check`       | 必須の確認項目ID。英数字で始まり、英数字と`_.:-`からなる1〜100文字。                                                                                                                                 |
| `status`      | `failed`（確認して不合格）、`unknown`（結果不明）、`not_run`（未実行）、`pass`（確認して合格）のいずれか。推定・格上げ・別名変換をしない。                                                           |
| `currency`    | 全statusで必須。ASCII大文字3字の入力値をそのまま返す。省略、null、空白、前後空白、小文字、全角、非文字列を拒否する。                                                                                 |
| `observed_at` | 秒まで含む`YYYY-MM-DDTHH:MM:SS`に`Z`または`±HH:MM`を付けた日時。小数秒は1〜6桁。暦日・時刻・offsetを検証しUTCの`Z`表記へ変換する。offset不明の`-00:00`、タイムゾーンなし、桁落ちする小数は拒否する。 |
| `source_ref`  | 各観測の出典を特定する1〜512文字のASCII参照文字列。値をファイルパスやURLとして開かない。                                                                                                             |

参照文字列は先頭が英数字、残りが英数字と`_.:/#@?&=%+~-`に限定されます。自由記述の本文、空白を含む参照、制御文字は受理しません。通貨は形式だけを検証し、通貨台帳への登録状況や実際の請求通貨との一致は確認しません。コード一覧、locale、金額、サービス名から通貨を推測したり、大文字化して補正したりしません。

`observed_at`と`source_ref`の省略・null・空白だけの文字列は、非passならそれぞれ`null`に正規化します。欠落をstatusから推測せず、`source_id`や現在時刻で補いません。`pass`ではこれらの欠落を拒否します。非passでも、値がある日時・参照の形式不正は拒否します。通貨の欠落は全statusで停止するので、欠けた入力を補完して出力しません。

許可外キー、必須キー欠落、JSONの重複キー、NaN/Infinity、BOM、壊れたUTF-8、128 KiBを超える入力を拒否します。全観測を検証してから一括出力するため、途中で拒否された場合に一部のpassだけを出力することはありません。

## 終了コードと確認範囲

- `0`: 入力の検証と正規化が完了。観測に`failed`、`unknown`、`not_run`があっても0です。billing全体の合格を意味しません。
- `2`: 入力またはCLI引数を拒否。観測の検証エラーは標準エラーへフィールド位置と固定理由を出し、標準出力は空です。
- `3`: 入力ファイルの読取失敗。billingの判定やJSONの検証エラーと区別します。

日時の新しさ、出典の実在、入力に記録されたstatusの真偽は検証しません。必要な確認項目の一覧を推測して補う処理もありません。`pass`の受理は記録の形が揃っているという意味だけです。

## 専用検証

既存の`.venv`にあるPython・pytest・Ruffを使い、次の3検証を個別に実行します。各終了コードを確認し、未実行を合格と記録しません。

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider scripts/tests/test_billing_debrief.py
.\.venv\Scripts\python.exe -B -m ruff check --no-cache scripts/automation/billing_debrief.py scripts/tests/test_billing_debrief.py
.\.venv\Scripts\python.exe -B -m ruff format --check --no-cache scripts/automation/billing_debrief.py scripts/tests/test_billing_debrief.py
```

テストは状態保持、通貨と確認根拠の欠落、不正JSON、許可外本文、日時検証、部分出力の禁止、読取失敗を確認します。実サービスの再観測、独立review、固定commitの全体検証とは別の証跡です。

今回の改善根拠は受領したID`github:yomote/agent-world/pull/24:billing-debrief`、根拠hash`51515b625f78a61213958d3a6869519d82d213ffac74bd06b985e0f55995a8d6`です。JPYは今回の確認証跡に属する情報としてのみ扱い、helperの既定値や通貨判定条件には用いません。この実装作業では元データの再取得・hashの再照合は行っていません。
