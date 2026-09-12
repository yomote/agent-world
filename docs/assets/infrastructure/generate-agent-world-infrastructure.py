# ruff: noqa: E501
from __future__ import annotations

import base64
import json
from html import escape
from pathlib import Path
from textwrap import wrap

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "agent-world-infrastructure-data.json"
OUTPUT_PATH = ROOT / "agent-world-infrastructure.svg"
ICON_DIR = ROOT / "official-assets"

WIDTH = 1800
HEIGHT = 1260

COLORS = {
    "background": "#ffffff",
    "panel": "#f8fafc",
    "panel_alt": "#f4f6f8",
    "github_panel": "#f7f7f8",
    "github_accent": "#24292f",
    "azure_panel": "#f2f8fd",
    "azure_accent": "#0078d4",
    "entra_panel": "#f1fbfb",
    "entra_accent": "#008a95",
    "text": "#17212b",
    "muted": "#526577",
    "border": "#a9bac9",
    "placed": "#15845d",
    "unverified": "#b66400",
    "working": "#0969da",
    "planned": "#7256a8",
    "data": "#006fbb",
    "auth": "#7650a8",
    "deploy": "#a65b00",
}

ICON_FILES = {
    "repository": "github-invertocat-white-clearspace.svg",
    "container_app": "container-apps.svg",
    "private_blob": "storage-accounts.svg",
    "key_vault": "key-vaults.svg",
    "uami": "managed-identities.svg",
    "log_analytics": "log-analytics-workspaces.svg",
    "budget": "cost-management-and-billing.svg",
    "entra": "microsoft-entra-id.svg",
}

ABBREVIATIONS = {
    "codex_tasks": "CX",
    "collector": "AL",
    "publisher": "PUB",
    "repository": "REPO",
    "actions": "CI",
    "ghcr": "IMG",
    "github_terraform": "TF",
    "entra": "ID",
    "container_app": "ACA",
    "private_blob": "BLOB",
    "key_vault": "KV",
    "uami": "MI",
    "log_analytics": "LOG",
    "budget": "¥",
    "smartphone": "UI",
    "world_app": "WORLD",
}


def attrs(**values: object) -> str:
    return " ".join(
        f'{key.replace("_", "-")}="{escape(str(value))}"' for key, value in values.items()
    )


def rect(
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    fill: str,
    stroke: str,
    radius: int = 16,
    dash: str | None = None,
) -> str:
    extra = {"stroke_dasharray": dash} if dash else {}
    return f"<rect {attrs(x=x, y=y, width=w, height=h, rx=radius, fill=fill, stroke=stroke, stroke_width=2, **extra)}/>"


def line_text(
    x: int,
    y: int,
    value: str,
    *,
    size: int = 18,
    color: str | None = None,
    weight: int = 400,
    anchor: str = "start",
) -> str:
    return f"<text {attrs(x=x, y=y, fill=color or COLORS['text'], font_size=size, font_weight=weight, text_anchor=anchor, font_family='Segoe UI, Noto Sans JP, sans-serif')}>{escape(value)}</text>"


def multiline(
    x: int,
    y: int,
    value: str,
    *,
    width: int,
    size: int = 15,
    color: str | None = None,
    max_lines: int = 3,
) -> list[str]:
    chars = max(8, int(width / (size * 0.75)))
    lines = wrap(value, width=chars, break_long_words=True, break_on_hyphens=False)[:max_lines]
    return [
        line_text(x, y + index * (size + 5), item, size=size, color=color or COLORS["muted"])
        for index, item in enumerate(lines)
    ]


def boundary(
    parts: list[str],
    x: int,
    y: int,
    w: int,
    h: int,
    title: str,
    management: str,
    *,
    dashed: bool = False,
    dominant: bool = False,
    stack_meta: bool = False,
    theme: str | None = None,
) -> None:
    fill = (
        COLORS[f"{theme}_panel"]
        if theme
        else (COLORS["panel"] if dominant else COLORS["panel_alt"])
    )
    stroke = COLORS[f"{theme}_accent"] if theme else COLORS["border"]
    parts.append(
        rect(x, y, w, h, fill=fill, stroke=stroke, radius=22, dash="12 8" if dashed else None)
    )
    if theme:
        parts.append(
            f"<path {attrs(d=f'M {x + 24} {y + 8} H {x + w - 24}', stroke=stroke, stroke_width=5, stroke_linecap='round')}/>"
        )
    parts.append(line_text(x + 24, y + 34, title, size=21, weight=500))
    if stack_meta:
        parts.append(line_text(x + 24, y + 59, management, size=12, color=COLORS["muted"]))
    else:
        parts.append(
            line_text(x + w - 24, y + 33, management, size=13, color=COLORS["muted"], anchor="end")
        )


