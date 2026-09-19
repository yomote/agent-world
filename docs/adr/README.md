# ADR一覧

このディレクトリは、製品アプリ、共有product実行基盤、公開インフラの**配置・通信・状態所有・信頼境界**に関する決定だけを置く。GitHub設定、CI、merge、PM、registry、開発automationの判断は、技術schemaやCASを含んでも製品ADRにしない。

## 現行の製品・インフラADR

| 番号                                                      | 決定                                                     |
| --------------------------------------------------------- | -------------------------------------------------------- |
| [0001](0001-authoritative-world.md)                       | World Simulatorを状態変更の唯一の責任者にする            |
| [0002](0002-async-agent-world.md)                         | Agentの判断・Worldの実行・結果配送を非同期として設計する |
| [0006](0006-azure-container-apps.md)                      | Azure Container Appsへ単一コンテナで初回公開する         |
| [0007](0007-azure-management-status.md)                   | 管理statusをContainer Appsと非公開Blobへ置く             |
| [0008](0008-shared-action-trace.md)                       | 同じWorldの有限Event履歴をpollingで共有する              |
| [0012](0012-logistics-scenario-and-local-capabilities.md) | 物流Scenarioとローカルcapability境界                     |

## 移転済みの判断記録

次の旧番号は既存リンクを保つ移転noticeであり、製品ADRではない。背景・構造上の制約・代替案は[開発・運用基盤の判断記録](../development-operations.md)、現在の仕様と手順は同文書の表に示す正本を参照する。

| 旧番号 | 分類                             |
| ------ | -------------------------------- |
| 0003   | CIと外部照会の開発運用           |
| 0004   | GitHub設定IaC                    |
| 0005   | merge gateとformal local entry   |
| 0009   | bounded local改善runner          |
| 0010   | Front Desk依頼registryとhandover |
| 0011   | 日次PM棚卸し                     |

新規作成時は[template](template.md)を使う。
