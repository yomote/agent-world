# Vertical Sliceの検証記録

確認日: 2026-09-06。Windows / Node.js 22.23.2 / Python 3.11.9。

## 実行結果

| 対象                         | 結果                                                                              |
| ---------------------------- | --------------------------------------------------------------------------------- |
| ESLint / Ruff                | PASS                                                                              |
| Prettier / Ruff format check | PASS                                                                              |
| Vitest                       | 6件PASS。確定前の表示維持、failure、通信失敗、応答順序、World再起動、Actor分離    |
| pytest                       | 22件PASS。move、4方向の境界、未知Actor、不変な観測、同時実行、実FastAPIのJSON受付 |
| TypeScript / Vite build      | PASS                                                                              |
| API再生成                    | OpenAPI JSONとTypeScript定義の再生成前後のハッシュが一致                          |
| Python依存                   | `pip check` で不整合なし                                                          |
| 1コマンド起動                | `python scripts/dev.py` で依存セットアップからVite/FastAPIの起動まで成功          |
| 実ブラウザー                 | 下記の操作を実API・実Phaserで確認。ブラウザーerrorログ0件                         |

## 実画面で確認した振る舞い

- 初期位置A=(3,2)、revision 0を取得し、Phaserのグリッドに表示。
- 右ActionでA=(4,2)、revision 1。Traceに同じActionのsuccessと(3,2)→(4,2)。
- 右端A=(7,2)でさらに右へActionを発行。failure / out_of_bounds、座標(7,2)、revision 4を維持。
- random開始で上下左右のActionと成功・失敗のTraceが増える。停止後はこのタブのTrace件数が20件のままで、自動発行が止まる。

## 検証の限界

GitHub Actions上の実行、macOS/Linuxの起動、実ブラウザーでの通信断とサーバー再起動は未検証。
通信断・再起動はSessionの単体テストで確認している。ローカルの停止操作は手順として用意したが、Ctrl+Cによる全プロセス終了はこの確認では実行していない。

Vite buildはPhaserチャンク約1.2 MB（gzip約332 kB）のサイズ警告を出す。pytestは固定したStarlette/httpx・AnyIOの組み合わせによる非推奨警告を2件出す。いずれも上記チェックの失敗ではない。

並行作業で追加された将来設計・開発運用・インフラ設定は、このVertical Sliceの実画面検証の対象に含まない。