def icon(parts: list[str], node_id: str, x: int, y: int, size: int = 46) -> bool:
    filename = ICON_FILES.get(node_id)
    path = ICON_DIR / filename if filename else None
    if path and path.is_file():
        if node_id == "repository":
            parts.append(
                rect(
                    x,
                    y,
                    size,
                    size,
                    fill=COLORS["github_accent"],
                    stroke=COLORS["github_accent"],
                    radius=10,
                )
            )
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append(
            f"<image {attrs(x=x, y=y, width=size, height=size, href='data:image/svg+xml;base64,' + encoded, preserveAspectRatio='xMidYMid meet')}/>"
        )
        return True
    parts.append(
        rect(x, y, size, size, fill=COLORS["background"], stroke=COLORS["border"], radius=10)
    )
    parts.append(
        line_text(
            x + size // 2, y + 29, ABBREVIATIONS[node_id], size=13, weight=500, anchor="middle"
        )
    )
    return False


def node(
    parts: list[str],
    nodes: dict[str, dict[str, str]],
    node_id: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    detail: str | None = None,
) -> None:
    item = nodes[node_id]
    parts.append(rect(x, y, w, h, fill=COLORS["background"], stroke=COLORS["border"], radius=14))
    icon(parts, node_id, x + 16, y + 17)
    parts.append(f"<circle {attrs(cx=x + w - 18, cy=y + 18, r=7, fill=COLORS[item['status']])}/>")
    parts.append(line_text(x + 76, y + 34, item["label"], size=17, weight=500))
    copy = detail or item["detail"]
    parts.extend(multiline(x + 76, y + 58, copy, width=w - 94, size=13, max_lines=2))


