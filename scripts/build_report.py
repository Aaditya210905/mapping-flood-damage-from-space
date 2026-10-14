"""Render the current Copilot submission report into a six-page PDF."""
import argparse
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=root / 'docs/technical-report.md')
parser.add_argument('--output', type=Path, default=root / 'output/pdf/copilot-technical-report.pdf')
args = parser.parse_args()
source = args.source.read_text(encoding="utf-8")
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
    canvas.drawString(42,27,"FLOODLENS AI / SITUATION-REPORT COPILOT / EDUCATIONAL PROTOTYPE")
    canvas.drawRightString(A4[0]-42,27,f"{document.page} / 6")

args.output.parent.mkdir(parents=True, exist_ok=True)
document = SimpleDocTemplate(str(args.output),pagesize=A4,topMargin=46,bottomMargin=47,leftMargin=42,rightMargin=42,
                             title="FloodLens AI: Situation-report Copilot",author="FloodLens contributors")
document.build(story,onFirstPage=footer,onLaterPages=footer)
print(args.output)
