"""Build the ClaimPilot AWS architecture diagram from the official AWS Architecture Icons PPTX toolkit.

    python scripts/build_architecture_diagram.py <AWS-Architecture-Icons-Deck_For-Light-BG_*.pptx>

Writes docs/architecture/claimpilot-architecture.pptx (editable, official icons and group styles)
and docs/architecture/claimpilot-architecture.svg (same layout, for the README and the deck).
The toolkit itself is not redistributed; download it from https://aws.amazon.com/architecture/icons/.
"""
from __future__ import annotations

import base64
import copy
import io
import shutil
import subprocess
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/architecture"
EMU = 914400
INK, TEAL, GREY = "232F3E", "00A4A6", "545B64"
ICON = 0.6  # inches; AWS guidance is 48px icons on a 13.33 x 7.5 in slide

# (slide index in the toolkit, label text next to the icon)
ICONS = {
    "amplify": (92, "AWS Amplify"), "apigw": (131, "Amazon API Gateway"), "lambda": (63, "AWS Lambda"),
    "s3": (153, "Amazon Simple Storage Service (Amazon S3)"), "sqs": (37, "Amazon Simple Queue Service (Amazon SQS)"),
    "sns": (37, "Amazon Simple Notification Service (Amazon SNS)"), "dynamodb": (78, "Amazon DynamoDB"),
    "healthlake": (48, "AWS HealthLake"), "secrets": (140, "AWS Secrets Manager"), "cloudwatch": (102, "Amazon CloudWatch"),
    "users": (27, "Users"), "documents": (28, "Documents"), "email": (28, "Email"),
}

# ---------------------------------------------------------------- layout (inches)
C1, C2, C3, C4, LEFT = 2.65, 4.25, 5.85, 7.55, 0.8
RA, RB, RC, HLY, SNSY = 1.85, 4.4, 6.18, 3.2, 6.08
NODES = [  # key, icon, cx, cy, label, label position
    ("users", "users", LEFT, RA, "Claims processors\n(browser)", "below"),
    ("vendor", "documents", LEFT, RB, "Scanning vendor /\nclearinghouse\n(batch drop)", "below"),
    ("amplify", "amplify", C1, RA, "AWS Amplify\nReact UI", "below"),
    ("apigw", "apigw", C2, RA, "Amazon API\nGateway", "below"),
    ("api", "lambda", C3, RA, "AWS Lambda\nAPI function", "below"),
    ("s3", "s3", C1, RB, "Amazon S3\ninbox/  batches/", "below"),
    ("sqs", "sqs", C2, RB, "Amazon SQS\nintake queue", "below"),
    ("sup", "lambda", C3, RB, "AWS Lambda supervisor\nExtraction Agent\nResolution Agent", "below"),
    ("dlq", "sqs", C2, RC, "Amazon SQS\ndead-letter queue", "below"),
    ("secrets", "secrets", C3, RC, "AWS Secrets\nManager", "below"),
    ("cw", "cloudwatch", C1, RC, "Amazon\nCloudWatch Logs", "below"),
    ("ddb", "dynamodb", C4, RA, "Amazon DynamoDB\nintake + batch records,\naudit trail", "right"),
    ("hl", "healthlake", C4, HLY, "AWS HealthLake\nFHIR R4: Patient,\nCoverage, Claim", "right"),
    ("sns", "sns", C4, SNSY, "Amazon SNS", "below"),
    ("email", "email", 11.8, SNSY, "Processor & provider\nemail", "below"),
]
OPENAI = (10.5, 3.95, 2.7, 1.3)  # x, y, w, h: the OpenAI Platform, drawn as the focal point (no logo)
OPENAI_FILL = "0F766E"
OPENAI_LINES = ["OpenAI Platform", "Responses API · Agents SDK", "gpt-6-luna → gpt-6.1-sol", "Traces · Evals"]
# The two agents are Agents SDK objects running inside the supervisor Lambda (no separate agent runtime)
AGENTS_BOX = (C3 - 0.98, RB - 0.72, 1.96, 1.86)
GENERIC = "7D8998"
CLOUD = (1.62, 0.4, 8.43, 6.72)
REGION = (1.8, 0.87, 8.1, 6.1)
ARROWS = [  # from, to, (optional) explicit points, dashed
    ("users", "amplify", None, False), ("amplify", "apigw", None, False), ("apigw", "api", None, False),
    ("api", "ddb", None, False), ("users", "s3", ((1.05, RA + 1.0), (2.38, RB - 0.27)), False),
    ("vendor", "s3", None, False), ("s3", "sqs", None, False), ("sqs", "sup", None, False),
    ("sqs", "dlq", ((C2, RB + 0.85), (C2, RC - 0.32)), True),
    ("secrets", "sup", ((C3, RC - 0.32), (C3, RB + 1.1)), True),
    ("sup", "hl", ((C3 + 0.32, RB - 0.12), (C4 - 0.32, HLY + 0.1)), False),
    ("sup", "ddb", ((C3 + 0.28, RB - 0.3), (C4 - 0.3, RA + 0.3)), False),
    ("sup", "sns", ((C3 + 0.32, RB + 0.2), (C4 - 0.6, RB + 0.2), (C4 - 0.6, SNSY), (C4 - 0.32, SNSY)), False),
    ("sup", "openai", ((C3 + 0.32, RB), (OPENAI[0], RB)), False),
    ("sns", "email", ((C4 + 0.32, SNSY), (11.8 - 0.32, SNSY)), False),
]
BADGES = [(1, 1.38, RB - 0.27), (2, 3.35, RB - 0.27), (3, 10.25, RB - 0.27), (4, 6.6, 3.4), (5, 6.62, 2.5),
          (6, 10.25, SNSY - 0.27), (7, 3.35, RA - 0.27)]