def connector(
    parts: list[str],
    points: list[tuple[int, int]],
    kind: str,
    label: str,
    *,
    status: str | None = None,
    label_at: tuple[int, int] | None = None,
) -> None:
    commands = [f"M {points[0][0]} {points[0][1]}"]
    for x, y in points[1:]:
        commands.append(f"L {x} {y}")
    dash = "" if kind == "data" else ("10 7" if kind == "auth" else "3 8")
    color = COLORS[kind]
    marker = f"arrow-{kind}"
    parts.append(
        f"<path {attrs(d=' '.join(commands), fill='none', stroke=color, stroke_width=2, stroke_dasharray=dash, marker_end=f'url(#{marker})')}/>"
    )
    middle = label_at or points[len(points) // 2]
    parts.append(
        f"<text {attrs(x=middle[0], y=middle[1] - 7, fill=color, font_size=12, font_weight=500, text_anchor='middle', font_family='Segoe UI, Noto Sans JP, sans-serif', stroke=COLORS['background'], stroke_width=6, paint_order='stroke')}>{escape(label)}</text>"
    )
    if status:
        parts.append(
            f"<circle {attrs(cx=middle[0] + 60, cy=middle[1] - 14, r=5, fill=COLORS[status])}/>"
        )


def legend(parts: list[str]) -> None:
    x = 1035
    y = 92
    for index, (status, label) in enumerate(
        (
            ("placed", "配置済"),
            ("unverified", "接続未検証"),
            ("working", "作業中"),
            ("planned", "計画"),
        )
    ):
        px = x + index * 160
        parts.append(f"<circle {attrs(cx=px, cy=y, r=7, fill=COLORS[status])}/>")
        parts.append(line_text(px + 13, y + 5, label, size=13, color=COLORS["muted"]))
    for index, (kind, label) in enumerate(
        (("data", "データ"), ("auth", "認証"), ("deploy", "配備"))
    ):
        px = x + index * 160
        py = y + 30
        dash = "" if kind == "data" else ("10 7" if kind == "auth" else "3 8")
        parts.append(
            f"<path {attrs(d=f'M {px} {py} H {px + 38}', stroke=COLORS[kind], stroke_width=3, stroke_dasharray=dash)}/>"
        )
        parts.append(line_text(px + 47, py + 5, label, size=13, color=COLORS["muted"]))


def generate() -> tuple[str, int]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    nodes = {item["id"]: item for item in data["nodes"]}
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" {attrs(width=WIDTH, height=HEIGHT, viewBox=f"0 0 {WIDTH} {HEIGHT}", role="img", aria_labelledby="title description")}>'
    )
    parts.append('<title id="title">Agent World 管理status インフラ構成</title>')
    parts.append(
        '<desc id="description">PCとGitHubからAzure管理Resource Groupへのdata、auth、deploy経路、管理方式、現在の確認状態を示す構成図。</desc>'
    )
    parts.append(
        "<metadata>Azure service icons, when bundled, are from Microsoft Azure Architecture Icons and are used for architecture documentation. Source: https://learn.microsoft.com/azure/architecture/icons/</metadata>"
    )
    parts.append(f'<rect width="100%" height="100%" fill="{COLORS["background"]}"/>')
    for kind in ("data", "auth", "deploy"):
        parts.append(
            f'<defs><marker id="arrow-{kind}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M 0 0 L 8 4 L 0 8 z" fill="{COLORS[kind]}"/></marker></defs>'
        )

    parts.append(line_text(58, 55, data["title"], size=30, weight=500))
    parts.append(
        line_text(
            58,
            86,
            f"観測: {data['observed_at']}  |  {data['disclaimer']}",
            size=14,
            color=COLORS["muted"],
        )
    )
    legend(parts)

    parts.append(line_text(50, 158, "利用・稼働情報", size=20, weight=500))
    parts.append(
        line_text(200, 158, "主経路を実線、認証を破線で表示", size=13, color=COLORS["muted"])
    )

    boundary(parts, 50, 180, 370, 600, "Local PC / Codex", "local + allowlist")
    node(parts, nodes, "codex_tasks", 80, 245, 310, 90, detail="local task state / wakeは別課題")
    node(parts, nodes, "collector", 80, 390, 310, 90, detail="allowlist済みsnapshot")
    node(
        parts,
        nodes,
        "publisher",
        80,
        535,
        310,
        100,
        detail="single PUT確認済 / snapshot期限切れ / 継続更新なし",
    )

    boundary(
        parts,
        470,
        180,
        820,
        600,
        "Azure 管理Resource Group",
        "Bicep: Core / Protected",
        dominant=True,
        theme="azure",
    )
    node(parts, nodes, "uami", 530, 255, 300, 90, detail="Appのidentity / RBAC境界")
    node(parts, nodes, "key_vault", 930, 255, 300, 90, detail="Appがsecret referenceで参照")
    node(
        parts,
        nodes,
        "container_app",
        580,
        430,
        330,
        105,
        detail="Protected apply成功 / Healthy / auth有効",
    )
    node(
        parts,
        nodes,
        "private_blob",
        960,
        430,
        270,
        105,
        detail="single PUT済み / ingest GET 403 / viewer GET未検証",
    )
    node(parts, nodes, "log_analytics", 530, 650, 300, 90, detail="runtime logs")
    node(parts, nodes, "budget", 930, 650, 300, 90, detail="Azure課金基盤の通知 / hard capではない")
    parts.append(
        line_text(
            1080,
            620,
            "BudgetはAzure課金基盤から通知（Logs直送ではない）",
            size=12,
            color=COLORS["muted"],
            anchor="middle",
        )
    )

    boundary(
        parts,
        1350,
        180,
        400,
        240,
        "Microsoft Entra tenant",
        "CLI + recovery journal",
        stack_meta=True,
        theme="entra",
    )
    node(parts, nodes, "entra", 1390, 255, 320, 105, detail="app / SP / role / token claims実証済")
    parts.append(
        line_text(
            1550, 390, "Bicep管理済みではない", size=12, color=COLORS["unverified"], anchor="middle"
        )
    )

    boundary(parts, 1350, 475, 400, 205, "User device", "browser")
    node(parts, nodes, "smartphone", 1390, 535, 320, 100, detail="Entra後のUI/API実表示は未確認")
    connector(parts, [(235, 335), (235, 390)], "data", "実行イベント", label_at=(315, 370))
    connector(parts, [(235, 480), (235, 535)], "data", "公開用に整形", label_at=(315, 515))
    connector(
        parts,
        [(390, 585), (490, 585), (490, 482), (580, 482)],
        "data",
        "稼働情報を送信",
        status="placed",
        label_at=(495, 560),
    )
    connector(parts, [(910, 482), (960, 482)], "data", "状態保存", label_at=(935, 470))
    connector(
        parts,
        [(1390, 585), (1320, 585), (1320, 575), (745, 575), (745, 535)],
        "data",
        "画面を表示",
        status="unverified",
        label_at=(1200, 568),
    )

    connector(
        parts,
        [(1550, 535), (1730, 535), (1730, 307), (1710, 307)],
        "auth",
        "ログイン",
        status="unverified",
        label_at=(1730, 465),
    )
    connector(
        parts,
        [(1390, 307), (1305, 307), (1305, 215), (880, 215), (880, 430)],
        "auth",
        "認証情報を検証",
        status="unverified",
        label_at=(1090, 215),
    )
    connector(parts, [(680, 430), (680, 345)], "auth", "管理ID", label_at=(620, 395))
    connector(parts, [(830, 300), (930, 300)], "auth", "参照権限", label_at=(880, 288))
    connector(
        parts,
        [(850, 430), (850, 380), (1080, 380), (1080, 345)],
        "auth",
        "秘密情報を参照",
        label_at=(965, 370),
    )
    connector(parts, [(680, 535), (680, 650)], "data", "ログ", label_at=(720, 605))

    parts.append(line_text(50, 840, "配備・構成管理", size=20, weight=500))
    parts.append(line_text(220, 840, "実行経路と分離した補助図", size=13, color=COLORS["muted"]))
    boundary(parts, 50, 865, 1060, 310, "GitHub", "Repo / Actions / GHCR", theme="github")
    node(parts, nodes, "repository", 85, 925, 280, 90, detail="source / review / CIの正本")
    node(parts, nodes, "actions", 415, 925, 280, 90, detail="image build / smoke / GHCR publish")
    node(parts, nodes, "ghcr", 745, 925, 280, 90, detail="digest固定・匿名pull成功")
    connector(parts, [(365, 970), (415, 970)], "deploy", "確認済み版", label_at=(390, 955))
    connector(parts, [(695, 970), (745, 970)], "deploy", "digest固定", label_at=(720, 955))

    parts.append(
        rect(
            1145,
            900,
            300,
            115,
            fill=COLORS["azure_panel"],
            stroke=COLORS["azure_accent"],
            radius=16,
        )
    )
    parts.append(line_text(1170, 932, "Azure 管理RG / ACA", size=17, weight=500))
    parts.append(line_text(1170, 960, "digest版を配備", size=13, color=COLORS["muted"]))
    parts.append(line_text(1170, 986, "Protected apply成功", size=13, color=COLORS["placed"]))
    connector(parts, [(1025, 970), (1145, 970)], "deploy", "固定digest参照", label_at=(1085, 955))

    parts.append(
        rect(85, 1050, 295, 85, fill=COLORS["background"], stroke=COLORS["border"], radius=12)
    )
    parts.append(line_text(105, 1080, "GitHub Terraform", size=15, weight=500))
    parts.append(
        line_text(
            105,
            1106,
            "local state 9件 / shared state・drift未実装",
            size=11,
            color=COLORS["unverified"],
        )
    )
    parts.append(
        rect(415, 1050, 295, 85, fill=COLORS["background"], stroke=COLORS["border"], radius=12)
    )
    parts.append(line_text(435, 1080, "Azure Bicep", size=15, weight=500))
    parts.append(
        line_text(
            435,
            1106,
            "PowerShell apply / Core・Protected",
            size=11,
            color=COLORS["muted"],
        )
    )
    connector(
        parts,
        [(562, 1050), (562, 1032), (1125, 1032), (1125, 995), (1145, 995)],
        "deploy",
        "Azure deploy",
        label_at=(860, 1024),
    )
    parts.append(
        rect(745, 1050, 295, 85, fill=COLORS["background"], stroke=COLORS["border"], radius=12)
    )
    parts.append(line_text(765, 1080, "Entra CLI + recovery journal", size=15, weight=500))
    parts.append(
        line_text(765, 1106, "app / SP / role / certificate", size=11, color=COLORS["muted"])
    )

    boundary(parts, 1490, 900, 260, 215, "World用RG", "", dashed=True, theme="azure")
    parts.append(
        line_text(1515, 955, "実配備未確認／今回管理RGと別", size=11, color=COLORS["muted"])
    )
    node(parts, nodes, "world_app", 1515, 980, 210, 80, detail="実配備未確認")

    parts.append(
        line_text(
            50,
            1235,
            "サービス記号: Microsoft公式Azure Architecture Icons / GitHub Mark。出典・利用条件は official-assets/PROVENANCE.md。",
            size=12,
            color=COLORS["muted"],
        )
    )
    parts.append("</svg>")
    official_count = sum(
        1 for node_id, filename in ICON_FILES.items() if (ICON_DIR / filename).is_file()
    )
    return "\n".join(parts), official_count


if __name__ == "__main__":
    svg, official_count = generate()
    OUTPUT_PATH.write_text(svg, encoding="utf-8", newline="\n")
    print(
        json.dumps({"svg": OUTPUT_PATH.name, "official_icons": official_count}, ensure_ascii=False)
    )
