from html import escape
from io import BytesIO
import textwrap

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.copilot import render_fact


def report_html(run, language="en"):
    heading = "बाढी स्थिति प्रतिवेदन" if language == "ne" else "Flood situation report"
    labels = ["Flood extent", "Infrastructure exposure", "Road access", "Data provenance", "Limitations"] if language == "en" else ["डुबान क्षेत्र", "पूर्वाधार", "सडक सम्पर्क", "तथ्याङ्कका स्रोत", "सीमाहरू"]
    blocks = "".join(f"<section><h2>{escape(label)}</h2><p>{escape(render_fact(run, intent, language).split(chr(10)+chr(10))[-1]).replace(chr(10), '<br>')}</p></section>" for label, intent in zip(labels, ["extent", "damage", "access", "sources", "limitations"]))
    demo_notice = ""
    if run["mode"] == "demo":
        demo_notice = '<p style="color:#a25315">' + ("कृत्रिम प्रदर्शन तथ्याङ्क — वास्तविक घटनाको नतिजा होइन।" if language == "ne" else "SYNTHETIC DEMO — not observations or results for the actual event.") + "</p>"
    attributions = "<br>".join(escape(t) for t in run["attributions"])
    return f"""<!doctype html><html lang="{language}"><meta charset="utf-8"><title>{heading}</title>
<style>body{{font:13px/1.5 Arial,sans-serif;max-width:760px;margin:35px auto;color:#173b36;padding:20px}}h1{{font-size:30px;margin-bottom:0}}h2{{font-size:14px;margin:16px 0 3px}}p{{margin:3px 0}}small{{font-size:10px}}header{{border-bottom:3px solid #23775e;padding-bottom:16px}}button{{padding:10px 16px;background:#23775e;color:white;border:0;cursor:pointer}}@page{{size:A4;margin:14mm}}@media print{{body{{margin:0;padding:0;font-size:10pt}}button{{display:none}}section{{break-inside:avoid}}}}</style>
<button onclick="window.print()">Print / Save PDF</button><header><p>FLOOD LENS · {escape(run['mode'].upper())}</p><h1>{heading}</h1><p>{escape(run['name'])} · {escape(run['flood_date'])}</p><small>Run {escape(run['id'])} · {escape(run['created_at'])}</small>{demo_notice}</header>{blocks}<hr><small>{attributions}</small></html>"""


def report_pdf(run):
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Flood Lens situation report — {run['name']}")
    y = height - 45
    pdf.setFillColor(HexColor("#23775e"))
    pdf.setFont("Helvetica-Bold", 10); pdf.drawString(42, y, "FLOOD LENS / SITUATION REPORT")
    y -= 30
    pdf.setFillColor(HexColor("#163b35"))
    pdf.setFont("Helvetica-Bold", 20); pdf.drawString(42, y, run["name"][:45])
    y -= 21
    pdf.setFont("Helvetica", 10); pdf.drawString(42, y, f"Event date: {run['flood_date']} | Mode: {run['mode'].upper()}")
    y -= 20
    if run["mode"] == "demo":
        pdf.setFillColor(HexColor("#a25315")); pdf.drawString(42, y, "SYNTHETIC DEMO: not observations or results for the actual event."); y -= 20
    for label, intent in zip(["Flood extent", "Infrastructure exposure", "Road access", "Data provenance", "Limitations"], ["extent", "damage", "access", "sources", "limitations"]):
        pdf.setFillColor(HexColor("#23775e")); pdf.setFont("Helvetica-Bold", 11); pdf.drawString(42, y, label); y -= 16
        pdf.setFillColor(HexColor("#243832")); pdf.setFont("Helvetica", 9)
        content = render_fact(run, intent).split("\n\n")[-1]
        # Long lists remain available in HTML and GeoJSON; the PDF stays one page.
        if intent == "access" and len(content) > 500:
            content = content[:400].rsplit(" ", 1)[0] + ". Full settlement list: see exported GeoJSON."
        content = content.replace("²", "2").replace("—", "-")
        for line in textwrap.wrap(content, 99):
            pdf.drawString(42, y, line); y -= 12
        y -= 13
    y = min(y, 145)
    pdf.setFont("Helvetica", 7)
    for attribution in run["attributions"]:
        for line in textwrap.wrap(attribution, 119):
            pdf.drawString(42, y, line); y -= 10
    pdf.drawString(42, 28, f"Run {run['id']} | Generated {run['created_at']} | Educational prototype")
    pdf.showPage(); pdf.save()
    return stream.getvalue()

