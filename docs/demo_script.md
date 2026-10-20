# 5-minute demo script

A finished recording of this script is produced by `scripts/make_demo_video.py` (narration from `docs/recording/narration.json`, generated with OpenAI TTS; live capture of the deployed app). See "Recording" below.

**Setup, 10 minutes before:**
1. Open https://main.dyc07h1e3zuao.amplifyapp.com, enter the access key, and browser-zoom to 90%.
2. Warm-up: click **CLM-1001** once. This warms both Lambdas.
3. Open a second tab on the OpenAI dashboard → **Traces** (platform.openai.com/traces).
4. Open your email inbox in a third tab, for the SNS batch summary.
5. Have the deck open on slide 1.
6. **Backup:** `python scripts/local_api.py` + `npm run dev` (same UI and batch flow, local), and the screen recording.

An 18-claim batch takes about 2 to 2.5 minutes at 6 in parallel. **Start it first, then talk while it runs.**

| Time | Screen | What to say (and do) |
|---|---|---|
| **0:00–0:40** | Slide 1, the customer | "Cascadia Health Plan, a regional payer with 450k members. I assume about 5% of its professional claims, roughly 300,000 a year, still arrive as paper, fax or PDF CMS-1500s, usually as a daily batch from a scanning vendor. The national average is closer to 2% (CAQH 2024), so I show that case too. Each one is keyed by hand, then the processor looks up the member, the coverage and the provider in separate systems, and then decides to accept it or bounce it. I assume around 8 minutes a claim, and a pilot would measure it." |
| **0:40–1:10** | Slide 2, today vs. with ClaimPilot | "With ClaimPilot, the system reads, checks and matches every claim and accepts the clear ones. Processors handle only the exceptions, each with the reason attached. One surface: the OpenAI Platform, specifically the Agents SDK on the Responses API." |
| **1:10–1:30** | Slide 3, solution | "Two agents, each with one job, run in a fixed order by ordinary code. Business rules and the final decision are code, so they're testable and auditable." → **switch to the UI** |
| **1:30–1:45** | UI: click **"Drop all 18 samples as one batch"** | "This is this morning's batch from the scanning vendor: 18 claims land in S3 at once, including a prompt-injection attempt and a duplicate. Each one becomes a message on an SQS queue, and Lambda works through them 6 at a time. That number is a setting; I've kept it low on this demo account, which allows 10 concurrent Lambda functions, so the app stays responsive." Point at the **Batches** progress bar filling. |
| **1:45–2:35** | UI: open **CLM-1004** (letter-O member ID) as soon as it's done | Open **Agent trace**: "The ID was typed with a letter O. The first search finds nothing. The agent retries with a zero, which is a look-alike fix our policy allows. It matches name *and* birth date, so it creates the FHIR Claim." Point at the **corrected** chip: "Logged as a correction, not silently fixed. Auto-accepted in about 20 seconds for about a cent." |
| **2:35–3:15** | UI: open **CLM-1015** (phone photo) | "A phone photo: skewed and shadowed. The fast model, gpt-6-luna, reads every claim. Code escalated this one to gpt-6.1-sol because the scan wasn't clean. Even then the billing NPI wasn't readable with confidence, so it goes to a processor instead of being accepted." Point at the **Extraction cascade** line. "And one rule I added after a live batch: nothing goes back to a provider on the fast model's read alone. The strong model confirms first." Optional: show the run in **Traces**. |
| **3:15–3:55** | UI: open **CLM-1012** (smudged fax) or **CLM-1007** (coverage ended) | "Here the system *doesn't* decide." Show **Validation**: "The birth date is smudged, and a misread isn't the provider's mistake, so it goes to a processor." Show **Processor review**: pre-drafted, editable provider message. Click **Approve** or **Return**: "One click, with an audit trail. No model call on this path." |
| **3:55–4:10** | UI: **Batches** row (complete) + email tab | "Batch done: 7 accepted, 6 for review, 5 returned, about 12 cents for all 18. One SNS summary to the team." (Counts can shift by one between runs; any shift is toward review, the safe side.) Show the summary email. |
| **4:10–5:00** | Slides 4–5 | "Measured on 18 labeled claims, including a prompt-injection attempt and a duplicate, run twice: the cascade made no wrong auto-accepts and no wrong returns, about a cent per claim. The fast model alone was also safe on this set, at a ninth of the cost, so the eval can't rank them on safety; I keep the cascade as insurance. At 60% auto-accept and 5% paper, that's about $1.3M a year in processor capacity, around 17 FTEs; at the 2% national average, about $0.5M. Model cost is under 2% of what's left. Governance: synthetic data here; production needs a BAA and Zero Data Retention, which also turns tracing off, so our own audit trail is the record. Next: an 8-week pilot on 500+ real de-identified scans." |

**If a live claim is slow:** "This is a real model call. While it runs…" and open one that's already done.
**If the API fails:** switch to the local backup, or play the recording from the same timestamp.
**Panel asks how you chose the threshold:** show `docs/evals/threshold_sweep.png` (appendix slide) and the OpenAI Evals dashboard.
**Panel asks for the AWS view:** open `docs/architecture/claimpilot-architecture.pptx` (or the appendix slide) and walk the numbered steps 1–7.

## Recording

`python scripts/make_demo_video.py narrate | slides | capture | assemble` rebuilds the video (about 15 minutes, mostly waiting on the live batch). The narration is in `docs/recording/narration.json`.
- **`recording/claimpilot-demo.mp4`** (about 4:23): footage, OpenAI TTS narration (model `gpt-4o-mini-tts`, voice `cedar`) and captions.
- **`recording/claimpilot-demo-captions-only.mp4`**: identical footage with no audio and the same captions, for sending without a voice. The captions carry the narration, so a reviewer can follow every step.
- **`recording/claimpilot-demo.srt`**: the captions as a separate file, if you'd rather upload them to a video host.
- Captions sit in a dedicated bar at the bottom of the frame, so they never cover a slide or the app.
- To change what's said, edit `narration.json`, then re-run `narrate` for that segment and `assemble`. A segment that shows live footage may also need `capture`, `letter-o`, `batch-start` or `batch-done` to match.
