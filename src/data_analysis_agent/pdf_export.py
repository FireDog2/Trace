from __future__ import annotations

import html
import re
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


_INLINE = re.compile(
    r"`([^`]+)`|\*\*([^*]+)\*\*|(?<!\*)\*([^*]+)\*(?!\*)|\[([^\]]+)\]\(([^)]+)\)"
)
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST = re.compile(r"^\s*([-+*]|\d+[.)])\s+(.+)$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")

_INK = colors.HexColor("#17233C")
_MUTED = colors.HexColor("#60708B")
_PRIMARY = colors.HexColor("#3D63DD")
_BORDER = colors.HexColor("#D8E0EE")
_PANEL = colors.HexColor("#F5F7FC")
_CODE = colors.HexColor("#EEF2F9")


def render_markdown_pdf(
    markdown: str,
    *,
    title: str,
    subtitle: str = "Evidence-linked analysis report",
) -> bytes:
    if not markdown.strip():
        raise ValueError("PDF export requires non-empty Markdown")

    regular_font, bold_font = _register_fonts()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=19 * mm,
        leftMargin=19 * mm,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
        title="TRACE Analysis Report",
        author="TRACE Data Analysis Agent",
        subject=subtitle,
    )
    styles = _styles(regular_font, bold_font)
    story = [
        Paragraph("TRACE / ANALYSIS REPORT", styles["eyebrow"]),
        Spacer(1, 3 * mm),
        Paragraph(_inline_markup(title), styles["title"]),
        Spacer(1, 2.5 * mm),
        Paragraph(_inline_markup(subtitle), styles["subtitle"]),
        Spacer(1, 5 * mm),
        HRFlowable(width="100%", thickness=0.8, color=_BORDER),
        Spacer(1, 5 * mm),
        *_markdown_flowables(markdown, styles, regular_font, bold_font),
    ]

    def draw_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(19 * mm, 16 * mm, A4[0] - 19 * mm, 16 * mm)
        canvas.setFont(regular_font, 7.5)
        canvas.setFillColor(_MUTED)
        canvas.drawString(19 * mm, 10.5 * mm, "TRACE  /  EVIDENCE-FIRST ANALYSIS")
        canvas.drawRightString(A4[0] - 19 * mm, 10.5 * mm, f"PAGE {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()


def _register_fonts() -> tuple[str, str]:
    regular_name = "TraceSans"
    bold_name = "TraceSansBold"
    if regular_name in pdfmetrics.getRegisteredFontNames():
        return regular_name, bold_name

    candidates = (
        (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
        ),
        (
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ),
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    )
    for regular_path, bold_path in candidates:
        if not regular_path.exists():
            continue
        pdfmetrics.registerFont(TTFont(regular_name, regular_path))
        pdfmetrics.registerFont(
            TTFont(bold_name, bold_path if bold_path.exists() else regular_path)
        )
        pdfmetrics.registerFontFamily(
            regular_name,
            normal=regular_name,
            bold=bold_name,
            italic=regular_name,
            boldItalic=bold_name,
        )
        return regular_name, bold_name

    # Portable CJK fallback supplied by ReportLab's Asian font support.
    fallback = "STSong-Light"
    if fallback not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(fallback))
    return fallback, fallback


def _styles(regular_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "TraceEyebrow", parent=sample["Normal"], fontName=bold_font, fontSize=7.5,
            leading=9, textColor=_PRIMARY, tracking=1.1,
        ),
        "title": ParagraphStyle(
            "TraceTitle", parent=sample["Title"], fontName=bold_font, fontSize=23,
            leading=29, textColor=_INK, alignment=TA_LEFT, spaceAfter=0,
        ),
        "subtitle": ParagraphStyle(
            "TraceSubtitle", parent=sample["Normal"], fontName=regular_font, fontSize=9,
            leading=14, textColor=_MUTED,
        ),
        "body": ParagraphStyle(
            "TraceBody", parent=sample["BodyText"], fontName=regular_font, fontSize=9.5,
            leading=16, textColor=_INK, spaceAfter=3.2 * mm, wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "TraceH1", parent=sample["Heading1"], fontName=bold_font, fontSize=17,
            leading=23, textColor=_INK, spaceBefore=7 * mm, spaceAfter=3 * mm,
        ),
        "h2": ParagraphStyle(
            "TraceH2", parent=sample["Heading2"], fontName=bold_font, fontSize=13,
            leading=19, textColor=_INK, spaceBefore=6 * mm, spaceAfter=2.5 * mm,
            borderColor=_PRIMARY, borderWidth=0, borderPadding=0,
        ),
        "h3": ParagraphStyle(
            "TraceH3", parent=sample["Heading3"], fontName=bold_font, fontSize=10.5,
            leading=16, textColor=_PRIMARY, spaceBefore=4.5 * mm, spaceAfter=2 * mm,
        ),
        "quote": ParagraphStyle(
            "TraceQuote", parent=sample["BodyText"], fontName=regular_font, fontSize=9,
            leading=15, textColor=_MUTED, leftIndent=6 * mm, rightIndent=3 * mm,
            borderColor=_PRIMARY, borderWidth=1.5, borderPadding=(2, 7, 2, 8),
            backColor=_PANEL, spaceBefore=2 * mm, spaceAfter=4 * mm, wordWrap="CJK",
        ),
        "code": ParagraphStyle(
            "TraceCode", parent=sample["Code"], fontName=regular_font, fontSize=7.8,
            leading=12, textColor=_INK, leftIndent=4 * mm, rightIndent=4 * mm,
            borderColor=_BORDER, borderWidth=0.5, borderPadding=7, backColor=_CODE,
            spaceBefore=2 * mm, spaceAfter=4 * mm, wordWrap="CJK",
        ),
        "list": ParagraphStyle(
            "TraceList", parent=sample["BodyText"], fontName=regular_font, fontSize=9.2,
            leading=15, textColor=_INK, wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "TraceTable", parent=sample["BodyText"], fontName=regular_font, fontSize=7.8,
            leading=11, textColor=_INK, wordWrap="CJK",
        ),
        "table_header": ParagraphStyle(
            "TraceTableHeader", parent=sample["BodyText"], fontName=bold_font, fontSize=7.8,
            leading=11, textColor=_INK, wordWrap="CJK",
        ),
        "page_break": ParagraphStyle("TracePageBreak", parent=sample["Normal"]),
    }


