#!/usr/bin/env python3
"""Render pack markdown files to branded FinLedger PDFs."""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

NAVY = colors.HexColor("#0B1F3A")
GOLD = colors.HexColor("#C4A35A")
ACCENT = colors.HexColor("#1B4F72")
INK = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#5A6570")
RULE = colors.HexColor("#D6D1C7")
ROW = colors.HexColor("#F7F4EE")
CODE_BG = colors.HexColor("#F4F1EA")
WHITE = colors.white

ROOT = Path("/home/workdir/artifacts/finledger_team_packs")


def styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="Kicker", fontName="Times-Bold", fontSize=8, textColor=GOLD, leading=11, spaceAfter=2))
    s.add(ParagraphStyle(name="CoverTitle", fontName="Times-Bold", fontSize=20, textColor=NAVY, leading=24, spaceAfter=8))
    s.add(ParagraphStyle(name="H1", fontName="Times-Bold", fontSize=14, textColor=NAVY, leading=18, spaceBefore=12, spaceAfter=6))
    s.add(ParagraphStyle(name="H2", fontName="Times-Bold", fontSize=12, textColor=ACCENT, leading=15, spaceBefore=9, spaceAfter=4))
    s.add(ParagraphStyle(name="Body", fontName="Times-Roman", fontSize=9.5, textColor=INK, leading=13, spaceAfter=5))
    s.add(ParagraphStyle(name="BulletBody", fontName="Times-Roman", fontSize=9.5, textColor=INK, leading=13))
    s.add(ParagraphStyle(name="CodeBlock", fontName="Courier", fontSize=7.4, textColor=INK, leading=10, backColor=CODE_BG))
    s.add(ParagraphStyle(name="Cell", fontName="Times-Roman", fontSize=7.6, textColor=INK, leading=10))
    s.add(ParagraphStyle(name="CellHead", fontName="Times-Bold", fontSize=7.6, textColor=WHITE, leading=10))
    s.add(ParagraphStyle(name="Footer", fontName="Times-Roman", fontSize=7.5, textColor=MUTED, leading=9))
    s.add(ParagraphStyle(name="FooterRight", fontName="Times-Roman", fontSize=7.5, textColor=MUTED, leading=9, alignment=TA_RIGHT))
    return s


S = styles()


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return text


