"""Render the editable technical report into an explicit six-page PDF."""
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


root = Path(__file__).resolve().parent.parent
source = (root / "docs/technical-report.md").read_text(encoding="utf-8")
sections = source.split("<!-- page -->")
assert len(sections) == 6
ink, green = colors.HexColor("#183e35"), colors.HexColor("#23775d")
styles = {
    "title": ParagraphStyle("Title",fontName="Helvetica-Bold",fontSize=22,leading=27,textColor=ink,spaceAfter=18),
    "heading": ParagraphStyle("Heading",fontName="Helvetica-Bold",fontSize=15,leading=20,textColor=green,spaceAfter=14),
    "body": ParagraphStyle("Body",fontName="Helvetica",fontSize=10,leading=15,textColor=ink,spaceAfter=11,alignment=TA_LEFT),
}
story = []
for index, section in enumerate(sections):
    if index: story.append(PageBreak())
    for block in section.strip().split("\n\n"):
        if block.startswith("# "): kind, text = "title", block[2:]
        elif block.startswith("## "): kind, text = "heading", block[3:]
        else: kind, text = "body", block
        text = text.replace("−", "-").replace("×", "x").replace("±", "+/-")
        story.append(Paragraph(escape(text),styles[kind]))

def footer(canvas, document):
    canvas.setFillColor(green); canvas.setFont("Helvetica",8)
    canvas.drawString(42,27,"FLOOD LENS / EDUCATIONAL PROTOTYPE / ACTUAL-DATA BASELINE")
    canvas.drawRightString(A4[0]-42,27,f"{document.page} / 6")

document = SimpleDocTemplate(str(root / "docs/technical-report.pdf"),pagesize=A4,topMargin=46,bottomMargin=47,leftMargin=42,rightMargin=42,
                             title="Flood Lens technical report",author="Flood Lens contributors")
document.build(story,onFirstPage=footer,onLaterPages=footer)
print(root / "docs/technical-report.pdf")
