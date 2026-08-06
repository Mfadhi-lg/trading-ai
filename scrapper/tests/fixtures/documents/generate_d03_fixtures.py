"""Regenerate the small, deterministic D03 binary fixtures."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent


def _canonical_zip(blob: bytes, *, normalize_core_time: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as destination:
        for name in sorted(source.namelist()):
            value = source.read(name)
            if normalize_core_time and name == "docProps/core.xml":
                value = re.sub(
                    rb">\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z<",
                    b">2026-08-05T14:30:00Z<",
                    value,
                )
            info = zipfile.ZipInfo(name, date_time=(2026, 8, 5, 14, 30, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            destination.writestr(info, value)
    return output.getvalue()


def _source_image(text: str, *, width: int = 1000, height: int = 500) -> Image.Image:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, width - 20, height - 20), outline="navy", width=5)
    draw.text((70, height // 2 - 20), text, fill="black", font_size=42)
    return image


def text_pdf() -> None:
    logo = _source_image("Revenue chart reference", width=600, height=160)
    logo_path = ROOT / "d03_reference.png"
    logo.save(logo_path, format="PNG", optimize=False)
    target = ROOT / "d03_text.pdf"
    document = canvas.Canvas(str(target), pagesize=A4, invariant=1, pageCompression=0)
    document.setTitle("D03 Native PDF Fixture")
    document.setFont("Helvetica-Bold", 18)
    document.drawString(72, 780, "Q1 FY2026 Results")
    document.setFont("Helvetica", 11)
    document.drawString(72, 752, "Native text remains the selected source.")
    x_values = (72, 240, 390)
    y_values = (700, 670, 640)
    rows = (("Metric", "Q1 FY26", "Q1 FY25"), ("Revenue", "1,234", "1,050"))
    for y in (715, 685, 655, 625):
        document.line(65, y, 510, y)
    for x in (65, 225, 375, 510):
        document.line(x, 625, x, 715)
    for row, y in zip(rows, y_values):
        for value, x in zip(row, x_values):
            document.drawString(x, y, value)
    document.drawImage(str(logo_path), 72, 430, width=360, height=96)
    document.showPage()
    document.setFont("Helvetica-Bold", 16)
    document.drawString(72, 780, "Operational highlights")
    document.setFont("Helvetica", 11)
    document.drawString(72, 750, "Second page verifies source page order.")
    document.save()


def scanned_pdf() -> None:
    image = _source_image("SCANNED EBITDA MARGIN 24 PERCENT", width=1240, height=1754)
    image_path = ROOT / "d03_scanned_page.png"
    image.save(image_path, format="PNG", optimize=False)
    target = ROOT / "d03_scanned.pdf"
    document = canvas.Canvas(str(target), pagesize=A4, invariant=1, pageCompression=0)
    document.drawImage(str(image_path), 0, 0, width=A4[0], height=A4[1])
    document.showPage()
    document.save()


def credit_rating_pdf() -> None:
    """Create a small native-text CARE-style rating rationale for D04."""

    target = ROOT / "d04_credit_rating.pdf"
    document = canvas.Canvas(str(target), pagesize=A4, invariant=1, pageCompression=0)
    document.setTitle("D04 Credit Rating Rationale Fixture")
    document.setAuthor("CARE Ratings Limited")
    document.setFont("Helvetica-Bold", 18)
    document.drawString(54, 792, "CARE Ratings Limited")
    document.setFont("Helvetica-Bold", 15)
    document.drawString(54, 764, "Rating Rationale")
    document.setFont("Helvetica", 10)
    document.drawString(54, 742, "Fixture Limited")
    document.drawString(54, 724, "Date: July 15, 2026")

    x_lines = (50, 245, 340, 455, 545)
    y_lines = (688, 658, 628, 598)
    for y in y_lines:
        document.line(x_lines[0], y, x_lines[-1], y)
    for x in x_lines:
        document.line(x, y_lines[-1], x, y_lines[0])
    headers = ("Facilities / Instrument", "Amount (Rs. crore)", "Rating", "Rating Action")
    row = ("Long-term bank facilities", "150.00", "CARE A+; Stable", "Reaffirmed")
    x_text = (56, 251, 346, 461)
    for value, x in zip(headers, x_text):
        document.drawString(x, 670, value)
    for value, x in zip(row, x_text):
        document.drawString(x, 640, value)
    document.setFont("Helvetica", 9)
    document.drawString(54, 566, "The rating assigned to the bank facilities continues to carry a Stable outlook.")
    document.showPage()

    document.setFont("Helvetica-Bold", 13)
    document.drawString(54, 792, "Rating action and instrument details")
    document.setFont("Helvetica", 10)
    x_lines = (50, 245, 340, 455, 545)
    y_lines = (748, 718, 688, 658, 628)
    for y in y_lines:
        document.line(x_lines[0], y, x_lines[-1], y)
    for x in x_lines:
        document.line(x, y_lines[-1], x, y_lines[0])
    headers = ("Facilities / Instrument", "Amount (Rs. crore)", "Rating", "Rating Action")
    rows = (
        ("Short-term bank facilities", "50.00", "CARE A1+", "Reaffirmed"),
        ("Non-convertible debentures", "100.00", "CARE A+; Stable", "Upgraded"),
    )
    x_text = (56, 251, 346, 461)
    for value, x in zip(headers, x_text):
        document.drawString(x, 730, value)
    for values, y in zip(rows, (700, 670)):
        for value, x in zip(values, x_text):
            document.drawString(x, y, value)
    document.drawString(54, 596, "Previous rating for the non-convertible debentures: CARE A; Stable.")
    document.save()


def official_transcript_pdf() -> None:
    """Create a native-text official call transcript with explicit boundaries."""

    target = ROOT / "d05_official_transcript.pdf"
    document = canvas.Canvas(str(target), pagesize=A4, invariant=1, pageCompression=0)
    document.setTitle("D05 Official Transcript Fixture")
    document.setAuthor("Fixture Limited")

    def page(lines: tuple[tuple[str, bool], ...]) -> None:
        y = 790
        for value, bold in lines:
            document.setFont("Helvetica-Bold" if bold else "Helvetica", 15 if bold else 11)
            document.drawString(54, y, value)
            y -= 30 if bold else 23
        document.showPage()

    page((
        ("Fixture Limited", True),
        ("Q1 FY2026 Earnings Conference Call Transcript", True),
        ("July 20, 2026", False),
        ("Management Participants", True),
        ("Asha Rao - Managing Director", False),
        ("Vikram Sen - Chief Financial Officer", False),
    ))
    page((
        ("Presentation", True),
        ("Moderator: Ladies and gentlemen, welcome to the Fixture Limited earnings call.", False),
        ("Asha Rao: Thank you. We delivered a strong start to the financial year.", False),
    ))
    page((
        ("Revenue grew eighteen percent and margins expanded during the quarter.", False),
        ("Moderator: We will now begin the question-and-answer session.", False),
    ))
    page((
        ("Question-and-Answer Session", True),
        ("Ravi Shah - Analyst: Could you discuss the demand outlook for the second half?", False),
        ("Asha Rao: We expect demand to remain resilient across our core markets.", False),
        ("End of Transcript", True),
    ))
    document.save()


def presentation() -> None:
    presentation = Presentation()
    fixed_time = datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc)
    presentation.core_properties.created = fixed_time
    presentation.core_properties.modified = fixed_time
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    title = slide.shapes.title
    title.text = "Q1 FY2026 Investor Presentation"
    body = slide.shapes.add_textbox(Inches(0.7), Inches(1.2), Inches(4.8), Inches(0.8))
    body.text_frame.text = "Revenue grew 18 percent year on year."
    table = slide.shapes.add_table(2, 3, Inches(0.7), Inches(2.1), Inches(5.4), Inches(1.2)).table
    for row_index, values in enumerate((("Metric", "FY26", "FY25"), ("Revenue", "1234", "1050"))):
        for column_index, value in enumerate(values):
            table.cell(row_index, column_index).text = value
    chart_data = ChartData()
    chart_data.categories = ["FY25", "FY26"]
    chart_data.add_series("Revenue", (1050, 1234))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(6.4), Inches(1.4), Inches(2.7), Inches(2.2), chart_data,
    )
    slide.notes_slide.notes_text_frame.text = "Speaker note: native slide structure fixture."

    image = _source_image("IMAGE ONLY GUIDANCE RAISED", width=1100, height=620)
    image_path = ROOT / "d03_slide_scan.png"
    image.save(image_path, format="PNG", optimize=False)
    scanned = presentation.slides.add_slide(presentation.slide_layouts[6])
    scanned.shapes.add_picture(str(image_path), Inches(0.5), Inches(0.7), width=Inches(9.0))
    raw = io.BytesIO()
    presentation.save(raw)
    raw.seek(0)
    raw.seek(0)
    outer = io.BytesIO()
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(
        outer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as destination:
        for name in sorted(source.namelist()):
            value = source.read(name)
            if name.endswith(".xlsx"):
                value = _canonical_zip(value, normalize_core_time=True)
            info = zipfile.ZipInfo(name, date_time=(2026, 8, 5, 14, 30, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            destination.writestr(info, value)
    (ROOT / "d03_presentation.pptx").write_bytes(outer.getvalue())


if __name__ == "__main__":
    text_pdf()
    scanned_pdf()
    presentation()
    credit_rating_pdf()
    official_transcript_pdf()