def parse_md(md: str):
    lines = md.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", "\n".join(buf)))
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*-+", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                raw = lines[i].strip()
                if re.match(r"^\|?\s*-+", raw):
                    i += 1
                    continue
                cells = [c.strip() for c in raw.strip("|").split("|")]
                rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue
        if line.startswith("# "):
            blocks.append(("h1", line[2:].strip()))
            i += 1
            continue
        if line.startswith("## "):
            blocks.append(("h2", line[3:].strip()))
            i += 1
            continue
        if line.startswith("### "):
            blocks.append(("h2", line[4:].strip()))
            i += 1
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:].strip())
                i += 1
            blocks.append(("ul", items))
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", "-", "```")):
            para.append(lines[i])
            i += 1
        blocks.append(("p", " ".join(x.strip() for x in para)))
    return blocks


def table_flow(rows):
    if not rows:
        return Spacer(1, 1)
    ncols = max(len(r) for r in rows)
    norm = [r + [""] * (ncols - len(r)) for r in rows]
    data = []
    for ri, r in enumerate(norm):
        style = S["CellHead"] if ri == 0 else S["Cell"]
        data.append([Paragraph(inline(c), style) for c in r])
    avail = 178 * mm
    # weight first col a bit smaller if many cols
    if ncols == 2:
        widths = [avail * 0.42, avail * 0.58]
    elif ncols == 3:
        widths = [avail * 0.28, avail * 0.36, avail * 0.36]
    elif ncols == 4:
        widths = [avail * 0.16, avail * 0.28, avail * 0.28, avail * 0.28]
    else:
        widths = [avail / ncols] * ncols
    t = Table(data, colWidths=widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
    ]
    for ri in range(1, len(norm)):
        if ri % 2 == 0:
            cmds.append(("BACKGROUND", (0, ri), (-1, ri), ROW))
    t.setStyle(TableStyle(cmds))
    return t


def code_flow(text):
    # Preformatted does not wrap well; split long lines
    wrapped = []
    for line in text.splitlines() or [""]:
        while len(line) > 98:
            wrapped.append(line[:98])
            line = "    " + line[98:]
        wrapped.append(line)
    body = "\n".join(wrapped)
    inner = Table([[Preformatted(body, S["CodeBlock"])]], colWidths=[178 * mm])
    inner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("BOX", (0, 0), (-1, -1), 0.3, RULE),
            ]
        )
    )
    return inner


def story_from_md(md: str):
    blocks = parse_md(md)
    story = []
    if blocks and blocks[0][0] == "h1":
        title = blocks[0][1]
        story.append(Paragraph("FINLEDGER  ·  ACCOUNTS PAYABLE AUTOMATION", S["Kicker"]))
        story.append(Paragraph(inline(title), S["CoverTitle"]))
        story.append(HRFlowable(width="100%", thickness=1.2, color=GOLD, spaceAfter=8))
        blocks = blocks[1:]
    for kind, data in blocks:
        if kind == "h1":
            story.append(Paragraph(inline(data), S["H1"]))
            story.append(HRFlowable(width="100%", thickness=0.4, color=GOLD, spaceAfter=4))
        elif kind == "h2":
            story.append(Paragraph(inline(data), S["H2"]))
        elif kind == "p":
            story.append(Paragraph(inline(data), S["Body"]))
        elif kind == "ul":
            items = [ListItem(Paragraph(inline(x), S["BulletBody"]), leftIndent=8, bulletColor=NAVY) for x in data]
            story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=14, spaceAfter=6))
        elif kind == "table":
            story.append(Spacer(1, 2))
            story.append(table_flow(data))
            story.append(Spacer(1, 6))
        elif kind == "code":
            story.append(Spacer(1, 2))
            story.append(code_flow(data))
            story.append(Spacer(1, 6))
    return story


def add_page_bits(canvas, doc, subtitle: str):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 12 * mm, w, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Times-Bold", 8)
    canvas.drawString(16 * mm, h - 7.6 * mm, "FINLEDGER  ·  AP AUTOMATION")
    canvas.setFont("Times-Roman", 8)
    canvas.drawRightString(w - 16 * mm, h - 7.6 * mm, subtitle)
    canvas.setFillColor(GOLD)
    canvas.rect(0, h - 12.6 * mm, w, 0.6 * mm, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, w, 10 * mm, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0, 10 * mm, w, 0.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Times-Roman", 7.5)
    canvas.drawString(16 * mm, 4.2 * mm, "Confidential  ·  India AP  ·  Tally-first  ·  Extraction never posts")
    canvas.drawRightString(w - 16 * mm, 4.2 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render(md_path: Path, pdf_path: Path, subtitle: str):
    md = md_path.read_text()
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=subtitle,
        author="FinLedger",
    )
    doc.build(
        story_from_md(md),
        onFirstPage=lambda c, d: add_page_bits(c, d, subtitle),
        onLaterPages=lambda c, d: add_page_bits(c, d, subtitle),
    )
    print("wrote", pdf_path)


JOBS = [
    (ROOT / "person1_ai_rag/ARCHITECTURE.md", ROOT / "person1_ai_rag/Person1_AI_RAG.pdf", "Person 1  ·  AI / RAG / Tokens"),
    (ROOT / "person2_platform/ARCHITECTURE.md", ROOT / "person2_platform/Person2_Platform.pdf", "Person 2  ·  Platform / Intake"),
    (ROOT / "person3_control_ui/ARCHITECTURE.md", ROOT / "person3_control_ui/Person3_Control_UI.pdf", "Person 3  ·  Control + Review UI"),
    (ROOT / "person4_tally/ARCHITECTURE.md", ROOT / "person4_tally/Person4_Tally_Connector.pdf", "Person 4  ·  Tally Connector"),
    (ROOT / "shared/00_SHARED_CONTRACTS.md", ROOT / "shared/Shared_Contracts.pdf", "Shared contracts"),
]


if __name__ == "__main__":
    for md, pdf, sub in JOBS:
        render(md, pdf, sub)
