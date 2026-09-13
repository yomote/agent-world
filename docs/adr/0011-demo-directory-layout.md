# ADR 0011: デモごとに実装と固有資料を配置する

## 決定

複数のデモや実験を並べられるよう、最初のA + move Vertical Sliceを `demos/resident-move/` へ移す。Python package名 `world` とrootの開発コマンドは維持する。

- World実装を `demos/resident-move/world/` に置く。
- React / Phaser UIとrandom actorを `demos/resident-move/web/` に置く。
- OpenAPIとデモ固有の設計を `demos/resident-move/docs/` に置く。
- 共通の依存定義、scripts、CI、infra、開発runbookはrootに置く。rootの `Dockerfile` はresident-moveをbuildする互換入口として維持する。

resident-moveは削除せず、再現可能なarchiveデモとして保持する。archiveは新機能開発の主対象外という状態を表す。新しい実験は `demos/<name>/` とREADMEから始める。共通SDKやruntimeは、複数デモから同じ責務を実際に使う必要が生じてから抽出する。

`docs/proposals/` に置かれた過去の提案は未採用資料であり、このADRによって採用済み計画にはならない。

## 理由

従来の `apps/world` と `apps/web` は単一アプリがリポジトリ全体の主目的に見え、新しい試行を同じアプリへ積み重ねやすい。実装、生成物、固有設計をデモ単位にまとめると、現在動く実験を残したまま別の試行を独立して追加できる。

一方、現時点で共通基盤を先に設計すると、一つしかない実例から不要な抽象化を固定する。そのため起動・検査の入口だけをrootに残し、runtimeの共通化は保留する。

## トレードオフ

rootのコマンドとDockerfileはresident-moveの配置を知るため、新しいデモを追加しただけでは実行対象にならない。各デモの依存や起動方法が増えた時点で、明示的な選択方法を別判断として追加する。

既存パスを参照する未統合branchは移動後に追従が必要になる。特にDraft PR #53のdomain設計とテスト追加は既存ownerの責務を維持し、先に取り込んだ後、この移動branchをrebaseして最終パスへ移す。
