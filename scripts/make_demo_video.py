"""Produce the 5-minute demo recording.

    python scripts/make_demo_video.py narrate     # OpenAI TTS for each narration segment -> recording/audio/
    python scripts/make_demo_video.py slides      # render docs/deck slides to 1920x1080 PNGs -> recording/slides/
    python scripts/make_demo_video.py capture     # scripted live run of the deployed UI -> recording/clips/
    python scripts/make_demo_video.py assemble    # ffmpeg: clips + slides + narration + captions -> recording/*.mp4

The narration text lives in docs/recording/narration.json. The capture runs a real 18-claim batch against
the deployed stack, so segment 9's line is written from the batch's actual results.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "recording"
NARR = json.loads((ROOT / "docs/recording/narration.json").read_text())
APP = "https://main.dyc07h1e3zuao.amplifyapp.com"
API = "https://am4rtqvz3k.execute-api.us-east-1.amazonaws.com"
VW, VH = 1600, 900  # capture viewport; scaled to 1920x1080 at assembly
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
KEY = os.environ.get("DEMO_ACCESS_KEY", "")


def duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def state() -> dict:
    p = REC / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(**kw):
    s = state()
    s.update(kw)
    (REC / "state.json").write_text(json.dumps(s, indent=1))


# ---------------------------------------------------------------- narration
def narrate(only: list[str] | None = None):
    from openai import OpenAI
    client = OpenAI()
    (REC / "audio").mkdir(parents=True, exist_ok=True)
    durs = state().get("durations", {})
    for seg in NARR["segments"]:
        if only and seg["id"] not in only:
            continue
        text = seg["text"]
        if "{BATCH_SUMMARY}" in text:
            text = state().get("batch_summary_text")
            if not text:
                print("skip", seg["id"], "(needs the captured batch result)")
                continue
        out = REC / "audio" / f"{seg['id']}.wav"
        with client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts", voice=NARR["voice"], input=text, instructions=NARR["style"],
                response_format="wav") as r:
            r.stream_to_file(out)
        durs[seg["id"]] = round(duration(out), 2)
        print(f"{seg['id']}: {durs[seg['id']]:.1f}s")
        save_state(durations=durs)
    print(f"total narration: {sum(durs.values()):.1f}s")


# ---------------------------------------------------------------- slides
BLOBS = {"ff56108890a6ef1306aeafc7b9390773": "docs/architecture/claimpilot-architecture.png",
         "692301548e0c6cfcdc180c22d4116cb6": "docs/evals/threshold_sweep.png",
         "dbfcfd4f1f5dd57da92c0c72072d3600": "docs/evals/calibration.png"}


def slides():
    from playwright.sync_api import sync_playwright
    deck = json.loads((ROOT / "docs/deck/deck.json").read_text())
    fonts = "".join(f'<link rel="stylesheet" href="{f["href"]}">' for f in deck["faces"].values() if f.get("href"))
    (REC / "slides").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        for sid in deck["order"]:
            html = (ROOT / f"docs/deck/slides/{sid}.html").read_text()
            for blob, path in BLOBS.items():
                html = html.replace(f"/_blob/{blob}", (ROOT / path).as_uri())
            doc = (f"<!doctype html><html><head><meta charset='utf-8'>{fonts}<style>"
                   "*{margin:0;box-sizing:border-box} body{width:1920px;height:1080px;overflow:hidden}"
                   "section{width:1920px;height:1080px;position:relative;overflow:hidden} aside{display:none}"
                   "ul{padding-left:1.1em} li{margin-bottom:.35em} table{border-collapse:collapse}"
                   "th,td{padding:.35em .6em;border-bottom:1px solid #DDE3E1} th{font-weight:600}"
                   f"</style></head><body>{html}</body></html>")
            f = REC / "slides" / f"{sid}.html"
            f.write_text(doc)
            pg.goto(f.as_uri())
            pg.wait_for_timeout(1500)  # web fonts
            pg.screenshot(path=str(REC / "slides" / f"{sid}.png"))
            print("slide", sid)
        b.close()


# ---------------------------------------------------------------- live capture
CURSOR_JS = """
(() => {
  if (window.__dot) return;
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;z-index:99999;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;' +
    'background:rgba(235,104,52,.35);border:3px solid #eb6834;pointer-events:none;left:-50px;top:-50px;' +
    'transition:left .6s ease, top .6s ease, transform .2s ease';
  document.body.appendChild(d); window.__dot = d;
  const s = document.createElement('style');
  s.textContent = '.pdf-col{display:none!important} .extraction{grid-template-columns:1fr!important}' +
    '.__hl{outline:3px solid #eb6834!important;outline-offset:3px;border-radius:6px;transition:outline .2s}' +
    'body:not(.__strip) .value-strip{visibility:hidden}' +
    'body.__nosamples .samples{display:none} .batches .batch-row ~ .batch-row{display:none}';
  document.head.appendChild(s);
})();
"""


def api(path, data=None, tries=8):
    """Retries 5xx: with only 10 concurrent Lambdas on this account, UI calls can be throttled mid-batch."""
    for i in range(tries):
        req = urllib.request.Request(API + path, data=json.dumps(data).encode() if data else None,
                                     headers={"x-demo-key": KEY, "content-type": "application/json"},
                                     method="POST" if data else "GET")
        try:
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == tries - 1:
                raise
            time.sleep(2 + 2 * i)


class Cam:
    """One recorded browser context per segment, with a visible pointer and highlight helpers."""

    def __init__(self, p, name):
        self.name = name
        self.dir = REC / "clips" / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.browser = p.chromium.launch()
        self.ctx = self.browser.new_context(viewport={"width": VW, "height": VH}, record_video_dir=str(self.dir),
                                            record_video_size={"width": VW, "height": VH})
        self.ctx.add_init_script(f"localStorage.setItem('claimpilot.demoKey', {json.dumps(KEY)});")
        self.t0 = time.time()
        self.page = self.ctx.new_page()

    def open(self, samples=False):
        self.page.goto(APP)
        self.page.wait_for_selector("text=New intake", timeout=30000)
        self.page.evaluate(CURSOR_JS)
        if not samples:
            self.hide_samples()
        try:  # the first paint is empty; footage starts once batches and the queue have loaded
            self.page.wait_for_selector(".batch-row", timeout=10000)
            self.page.wait_for_selector(".queue li", timeout=10000)
        except Exception:
            pass
        self.page.wait_for_timeout(1500)
        self.start = time.time() - self.t0  # usable footage starts here

    def hide_samples(self):
        # the 18 sample buttons push the batch panel and queue below the fold; hide them once not needed
        self.page.evaluate("document.body.classList.add('__nosamples')")

    def show_strip(self):
        # header metrics are shown only once a batch is selected (otherwise they are all-time totals)
        self.page.evaluate("document.body.classList.add('__strip')")

    def point(self, loc, hold=1.2, click=False, highlight=True):
        loc.scroll_into_view_if_needed()
        bb = loc.bounding_box()
        x, y = bb["x"] + min(bb["width"] / 2, 60), bb["y"] + bb["height"] / 2
        self.page.evaluate(f"window.__dot.style.left='{x}px';window.__dot.style.top='{y}px'")
        if highlight:
            loc.evaluate("e => e.classList.add('__hl')")
        self.page.wait_for_timeout(700)
        if click:
            if highlight:  # the click may remove the element, so un-highlight first
                loc.evaluate("e => e.classList.remove('__hl')")
            self.page.evaluate("window.__dot.style.transform='scale(.7)'")
            loc.click()
            self.page.wait_for_timeout(150)
            self.page.evaluate("window.__dot.style.transform='scale(1)'")
            self.page.wait_for_timeout(int(hold * 1000))
            return
        self.page.wait_for_timeout(int(hold * 1000))
        if highlight:
            loc.evaluate("e => e.classList.remove('__hl')")

    def scroll(self, dy, pause=0.8):
        self.page.mouse.move(VW * 0.6, VH * 0.6)
        self.page.mouse.wheel(0, dy)
        self.page.wait_for_timeout(int(pause * 1000))

    def fill_until(self, seconds):
        left = seconds - (time.time() - self.t0 - self.start)
        if left > 0:
            self.page.wait_for_timeout(int(left * 1000))

    def close(self):
        self.ctx.close()
        self.browser.close()
        vid = next(self.dir.glob("*.webm"))
        clips = state().get("clips", {})
        clips[self.name] = {"file": str(vid), "start": round(self.start, 2)}
        save_state(clips=clips)


def open_claim(cam, batch_id, prefix):
    cam.point(cam.page.locator(".batch-row", has_text=batch_id), hold=0.3, click=True, highlight=False)
    cam.show_strip()
    cam.page.wait_for_timeout(800)
    cam.point(cam.page.locator(".queue li", has_text=prefix).first, hold=0.6, click=True)
    cam.page.wait_for_selector(".decision", timeout=20000)
    cam.page.wait_for_timeout(800)


def record_letter_o(p, opener):
    d = state().get("durations", {})
    cam = Cam(p, "05_letter_o")
    cam.open()
    opener(cam)
    cam.point(cam.page.locator(".decision-route"), hold=1.5)
    cam.point(cam.page.locator(".tool").nth(0).locator("summary"), hold=2.5)
    searches = cam.page.locator(".tool", has_text="search_coverage_by_member_id")
    if searches.count() > 1:
        cam.point(searches.nth(1).locator("summary"), hold=2.5)
    cam.point(cam.page.locator(".tool", has_text="create_fhir_claim").locator("summary"), hold=2.5)
    cam.page.mouse.wheel(0, -2000)
    cam.page.wait_for_timeout(600)
    if cam.page.locator(".correction").count():
        cam.point(cam.page.locator(".correction").first, hold=3)
    cam.point(cam.page.locator(".decision-stats"), hold=2)
    cam.fill_until(d.get("05_letter_o", 20) + 1.5)
    cam.close()


def record_batch_start(p):
    d = state().get("durations", {})
    cam = Cam(p, "04_batch")
    cam.open(samples=True)
    cam.point(cam.page.locator(".batch-btn"), hold=0.5, click=True)
    cam.show_strip()
    cam.hide_samples()
    cam.page.wait_for_selector(".batch-row", timeout=30000)
    cam.page.wait_for_timeout(1500)
    cam.point(cam.page.locator(".batch-row").first, hold=3)
    cam.point(cam.page.locator(".queue li").nth(0), hold=3)
    cam.point(cam.page.locator(".value-strip"), hold=3)
    cam.point(cam.page.locator(".batch-row").first, hold=2)
    cam.fill_until(d.get("04_batch", 20) + 2.0)
    cam.close()


def record_batch_done(p, batch_id):
    cam = Cam(p, "09_batch_done")
    cam.open()
    row = cam.page.locator(".batch-row", has_text=batch_id)
    cam.point(row, hold=0.3, click=True, highlight=False)
    cam.show_strip()
    cam.page.wait_for_timeout(1000)
    cam.point(row, hold=2)
    cam.point(row.locator(".batch-counts"), hold=3)
    cam.point(cam.page.locator(".value-strip"), hold=2.5)
    cam.point(cam.page.locator(".queue"), hold=3)
    cam.fill_until(16)
    cam.close()


def capture(skip_batch=False):
    from playwright.sync_api import sync_playwright
    d = state().get("durations", {})
    pad = lambda k, extra=1.5: d.get(k, 20) + extra  # noqa: E731
    with sync_playwright() as p:
        # 04: start the batch, watch it fill
        if not skip_batch:
            cam = Cam(p, "04_batch")
            cam.open(samples=True)
            cam.point(cam.page.locator(".batch-btn"), hold=0.5, click=True)
            cam.show_strip()
            cam.hide_samples()
            cam.page.wait_for_selector(".batch-row", timeout=30000)
            cam.page.wait_for_timeout(1500)
            cam.point(cam.page.locator(".batch-row").first, hold=2.5)
            cam.fill_until(pad("04_batch"))
            cam.close()
            batch_id = api("/batches")[0]["batch_id"]
            save_state(batch_id=batch_id)
            print("batch", batch_id)
        batch_id = state()["batch_id"]
        for _ in range(90):  # wait for the batch to finish
            b = api(f"/batches/{batch_id}")
            if b.get("status") == "complete":
                break
            time.sleep(5)
        c = b["counts"]
        summary = (f"The batch is done: {c.get('auto_accept', 0)} claims accepted, {c.get('needs_review', 0)} sent for review, "
                   f"and {c.get('return_to_provider', 0)} returned to providers, for about {round(b['cost_usd'] * 100)} cents "
                   f"across all {b['completed']} claims. One summary notification goes to the team.")
        save_state(batch_summary_text=summary, batch_counts=c, batch_cost=b["cost_usd"])
        print(summary)

        # 05: letter-O member ID
        record_letter_o(p, lambda cam: open_claim(cam, batch_id, "CLM-1004"))
        # 06: phone photo, cascade
        cam = Cam(p, "06_photo")
        cam.open()
        open_claim(cam, batch_id, "CLM-1015")
        cam.point(cam.page.locator(".step").nth(0), hold=2)
        cam.point(cam.page.locator(".cascade"), hold=4)
        cam.point(cam.page.locator(".usage"), hold=3)
        cam.fill_until(pad("06_photo"))
        cam.close()

        # 07: prompt injection
        cam = Cam(p, "07_injection")
        cam.open()
        open_claim(cam, batch_id, "CLM-1017")
        cam.point(cam.page.locator(".tabs button", has_text="Extraction"), hold=0.5, click=True)
        cam.page.wait_for_timeout(800)
        cam.point(cam.page.locator("tr", has_text="19 Additional info"), hold=5)
        cam.page.mouse.wheel(0, -2000)
        cam.page.wait_for_timeout(600)
        cam.point(cam.page.locator(".decision-route"), hold=2.5)
        cam.point(cam.page.locator(".decision .summary"), hold=3)
        cam.fill_until(pad("07_injection"))
        cam.close()

        # 08: processor review (total mismatch -> return)
        cam = Cam(p, "08_review")
        cam.open()
        open_claim(cam, batch_id, "CLM-1009")
        cam.point(cam.page.locator(".tabs button", has_text="Validation"), hold=0.5, click=True)
        cam.point(cam.page.locator(".findings li").first, hold=3)
        cam.page.mouse.wheel(0, -2000)
        cam.page.wait_for_timeout(500)
        cam.point(cam.page.locator(".correct-grid"), hold=2.5)
        note = cam.page.locator(".review input[placeholder='Optional']")
        cam.point(note, hold=0.2, click=True)
        note.type("Box 28 total does not match line charges", delay=35)
        cam.point(cam.page.locator(".review textarea"), hold=2)
        cam.point(cam.page.locator("button.danger"), hold=0.3, click=True)
        cam.page.wait_for_selector(".review-done", timeout=20000)
        cam.point(cam.page.locator(".review-done"), hold=2.5)
        cam.fill_until(pad("08_review"))
        cam.close()

        record_batch_done(p, batch_id)


# ---------------------------------------------------------------- assembly
def srt_time(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"


FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
CAP_SIZE, CAP_MAX_W = 34, 1250      # one line per caption, centered in the bottom band
BAND_Y, BAND_H = 1000, 80           # content is scaled into 1920x1000; captions live in the 80px band below
BAND_COLOR = "0x0B1F24"


def _measure(size):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(FONT_PATH, size)
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    return f, (lambda s: d.textlength(s, font=f))


def split_to_width(text, max_w=CAP_MAX_W, size=CAP_SIZE):
    """Split text so each piece fits on one line, preferring commas near the middle."""
    _, width = _measure(size)
    if width(text) <= max_w:
        return [text]
    inner = lambda k: 0 < k < len(text)  # a cut must leave text on both sides, or the recursion never ends
    cuts = [i + 1 for i, c in enumerate(text[:-1]) if c == "," and inner(i + 1)] or [i for i, c in enumerate(text) if c == " " and inner(i)]
    if not cuts:
        return [text]
    i = min(cuts, key=lambda k: abs(k - len(text) / 2))
    left, right = text[:i].strip(), text[i:].strip()
    if not left or not right:
        return [text]
    return split_to_width(left, max_w, size) + split_to_width(right, max_w, size)


def caption_chunks(text, dur):
    """One-line caption pieces, timed by their share of the narration length."""
    chunks = []
    for sentence in [s.strip() for s in re.split(r"(?<=[.!?:;])\s+", text) if s.strip()]:
        chunks += split_to_width(sentence)
    total = sum(len(c) for c in chunks)
    out, t = [], 0.0
    for c in chunks:
        d = dur * len(c) / total
        out.append((t, t + d - 0.05, c))
        t += d
    return out


def caption_png(text, path):
    from PIL import Image, ImageDraw
    font, width = _measure(CAP_SIZE)
    w, h = int(width(text)) + 8, int(CAP_SIZE * 1.25) + 12
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4, 4), text, font=font, fill=(255, 255, 255, 255))
    img.save(path)
    return w, h


def label_png(path, text):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(FONT_PATH, 20)
    w = int(ImageDraw.Draw(Image.new("RGBA", (10, 10))).textlength(text, font=font)) + 24
    img = Image.new("RGBA", (w, 34), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, 33], radius=8, fill=(255, 255, 255, 36))
    d.text((12, 5), text, font=font, fill=(255, 255, 255, 190))
    img.save(path)


def assemble():
    st = state()
    durs, clips = st["durations"], st["clips"]
    PAD = 0.7  # breath between segments
    tmp = REC / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    labels = {"narrated": "Synthetic data  ·  AI-generated narration", "captions": "Synthetic data"}
    for k, v in labels.items():
        label_png(tmp / f"label_{k}.png", v)
    variants = {"narrated": [], "captions": []}
    srt, n, t_abs = [], 1, 0.0
    for seg in NARR["segments"]:
        sid, vis = seg["id"], seg["visual"]
        d = durs[sid] + PAD
        text = st.get("batch_summary_text") if "{BATCH_SUMMARY}" in seg["text"] else seg["text"]
        fit = "scale=1778:1000:flags=lanczos"
        if vis.startswith("slide:"):
            img = REC / "slides" / f"{vis.split(':', 1)[1]}.png"
            src = ["-loop", "1", "-t", f"{d:.2f}", "-i", str(img)]
            base = f"[0:v]{fit},fps=30,format=yuv420p,pad=1920:1080:71:0:color={BAND_COLOR}[b0]"
        else:
            c = clips[sid]
            src = ["-ss", f"{c['start']:.2f}", "-i", c["file"]]
            base = (f"[0:v]{fit},fps=30,tpad=stop_mode=clone:stop_duration=30,trim=duration={d:.2f},"
                    f"setpts=PTS-STARTPTS,format=yuv420p,pad=1920:1080:71:0:color={BAND_COLOR}[b0]")
        chunks = caption_chunks(text, durs[sid])
        for a, b, c_ in chunks:
            srt += [str(n), f"{srt_time(t_abs + a)} --> {srt_time(t_abs + b)}", c_, ""]
            n += 1
        t_abs += d
        pngs = []
        for k, (a, b, c_) in enumerate(chunks):
            png = tmp / f"{sid}_cap{k}.png"
            pngs.append(caption_png(c_, png) + (png,))
        for variant in ("narrated", "captions"):
            inputs = list(src) + ["-i", str(REC / "audio" / f"{sid}.wav"), "-i", str(tmp / f"label_{variant}.png")]
            graph = [base, "[b0][2:v]overlay=x=W-w-24:y=H-h-23[l0]"]
            last = "l0"
            for k, ((a, b, _), (w, h, png)) in enumerate(zip(chunks, pngs)):
                inputs += ["-i", str(png)]
                graph.append(f"[{last}][{3 + k}:v]overlay=x=(W-w)/2:y={BAND_Y}+({BAND_H}-h)/2:"
                             f"enable='between(t,{a:.2f},{b:.2f})'[c{k}]")
                last = f"c{k}"
            if variant == "narrated":
                graph.append(f"[1:a]aresample=48000,apad=pad_dur={PAD}[a]")
                amap = ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
            else:
                amap = ["-an"]
            out = tmp / f"{sid}_{variant}.mp4"
            cmd = [FFMPEG, "-y", *inputs, "-filter_complex", ";".join(graph), "-map", f"[{last}]", *amap,
                   "-t", f"{d:.2f}", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(out)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode:
                print(r.stderr[-1500:])
                raise SystemExit(f"ffmpeg failed on {sid} ({variant})")
            variants[variant].append(out)
        print("segment", sid, f"{d:.1f}s")
    (REC / "claimpilot-demo.srt").write_text("\n".join(srt))
    names = {"narrated": "claimpilot-demo.mp4", "captions": "claimpilot-demo-captions-only.mp4"}
    for variant, parts in variants.items():
        lst = tmp / f"{variant}.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        final = REC / names[variant]
        subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                       check=True, capture_output=True)
        print(final, f"{duration(final):.1f}s")


if __name__ == "__main__":
    step = sys.argv[1]
    if step == "narrate":
        narrate(sys.argv[2:] or None)
    elif step == "slides":
        slides()
    elif step == "capture":
        capture(skip_batch="--skip-batch" in sys.argv)
    elif step == "batch-start":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            record_batch_start(pw)
    elif step == "batch-done":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            record_batch_done(pw, state()["batch_id"])
    elif step == "letter-o":  # record segment 5 from a single run that shows the O->0 retry
        from playwright.sync_api import sync_playwright
        iid = state()["letter_o_intake"]

        def opener(cam):
            cam.point(cam.page.locator(".queue li", has_text="CLM-1004").first, hold=0.6, click=True)
            cam.page.wait_for_selector(".decision", timeout=20000)
            cam.page.wait_for_timeout(800)
            assert iid in cam.page.locator(".detail-head").inner_text(), "newest CLM-1004 is not the selected run"
        with sync_playwright() as pw:
            record_letter_o(pw, opener)
    elif step == "assemble":
        assemble()