LEGEND = [
    "Claims arrive: one at a time (presigned upload from the UI) or as a batch dropped into batches/<id>/.",
    "Each S3 event becomes an SQS message; the queue absorbs the batch, retries, then uses the DLQ.",
    "Extraction Agent (in the supervisor Lambda) reads the claim via the OpenAI API: luna first, sol if risky.",
    "Rules in code; the Resolution Agent does typed FHIR lookups and creates the Claim in HealthLake.",
    "Code decides the route; status, audit trail and atomic batch counters land in DynamoDB.",
    "SNS sends processor / provider messages and one summary per completed batch.",
    "Processors review only the exceptions in the UI (Amplify → API Gateway → Lambda).",
]
LEGEND_BOX = (10.15, 0.42, 3.1, 3.7)


# ---------------------------------------------------------------- toolkit icons
def find_icon(slide, label):
    pics = [s for s in slide.shapes if s.shape_type == 13]
    texts = [s for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip().replace("\n", " ") == label]
    if not texts:
        raise KeyError(f"label {label!r} not found on toolkit slide")
    t = texts[0]
    tcx = t.left + t.width / 2
    best = min((p for p in pics if p.top + p.height <= t.top + EMU * 0.05),
               key=lambda p: abs(p.left + p.width / 2 - tcx) + abs(t.top - (p.top + p.height)))
    return best


def capture(el, part):
    """Copy a picture/shape element plus the image parts its r:embed ids point to."""
    el = copy.deepcopy(el)
    rels = {}
    for node in el.iter():
        for attr in (qn("r:embed"), qn("r:link")):
            rid = node.get(attr)
            if rid:
                rels[rid] = part.related_part(rid)
    return el, rels


