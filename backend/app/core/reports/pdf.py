"""PDF-Ausgabe der Reports mit ReportLab (Backlog #84). ReportLab ist reines
Python -- die Umgebungen aktualisieren sich per git + pip, ohne das Container-
Image neu zu bauen; Bibliotheken mit Systempaketen (WeasyPrint) kaemen dort
nicht an.

Aufbau: Kopf mit Logo, Produktname und Report-Titel auf jeder Seite;
Untertitel (Zeitraum/Auswahl); Kennzahlen als Ampel-Kacheln; Vergleich zum
Vorzeitraum; je Abschnitt eine Tabelle (bricht ueber Seiten um, Kopfzeile
wiederholt sich); Fuss mit Erstellzeit/-von, "Seite x von y" und der
Inhalts-Pruefsumme. A4 quer, damit breite Tabellen lesbar bleiben."""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.reports.base import ReportContent

PRODUCT = "AU Storage Manager for Hyper-V"
LOGO = Path(__file__).resolve().parents[2] / "static" / "report-logo.png"
PAGE = landscape(A4)
MARGIN = 14 * mm

LEVEL_FILL = {"ok": colors.HexColor("#e6f4ea"), "warn": colors.HexColor("#fff4d6"), "bad": colors.HexColor("#fde2e1"), "neutral": colors.HexColor("#eef1f5")}
LEVEL_TEXT = {"ok": colors.HexColor("#1e7b34"), "warn": colors.HexColor("#8a5a00"), "bad": colors.HexColor("#b42318"), "neutral": colors.HexColor("#344054")}
HEADER_FILL = colors.HexColor("#1f3a5f")
ZEBRA = colors.HexColor("#f7f8fa")
GRID = colors.HexColor("#d0d5dd")

_body = ParagraphStyle("body", fontName="Helvetica", fontSize=8, leading=10, alignment=TA_LEFT)
_cell = ParagraphStyle("cell", parent=_body, fontSize=7.5, leading=9)
_head = ParagraphStyle("head", parent=_cell, fontName="Helvetica-Bold", textColor=colors.white)
_title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16, leading=20)
_subtitle = ParagraphStyle("subtitle", parent=_body, fontSize=9, leading=12, textColor=colors.HexColor("#475467"))
# keepWithNext: Ueberschrift nie allein am Seitenende
_section = ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=6, spaceAfter=4, keepWithNext=1)
_kpi_label = ParagraphStyle("kpilabel", parent=_body, fontSize=7.5, textColor=colors.HexColor("#475467"))


def _p(text: str, style: ParagraphStyle = _cell) -> Paragraph:
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def _numbered_canvas(meta: dict):
    """Canvas, der erst am Ende alle Seiten zeichnet -- fuer "Seite x von y"."""

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total: int) -> None:
            width, height = PAGE
            if LOGO.exists():
                self.drawImage(str(LOGO), MARGIN, height - MARGIN - 8 * mm, height=8 * mm, width=28 * mm,
                               preserveAspectRatio=True, anchor="w", mask="auto")
            self.setFont("Helvetica-Bold", 9)
            self.setFillColor(colors.HexColor("#344054"))
            self.drawRightString(width - MARGIN, height - MARGIN - 3 * mm, PRODUCT)
            self.setFont("Helvetica", 8)
            self.drawRightString(width - MARGIN, height - MARGIN - 7 * mm, meta["title"])
            self.setStrokeColor(GRID)
            self.line(MARGIN, height - MARGIN - 10 * mm, width - MARGIN, height - MARGIN - 10 * mm)
            self.line(MARGIN, MARGIN + 6 * mm, width - MARGIN, MARGIN + 6 * mm)
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.HexColor("#667085"))
            self.drawString(MARGIN, MARGIN + 2 * mm, f"Erstellt {meta['created']} von {meta['created_by']}")
            self.drawCentredString(width / 2, MARGIN + 2 * mm, f"Inhalts-Prüfsumme (SHA-256): {meta['content_sha256']}")
            self.drawRightString(width - MARGIN, MARGIN + 2 * mm, f"Seite {self._pageNumber} von {total}")

    return NumberedCanvas


def _kpi_table(content: ReportContent, width: float):
    """Kennzahlen als Kacheln nebeneinander, Hintergrund nach Ampel."""
    if not content.kpis:
        return Spacer(1, 1)
    cells = []
    for kpi in content.kpis:
        value_style = ParagraphStyle(
            "kpivalue", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=LEVEL_TEXT.get(kpi.level, LEVEL_TEXT["neutral"]),
        )
        cells.append(Table([[_p(kpi.value, value_style)], [_p(kpi.label, _kpi_label)]], style=[("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    row = Table([cells], colWidths=[width / len(cells)] * len(cells))
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, kpi in enumerate(content.kpis):
        style.append(("BACKGROUND", (i, 0), (i, 0), LEVEL_FILL.get(kpi.level, LEVEL_FILL["neutral"])))
        style.append(("LINEAFTER", (i, 0), (i, 0), 3, colors.white))
    row.setStyle(TableStyle(style))
    return row


def _section_table(section, width: float):
    flow = [_p(section.title, _section)]
    if not section.rows:
        flow.append(_p(section.empty_text, _body))
        return flow
    weights = section.widths or [1.0] * len(section.columns)
    total = sum(weights)
    col_widths = [width * w / total for w in weights]
    data = [[_p(c, _head) for c in section.columns]] + [[_p(v) for v in row] for row in section.rows]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_FILL),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
        level = (section.row_levels or [None] * len(section.rows))[i - 1]
        if level in ("warn", "bad", "ok"):
            col = section.status_col
            style.append(("BACKGROUND", (col, i), (col, i), LEVEL_FILL[level]))
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style))
    flow.append(table)
    if section.note:
        flow.append(Spacer(1, 2 * mm))
        flow.append(_p(section.note, _subtitle))
    return flow


def render_pdf(content: ReportContent, *, created: str, created_by: str, content_sha256: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + 13 * mm, bottomMargin=MARGIN + 10 * mm,
        title=f"{content.title} -- {PRODUCT}", author=PRODUCT,
    )
    width = PAGE[0] - 2 * MARGIN
    story = [_p(content.title, _title), _p(content.subtitle, _subtitle), Spacer(1, 4 * mm), _kpi_table(content, width)]
    if content.comparison:
        story += [Spacer(1, 2 * mm), _p(content.comparison, _subtitle)]
    story.append(Spacer(1, 4 * mm))
    for section in content.sections:
        story += _section_table(section, width)
        story.append(Spacer(1, 5 * mm))
    meta = {"title": content.title, "created": created, "created_by": created_by, "content_sha256": content_sha256}
    doc.build(story, canvasmaker=_numbered_canvas(meta))
    return buffer.getvalue()
