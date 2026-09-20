import { AccountingLab } from "./AccountingLab";
import { LogisticsLab } from "./LogisticsLab";
import { WorldDemoPage } from "./App";

function DemoHeader({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
        </div>
      </header>
      <p className="demo-back">
        <a href="/">← デモ選択へ戻る</a>
      </p>
    </>
  );
}

function DemoHome() {
  return (
    <main className="demo-home">
      <header className="demo-home-heading">
        <p className="eyebrow">AGENT WORLD / DEMOS</p>
        <h1>今日触れるデモを選ぶ</h1>
        <p>それぞれ独立した画面で、目的と結果を確認できます。</p>
      </header>
      <nav className="demo-grid" aria-label="デモ一覧">
        <a className="demo-card primary" href="/demos/accounting">
          <span>会計</span>
          <h2>入金消込のレビュー準備</h2>
          <p>既存の会計Labを開きます。</p>
        </a>
        <a className="demo-card" href="/demos/logistics">
          <span>ルール版</span>
          <h2>配送計画を比較する</h2>
          <p>倉庫能力を考えた再配分と、トラック追加だけの差を見ます。</p>
        </a>
        <a className="demo-card" href="/demos/world">
          <span>基礎Sandbox</span>
          <h2>WorldのActionを観察する</h2>
          <p>移動ActionとSimulatorが確定した結果を追います。</p>
        </a>
      </nav>
    </main>
  );
}

function AccountingDemoPage() {
  return (
    <main>
      <DemoHeader eyebrow="BUSINESS DEMO / ACCOUNTING" title="入金消込のレビュー準備" />
      <AccountingLab />
    </main>
  );
}

function LogisticsDemoPage() {
  return (
    <main>
      <DemoHeader eyebrow="BUSINESS DEMO / LOGISTICS" title="配送計画を比較する" />
      <LogisticsLab />
    </main>
  );
}

export default function RootApp() {
  switch (window.location.pathname.replace(/\/$/, "") || "/") {
    case "/demos/accounting":
      return <AccountingDemoPage />;
    case "/demos/logistics":
      return <LogisticsDemoPage />;
    case "/demos/world":
      return <WorldDemoPage />;
    default:
      return <DemoHome />;
  }
}
