import logging
log = logging.getLogger(__name__)
import os
import re
import datetime
import zipfile
import tempfile
from typing import Any, Dict, List, Optional, Tuple


# ── Lightweight Markdown → docx rendering (subset) ──────────────────────────
# Renders the common markdown the LLM emits for report section bodies into
# docx paragraphs/runs. No new dependencies. Supports: ATX headings (###),
# bold (**..** / __..__), unordered lists (- / * / +), ordered lists (1. ),
# and paragraph splitting on blank lines. Unknown syntax falls through as
# plain text so the report never breaks on unexpected input.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_OLIST_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_MD_ULIST_RE = re.compile(r"^\s*[-*+]\s+(.*)$")


def _add_runs_with_bold(paragraph, text: str) -> None:
    """Add *text* to *paragraph*, turning **bold** / __bold__ into bold runs."""
    pos = 0
    for m in _MD_BOLD_RE.finditer(text):
        start, end = m.span()
        if start > pos:
            paragraph.add_run(text[pos:start])
        bold_text = m.group(1) if m.group(1) is not None else m.group(2)
        run = paragraph.add_run(bold_text)
        run.bold = True
        pos = end
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _render_markdown_to_docx(doc, content: str) -> None:
    """Render a markdown-style string into *doc* (python-docx Document)."""
    lines = content.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            i += 1
            continue
        m = _MD_HEADING_RE.match(line.strip())
        if m:
            level = min(len(m.group(1)) + 1, 9)  # ### → docx heading 2
            doc.add_heading(m.group(2).strip(), level=level)
            i += 1
            continue
        mo = _MD_OLIST_RE.match(line)
        if mo:
            p = doc.add_paragraph(style="List Number")
            _add_runs_with_bold(p, mo.group(2))
            i += 1
            continue
        mu = _MD_ULIST_RE.match(line)
        if mu:
            p = doc.add_paragraph(style="List Bullet")
            _add_runs_with_bold(p, mu.group(1))
            i += 1
            continue
        # Plain paragraph (may span consecutive non-blank, non-list lines)
        p = doc.add_paragraph()
        _add_runs_with_bold(p, line.strip())
        i += 1


def _title_slug(title: str) -> str:
    """Filesystem-safe slug for report title, used in bundled chart filenames."""
    slug = re.sub(r"[^\w\-.]+", "_", str(title or "report")).strip("_")
    return slug[:40] or "report"


def export_to_report(
    title: str,
    sections: List[Dict[str, Any]],
    filepath: str,
    chart_htmls: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    Generate a Word (.docx) report and optionally bundle charts as a ZIP.

    Parameters
    ----------
    title       : Report title shown at the top of the document.
    sections    : Ordered list of {heading: str, content: str | list[dict]}.
                  str content → paragraph; list[dict] content → table.
    filepath    : Absolute path for the .docx file (directory must exist).
    chart_htmls : Optional list of Plotly HTML strings from the session.
                  When provided, returns a .zip containing the .docx + chart HTML files.

    Returns
    -------
    (result_path, download_name)
      result_path   : absolute path of the file to serve (zip or docx)
      download_name : filename the browser will see
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
    except ImportError as exc:
        raise ImportError("python-docx 未安装，请运行: pip install python-docx") from exc

    doc = Document()

    # ── Title block ────────────────────────────────────────────────────
    doc.add_heading(title, level=0)
    ts_para = doc.add_paragraph(
        f"生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    ts_para.runs[0].font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    ts_para.runs[0].font.size = Pt(10)
    doc.add_paragraph()  # spacer

    # ── Sections ───────────────────────────────────────────────────────
    for section in sections:
        heading = section.get("heading", "")
        content = section.get("content", "")

        if heading:
            doc.add_heading(heading, level=1)

        if isinstance(content, str) and content.strip():
            # Split on blank lines to preserve paragraph breaks, then render
            # markdown subset (headings/bold/lists) per block.
            for block in re.split(r"\n\s*\n", content):
                if block.strip():
                    _render_markdown_to_docx(doc, block)

        elif isinstance(content, list) and content:
            cols = list(content[0].keys())
            tbl = doc.add_table(rows=1, cols=len(cols))
            tbl.style = "Table Grid"
            hdr_cells = tbl.rows[0].cells
            for i, col in enumerate(cols):
                hdr_cells[i].text = str(col)
                run = hdr_cells[i].paragraphs[0].runs[0]
                run.bold = True
            for row_data in content:
                row_cells = tbl.add_row().cells
                for i, col in enumerate(cols):
                    row_cells[i].text = str(row_data.get(col, ""))
            doc.add_paragraph()  # spacer after table

    # ── Chart reference section ─────────────────────────────────────────
    if chart_htmls:
        slug = _title_slug(title)
        doc.add_heading("图表附件", level=1)
        doc.add_paragraph(
            f"本报告共包含 {len(chart_htmls)} 张交互式图表，"
            "已随报告一同打包为 HTML 文件，请用浏览器打开查看。"
        )
        for i in range(len(chart_htmls)):
            doc.add_paragraph(f"• {slug}_chart_{i + 1:02d}.html")

    out_dir = os.path.dirname(filepath)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    doc.save(filepath)

    # ── Bundle into ZIP when charts are present ─────────────────────────
    if chart_htmls:
        slug = _title_slug(title)
        zip_path = filepath.replace(".docx", ".zip")
        zip_name = os.path.basename(zip_path)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(filepath, os.path.basename(filepath))
            for i, html in enumerate(chart_htmls):
                zf.writestr(f"{slug}_chart_{i + 1:02d}.html", html)
        return zip_path, zip_name

    doc_name = os.path.basename(filepath)
    return filepath, doc_name
