# テスト方針

Mind Inboxの「静かな回帰を止める」「取得失敗と成功を区別する」を小さく再利用する。
網羅率の数字や単純な受け渡しテストを増やすことを目的にしない。

| 層              | 対象                                     | 防ぐ回帰                                                                            |
| --------------- | ---------------------------------------- | ----------------------------------------------------------------------------------- |
| Python unit     | WorldSimulator                           | 境界外移動、不正移動、未知Actor、観測経由の変更、同時更新消失、EventとStateの不一致 |
| API integration | 実FastAPIと実SimulatorをTestClientで接続 | JSON契約、型の暗黙変換、不正Actionが状態を変更する抜け道                            |
| Frontend unit   | Sessionとrandom actor                    | 応答前の移動、古い観測での巻き戻し、通信失敗の偽装、再起動追従、Actorの観測変更     |
| Contract        | OpenAPIとTypeScriptの再生成差分          | PythonのAPI変更がフロントの型に反映されない状態                                     |
| 手動の通し確認  | 実Vite・FastAPI・Phaser                  | Canvasの描画、Actionボタン、Trace、random開始停止                                   |

各テストのコメントに防ぐ回帰を書く。FrontendのAPIダブルは順序・通信異常を制御する目的のみ。画面が実サーバーと動いた証明には使わない。
UI snapshot、大規模E2E基盤、LLM評価、カバレッジの一律閾値、定期監視は今回は導入しない。

CIは `npm run check` と契約再生成差分を実行する。ブラウザーの目視確認やGitHub Actions自体の実行結果は別に報告する。
