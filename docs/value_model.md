# Value model: Cascadia Health Plan claims intake

Cascadia is fictitious, so every Cascadia number below is an **assumption**. The pilot's first job is to replace each one with a measurement. Numbers from outside sources are marked and linked. The OpenAI cost per claim is **measured** (`evals/results/`).

## What the industry data says (2024 CAQH Index, medical)

All figures are from the [2024 CAQH Index report](https://www.caqh.org/hubfs/Index/2024%20Index%20Report/CAQH_IndexReport_2024_FINAL.pdf), claim submission section and cost table (pages 21–23 and 51).

| | Fully manual | Fully electronic (837) |
|---|---|---|
| Share of medical claims, 2024 | **about 2%** (159 million) | about 98% (9.6 billion) |
| Plan-side labor cost per submission | **$1.02** | $0.10 |
| Provider-side labor cost per submission | $6.33 | $3.05 |
| Industry cost per submission | **$7.35** | $3.15 |

Notes: CAQH counts the labor to conduct the transaction. It excludes gathering information, follow-up and system costs. The report puts the remaining savings from automating manual transactions across medical and dental at about **$20 billion a year** (22% of the $90 billion it tracks), and the medical claim-submission share of that at $2.5 billion.

**What this means for the model:** nationally, paper is a small tail (about 2%). My base case uses 5% for a regional plan with many small practices, which is **above the national average**, so the model also shows the 2% case.

## Inputs

| Input | Value | Type |
|---|---|---|
| Professional claims per year | 6,000,000 (about 450k members) | Assumption |
| Paper, fax or PDF share | **5%, or 300,000/yr** base case; 2% (120,000) national average | Assumption; CAQH for the 2% |
| Handling time per paper claim today (keying about 25 fields, member/coverage/provider lookups, front-end edits) | **8 min** | Assumption. CAQH's plan-side $1.02 is about 1.6 min at this wage, but covers only the transaction itself, so it is a lower bound for the whole intake job |
| Loaded processor cost | **$38/hr** | Assumption |
| Review time per exception with ClaimPilot | **3 min** | Assumption; measure in the pilot |
| Auto-accept rate | **60%** (sensitivity 40–80%) | Assumption. The eval set is built around edge cases, so its rates don't transfer |
| OpenAI API cost per claim | **about 1.1¢** | Measured: 0.86¢ on the first run of each scan in the evals, and **1.14¢ in a live, fully cold 18-claim batch on the deployed stack** ($0.206). I use the higher figure. Prompt caching saves about 42% on new scans; the evals re-run the same scans, so repeats look cheaper |
| AWS variable cost per claim (Lambda, S3, DynamoDB, SNS) | about 0.1¢ | Estimate. HealthLake is a fixed ~$195/month per datastore |

## Annual impact

Savings per claim = (minutes today − exception rate × 3 min) × $38/60 − API and AWS cost. At 60% auto-accept the exception time is 0.4 × 3 = 1.2 min.

**Base case: 300,000 paper claims, 8 minutes today**

| | Today (manual) | 40% auto | **60% auto** | 80% auto |
|---|---|---|---|---|
| Processor hours | 40,000 | 9,000 | **6,000** | 3,000 |
| Labor cost | $1.52M | $342k | **$228k** | $114k |
| OpenAI + AWS variable | – | $3.6k | **$3.6k** | $3.6k |
| **Net annual savings** | – | $1.17M | **$1.29M** | $1.40M |
| FTE capacity released (2,000 h/FTE) | – | 15.5 | **17** | 18.5 |

Per claim: about $5.07 manual vs about $0.77 with ClaimPilot at 60% auto-accept.

**Sensitivity: annual savings at 60% auto-accept**

| Minutes per paper claim today | 120,000 claims (2%, national average) | 200,000 (3.3%) | 300,000 (5%, base case) |
|---|---|---|---|
| 3 | $0.14M | $0.23M | $0.34M |
| 5 | $0.29M | $0.48M | $0.72M |
| 8 | **$0.52M** (about 7 FTE) | $0.86M | **$1.29M** (about 17 FTE) |

**The honest range is about $0.1M to $1.3M a year.** It is driven by paper share and handling time, which are both assumptions. Model cost is not a driver: it is under 2% of what remains.

## Value beyond labor (not monetized above)

- **Turnaround:** the intake step takes about 20 seconds per claim, so clean claims can be ready for adjudication the same day instead of waiting in a keying queue. The current queue time is an assumption to measure in the pilot.
- **Fewer rework loops:** providers get a specific, plain-English correction request (for example, "the rendering NPI on line 1 fails the check-digit test") instead of a generic rejection. For scale, CAQH puts a provider's time saving from submitting a claim electronically instead of manually at about 7 minutes.
- **Risk:**
  - No wrong auto-accepts and no wrong returns in the final 36 runs per configuration. That is evidence that they are not common, not that they are rare: the upper bound on the rate is about 8%.
  - Every decision carries an audit trail: tool calls, rule IDs, the model used, any escalation and the reviewer's action.
- **Cost of the insurance:** the cascade costs about 0.8¢ per claim more than the fast model alone, about $2,400 a year at 300k claims. It breaks even if it prevents one wrongful return in roughly 400 to 900 claims, using CAQH's $3.15 to $7.35 to submit a claim again.

## What would change the model

- **A lower paper share or fewer minutes per claim:** see the sensitivity table. These two matter most.
- **A lower auto-accept rate in the real mix** (for example, more handwriting): at 40%, savings are $1.17M in the base case.
- **A BAA or zero-data-retention requirement:** no expected change to API price, but it needs OpenAI's approval and contracting. Under ZDR the Traces dashboard is unavailable, so the DynamoDB audit trail becomes the record.
- **Using HealthLake as the system of record vs. syncing from the core claims platform:** a fixed-cost and integration question, not a per-claim one.