def svg_to_png(svg: bytes, px: int = 192) -> bytes | None:
    exe = shutil.which("rsvg-convert") or "/opt/homebrew/bin/rsvg-convert"
    try:
        return subprocess.run([exe, "-w", str(px), "-h", str(px)], input=svg, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def svg_blob(rels):
    for p in rels.values():
        if p.content_type == "image/svg+xml":
            return p.blob
    return next(iter(rels.values())).blob


def main(toolkit: str):
    prs = Presentation(toolkit)
    icons = {}
    for key, (idx, label) in ICONS.items():
        s = prs.slides[idx]
        icons[key] = capture(find_icon(s, label)._element, s.part)
    groups = {}
    for g in prs.slides[24].shapes:
        if g.shape_type == 6:
            kids = list(g.shapes)
            name = next((k.text_frame.text.strip() for k in kids if k.has_text_frame and k.text_frame.text.strip()), "")
            if name in ("AWS Cloud", "Region") and name not in groups:
                rect = next(k for k in kids if k.has_text_frame)
                pic = next(k for k in kids if k.shape_type == 13)
                groups[name] = (capture(rect._element, prs.slides[24].part), capture(pic._element, prs.slides[24].part))

    # drop every toolkit slide, keep its theme and layouts
    sld_ids = prs.slides._sldIdLst
    for sid in list(sld_ids):
        prs.part.drop_rel(sid.rId)
        sld_ids.remove(sid)
    slide = prs.slides.add_slide(next(l for l in prs.slide_layouts if l.name == "Blank"))
    slide._element.set("showMasterSp", "0")  # hide the template's footer artwork
    for ph in list(slide.placeholders):
        ph._element.getparent().remove(ph._element)
    tree = slide.shapes._spTree
    next_id = [1000]

    def place(captured, x, y, w, h):
        el, rels = captured
        el = copy.deepcopy(el)
        remap = {old: slide.part.relate_to(part, RT.IMAGE) for old, part in rels.items()}
        for node in el.iter():
            for attr in (qn("r:embed"), qn("r:link")):
                if node.get(attr) in remap:
                    node.set(attr, remap[node.get(attr)])
            if node.tag in (qn("p:cNvPr"),):
                next_id[0] += 1
                node.set("id", str(next_id[0]))
        # PNG fallback for SVG-only icons, as PowerPoint writes it, so Keynote / Google Slides / previews draw them
        for blip in el.iter(qn("a:blip")):
            svg = blip.find(".//{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip")
            if svg is not None and not blip.get(qn("r:embed")):
                png = svg_to_png(slide.part.related_part(svg.get(qn("r:embed"))).blob)
                if png:
                    _, rid = slide.part.get_or_add_image_part(io.BytesIO(png))
                    blip.set(qn("r:embed"), rid)
        xfrm = el.find(".//" + qn("a:xfrm"))
        xfrm.find(qn("a:off")).set("x", str(int(x * EMU)))
        xfrm.find(qn("a:off")).set("y", str(int(y * EMU)))
        xfrm.find(qn("a:ext")).set("cx", str(int(w * EMU)))
        xfrm.find(qn("a:ext")).set("cy", str(int(h * EMU)))
        tree.insert_element_before(el, "p:extLst")
        return el

    def text(x, y, w, h, s, size=12, bold=False, color=INK, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.TOP):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = anchor
        for i, line in enumerate(s.split("\n")):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.alignment = align
            run = para.add_run()
            run.text = line
            run.font.name, run.font.size, run.font.bold = "Arial", Pt(size), bold
            run.font.color.rgb = RGBColor.from_string(color)
        return tb

    # groups: the toolkit's own AWS Cloud and Region group shapes, resized
    for name, (x, y, w, h) in (("AWS Cloud", CLOUD), ("Region", REGION)):
        rect, pic = groups[name]
        el = place(rect, x, y, w, h)
        if name == "Region":
            for t in el.iter(qn("a:t")):
                t.text = "Region: us-east-1"
        place(pic, x, y, 0.42, 0.42)

    gx, gy, gw, gh = AGENTS_BOX
    grp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(gx), Inches(gy), Inches(gw), Inches(gh))
    grp.fill.background()
    grp.line.color.rgb = RGBColor.from_string(GENERIC)
    grp.line.width = Pt(1)
    grp.line.dash_style = MSO_LINE.DASH
    text(gx + 0.08, gy + 0.05, 0.9, 0.25, "Agents SDK", size=10, color=GENERIC, align=PP_ALIGN.LEFT)

    nodes = {}
    for key, icon, cx, cy, label, pos in NODES:
        place(icons[icon], cx - ICON / 2, cy - ICON / 2, ICON, ICON)
        if pos == "below":
            text(cx - 0.85, cy + ICON / 2 + 0.05, 1.7, 0.7, label)
        else:
            text(cx + ICON / 2 + 0.1, cy - 0.3, 1.85, 0.75, label, align=PP_ALIGN.LEFT)
        nodes[key] = (cx, cy)

    ox, oy, ow, oh = OPENAI
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(ox), Inches(oy), Inches(ow), Inches(oh))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor.from_string(OPENAI_FILL)
    box.line.fill.background()
    box.adjustments[0] = 0.12
    box.text_frame.text = ""
    text(ox + 0.1, oy + 0.12, ow - 0.2, 0.3, OPENAI_LINES[0], size=15, bold=True, color="FFFFFF")
    text(ox + 0.1, oy + 0.48, ow - 0.2, oh - 0.55, "\n".join(OPENAI_LINES[1:]), size=11, color="FFFFFF")

    def center_edge(a, b):
        (ax, ay), (bx, by) = nodes[a], nodes[b] if b in nodes else (ox, oy + oh / 2)
        h = ICON / 2 + 0.02
        if abs(ay - by) < 0.01:
            return (ax + h if bx > ax else ax - h, ay), (bx - h if bx > ax else bx + h, by)
        return (ax, ay + h if by > ay else ay - h), (bx, by - h if by > ay else by + h)

    for a, b, pts, dashed in ARROWS:
        pts = pts or center_edge(a, b)
        for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
            c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
            c.line.color.rgb = RGBColor.from_string(INK)
            c.line.width = Pt(1.25)
            if dashed:
                c.line.dash_style = MSO_LINE.DASH
            if i == len(pts) - 2:
                ln = c.line._get_or_add_ln()
                etree.SubElement(ln, qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})

    def badge(n, x, y, d=0.27):
        o = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x - d / 2), Inches(y - d / 2), Inches(d), Inches(d))
        o.fill.solid()
        o.fill.fore_color.rgb = RGBColor.from_string(INK)
        o.line.fill.background()
        tf = o.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = str(n)
        r.font.name, r.font.size, r.font.bold = "Arial", Pt(11), True
        r.font.color.rgb = RGBColor.from_string("FFFFFF")

    for n, x, y in BADGES:
        badge(n, x, y)
    lx, ly, lw, lh = LEGEND_BOX
    for i, s in enumerate(LEGEND):
        badge(i + 1, lx + 0.14, ly + 0.14 + i * 0.5)
        text(lx + 0.38, ly + i * 0.5, lw - 0.38, 0.5, s, size=10, align=PP_ALIGN.LEFT)
    text(0.3, 7.16, 9.4, 0.3, "ClaimPilot: claims intake automation. Every model call goes to the OpenAI Platform; the customer's AWS account hosts the app and data.",
         size=10, color=GREY, align=PP_ALIGN.LEFT)

    for master in prs.slide_masters:
        for layout in list(master.slide_layouts):
            if layout is not slide.slide_layout and not layout.used_by_slides:
                master.slide_layouts.remove(layout)
    OUT.mkdir(parents=True, exist_ok=True)
    prs.save(OUT / "claimpilot-architecture.pptx")
    write_svg(icons, nodes, groups)
    print("wrote", OUT / "claimpilot-architecture.pptx", "and .svg")


