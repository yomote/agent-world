# ローカル開発

## 起動と停止

Node.js 22.13以上・npm・Python 3.11以上をPATHに用意し、リポジトリ直下で `python scripts/dev.py`。
初回と依存ファイル変更時のみインストールする。認証情報は不要。
Windows/macOS/Linux用のvenvパスを自動選択する。UIは [5173](http://127.0.0.1:5173)、APIは [8000](http://127.0.0.1:8000/docs)。

Ctrl+Cで両サーバーを終了。片方の異常終了時ももう片方を終了し、失敗を報告する。
フロントはHMR、Pythonの変更は再起動で反映する。

個別に起動したい場合は初回セットアップ後に別ターミナルで:

```sh
npm run dev:world
npm run dev:web
```

## 品質チェック

```sh
npm run check
```

順にESLint・Ruff、Prettier・Ruff format check、Vitest・pytest、TypeScript・Vite buildを実行する。
整形は `npm run format`。

APIモデルを変更した場合:

```sh
npm run api:generate
npm run check
```

生成されたOpenAPIとTS型を変更と一緒にcommitする。CIは再生成後の差分もチェックする。

CIはDraft中のjobを省略し、同じPR/対象ブランチでは古い実行を取り消す。pushのまとめ方、結果確認の回数制限、制限応答時の停止は [CI運用](ci.md) を参照する。

## 依存の再現

JavaScriptは `package-lock.json` と `npm ci`、Pythonは `requirements-dev.txt` の固定版を使う。
venvを手動作成するなら `python -m venv .venv`、その環境で `python -m pip install -r requirements-dev.txt`。
起動スクリプトは依存ファイルのハッシュを記録して、変更時のみ再インストールする。

## 動作確認

1. 右ボタンでAが1マス移動し、Traceに同じActionのsuccessと位置変化が出る。
2. 右端でさらに右ボタンを押すとfailureになり、確定位置とrevisionが変わらない。
3. randomを開始し、Traceが増えてWorldが追従する。停止後、新しいActionが出ない。
4. APIを止めた場合、未接続/結果不明の表示が出て成功には見えない。
5. API再起動後は新world_id・初期位置・revision 0を採用する。

## トラブルシュート

- Nodeバージョンエラー: `node --version` で確認し、22 LTSの新しい版へ更新。Volta利用時は `volta run --node 22 npm run dev` でも実行できる。
- ポート8000/5173が使用中: 該当する以前の開発サーバーを停止する。起動スクリプトは別のWorldへ黙って接続しない。
- World未接続: 同じターミナルのAPIログを確認。UIは1秒ごとに観測を再取得する。
- `npm run build` のPhaserチャンクサイズ警告: Phaserを独立チャンクにしている。これはbuild失敗ではなく、初期Sliceの既知のサイズ制約。
- PythonはUTF-8ソース。Windowsのコンソールが日本語を文字化け表示してもソースを別エンコードに変更しない。

`vite preview` やdist単体のホストはAPIを提供しない。このSliceの通し動作には上記開発サーバー構成を使う。本番デプロイは対象外。