def _markdown_flowables(
    markdown: str,
    styles: dict[str, ParagraphStyle],
    regular_font: str,
    bold_font: str,
) -> list:
    del regular_font, bold_font
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped in {"---", "***", "___"}:
            output.extend((Spacer(1, 2 * mm), HRFlowable(width="100%", thickness=0.6, color=_BORDER), Spacer(1, 3 * mm)))
            index += 1
            continue
        if stripped == "<!-- pagebreak -->":
            output.append(PageBreak())
            index += 1
            continue
        if stripped.startswith("```"):
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1 if index < len(lines) else 0
            output.append(Preformatted("\n".join(code), styles["code"], maxLineLength=105))
            continue
        heading = _HEADING.match(stripped)
        if heading:
            if len(heading.group(1)) == 1 and not output:
                # The document cover already carries the report title.
                index += 1
                continue
            level = min(len(heading.group(1)), 3)
            output.append(Paragraph(_inline_markup(heading.group(2)), styles[f"h{level}"]))
            index += 1
            continue
        if index + 1 < len(lines) and "|" in stripped and _TABLE_RULE.match(lines[index + 1]):
            rows = [_table_cells(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_table_cells(lines[index]))
                index += 1
            output.append(_markdown_table(rows, styles))
            output.append(Spacer(1, 4 * mm))
            continue
        list_match = _LIST.match(line)
        if list_match:
            ordered = list_match.group(1)[0].isdigit()
            items: list[ListItem] = []
            while index < len(lines):
                item_match = _LIST.match(lines[index])
                if not item_match or item_match.group(1)[0].isdigit() != ordered:
                    break
                item = re.sub(r"^\[[ xX]\]\s*", lambda m: "☑ " if "x" in m.group(0).lower() else "☐ ", item_match.group(2))
                items.append(ListItem(Paragraph(_inline_markup(item), styles["list"]), leftIndent=3 * mm))
                index += 1
            output.append(ListFlowable(
                items,
                bulletType="1" if ordered else "bullet",
                start="1" if ordered else "•",
                leftIndent=6 * mm,
                bulletFontName=styles["list"].fontName,
                bulletFontSize=8,
                spaceAfter=3.5 * mm,
            ))
            continue
        if stripped.startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip().removeprefix(">").lstrip())
                index += 1
            output.append(Paragraph(_inline_markup("\n".join(quote)), styles["quote"]))
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines) and lines[index].strip() and not _starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        output.append(Paragraph(_inline_markup("\n".join(paragraph)), styles["body"]))
    return output


def _starts_block(lines: list[str], index: int) -> bool:
    stripped = lines[index].strip()
    return bool(
        stripped.startswith(("```", ">", "<!-- pagebreak -->"))
        or _HEADING.match(stripped)
        or _LIST.match(lines[index])
        or stripped in {"---", "***", "___"}
        or (index + 1 < len(lines) and "|" in stripped and _TABLE_RULE.match(lines[index + 1]))
    )


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _markdown_table(rows: list[list[str]], styles: dict[str, ParagraphStyle]) -> Table:
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    data = [
        [Paragraph(_inline_markup(cell), styles["table_header" if row_index == 0 else "table"]) for cell in row]
        for row_index, row in enumerate(normalized)
    ]
    available_width = A4[0] - 38 * mm
    table = Table(data, colWidths=[available_width / width] * width, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _CODE),
        ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
        ("GRID", (0, 0), (-1, -1), 0.45, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _PANEL]),
    ]))
    return table


def _inline_markup(text: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in _INLINE.finditer(text):
        output.append(html.escape(text[cursor:match.start()]).replace("\n", "<br/>"))
        code, bold, italic, label, target = match.groups()
        if code is not None:
            output.append(f'<font color="#3D4C66"><b>{html.escape(code)}</b></font>')
        elif bold is not None:
            output.append(f"<b>{html.escape(bold)}</b>")
        elif italic is not None:
            output.append(f"<i>{html.escape(italic)}</i>")
        elif label is not None and target is not None:
            safe_label = html.escape(label)
            if target.startswith(("https://", "http://", "artifact://")):
                output.append(f'<link href="{html.escape(target, quote=True)}" color="#3D63DD">{safe_label}</link>')
            else:
                output.append(safe_label)
        cursor = match.end()
    output.append(html.escape(text[cursor:]).replace("\n", "<br/>"))
    return "".join(output)