# ---------------------------------------------------------------- SVG twin (same coordinates)
def write_svg(icons, nodes, groups):
    S = 96  # px per inch
    W, H = 13.333 * S, 7.5 * S
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
           'font-family="Arial, Helvetica, sans-serif">',
           '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
           f'<path d="M0,0 L10,5 L0,10 z" fill="#{INK}"/></marker></defs>',
           f'<rect width="{W:.0f}" height="{H:.0f}" fill="#ffffff"/>']

    def img(blob, x, y, w, h):
        mime = "image/png" if blob[:4] == b"\x89PNG" else "image/svg+xml"
        out.append(f'<image x="{x*S:.1f}" y="{y*S:.1f}" width="{w*S:.1f}" height="{h*S:.1f}" '
                   f'href="data:{mime};base64,{base64.b64encode(blob).decode()}"/>')

    def txt(x, y, w, s, size=12, anchor="middle", color=INK, weight="normal"):
        px = size * 96 / 72
        ax = x * S + (w * S / 2 if anchor == "middle" else 0)
        lines = s.split("\n")
        out.append(f'<text x="{ax:.1f}" y="{y*S + px:.1f}" font-size="{px:.1f}" fill="#{color}" text-anchor="{anchor}" '
                   f'font-weight="{weight}">' + "".join(
                       f'<tspan x="{ax:.1f}" dy="{0 if i == 0 else px*1.15:.1f}">{l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</tspan>'
                       for i, l in enumerate(lines)) + "</text>")

    x, y, w, h = CLOUD
    out.append(f'<rect x="{x*S}" y="{y*S}" width="{w*S}" height="{h*S}" fill="none" stroke="#{INK}" stroke-width="1.3"/>')
    img(svg_blob(groups["AWS Cloud"][1][1]), x, y, 0.42, 0.42)
    txt(x + 0.5, y + 0.1, 2, "AWS Cloud", anchor="start")
    x, y, w, h = REGION
    out.append(f'<rect x="{x*S}" y="{y*S}" width="{w*S}" height="{h*S}" fill="none" stroke="#{TEAL}" stroke-width="1.3" stroke-dasharray="5,4"/>')
    img(svg_blob(groups["Region"][1][1]), x, y, 0.42, 0.42)
    txt(x + 0.5, y + 0.1, 2.5, "Region: us-east-1", anchor="start", color=TEAL)
    gx, gy, gw, gh = AGENTS_BOX
    out.append(f'<rect x="{gx*S:.1f}" y="{gy*S:.1f}" width="{gw*S:.1f}" height="{gh*S:.1f}" fill="none" '
               f'stroke="#{GENERIC}" stroke-width="1.3" stroke-dasharray="5,4"/>')
    txt(gx + 0.08, gy + 0.05, 0.9, "Agents SDK", size=10, anchor="start", color=GENERIC)
    for key, icon, cx, cy, label, pos in NODES:
        img(svg_blob(icons[icon][1]), cx - ICON / 2, cy - ICON / 2, ICON, ICON)
        if pos == "below":
            txt(cx - 0.85, cy + ICON / 2 + 0.05, 1.7, label)
        else:
            txt(cx + ICON / 2 + 0.1, cy - 0.3, 1.85, label, anchor="start")
    ox, oy, ow, oh = OPENAI
    out.append(f'<rect x="{ox*S}" y="{oy*S}" width="{ow*S}" height="{oh*S}" rx="10" fill="#{OPENAI_FILL}"/>')
    txt(ox, oy + 0.12, ow, OPENAI_LINES[0], size=15, color="FFFFFF", weight="bold")
    txt(ox, oy + 0.48, ow, "\n".join(OPENAI_LINES[1:]), size=11, color="FFFFFF")
    for a, b, pts, dashed in ARROWS:
        if pts is None:
            (ax, ay), (bx, by) = nodes[a], nodes[b]
            hh = ICON / 2 + 0.02
            if abs(ay - by) < 0.01:
                pts = ((ax + hh if bx > ax else ax - hh, ay), (bx - hh if bx > ax else bx + hh, by))
            else:
                pts = ((ax, ay + hh if by > ay else ay - hh), (bx, by - hh if by > ay else by + hh))
        d = " ".join(f"{x*S:.1f},{y*S:.1f}" for x, y in pts)
        out.append(f'<polyline points="{d}" fill="none" stroke="#{INK}" stroke-width="1.6" '
                   f'{"stroke-dasharray=\"6,4\" " if dashed else ""}marker-end="url(#ah)"/>')

    def badge(n, x, y):
        out.append(f'<circle cx="{x*S:.1f}" cy="{y*S:.1f}" r="{0.135*S:.1f}" fill="#{INK}"/>')
        out.append(f'<text x="{x*S:.1f}" y="{y*S + 5:.1f}" font-size="14" font-weight="bold" fill="#fff" text-anchor="middle">{n}</text>')

    for n, x, y in BADGES:
        badge(n, x, y)
    lx, ly, lw, lh = LEGEND_BOX
    for i, s in enumerate(LEGEND):
        badge(i + 1, lx + 0.14, ly + 0.14 + i * 0.5)
        words, lines, cur = s.split(), [], ""
        for wd in words:  # wrap at ~44 chars for 10pt in 2.77in
            if len(cur) + len(wd) + 1 > 40:
                lines.append(cur)
                cur = wd
            else:
                cur = (cur + " " + wd).strip()
        lines.append(cur)
        txt(lx + 0.38, ly + i * 0.5, lw - 0.38, "\n".join(lines), size=10, anchor="start")
    txt(0.3, 7.16, 9.4, "ClaimPilot: claims intake automation. Every model call goes to the OpenAI Platform; the customer's AWS account hosts the app and data.",
        size=10, anchor="start", color=GREY)
    out.append("</svg>")
    (OUT / "claimpilot-architecture.svg").write_text("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
