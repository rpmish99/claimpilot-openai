# Eval results (18 synthetic claims × 2 runs per configuration)

| Configuration | Routing | Wrong auto-accepts | Wrong returns | Unneeded reviews | Escalated | ¢ / claim | p50 s |
|---|---|---|---|---|---|---|---|
| cascade, threshold 0.70 | 94% | 0 | 0 | 2 | 58% | 0.77 | 38.8 |
| cascade, threshold 0.80 | 94% | 0 | 0 | 2 | 56% | 0.79 | 33.2 |
| cascade, threshold 0.90 | 94% | 0 | 0 | 2 | 58% | 0.88 | 41.1 |
| cascade, threshold 0.95 | 94% | 0 | 0 | 2 | 58% | 0.87 | 38.4 |
| luna only | 94% | 0 | 0 | 2 | 0% | 0.09 | 19.3 |
| sol only | 92% | 0 | 0 | 3 | 0% | 1.09 | 23.6 |

## Calibration (all stored extractions)

| Model | Field reads | Accuracy | Reads at ≥0.90 confidence | Wrong at ≥0.90 |
|---|---|---|---|---|
| gpt-6-luna | 3208 | 96.4% | 3022 | 23 |
| gpt-6.1-sol | 2039 | 94.5% | 1888 | 20 |

## Prompt caching (cascade runs; cost with the real cached-token counts vs. the same tokens uncached)

| Scan seen | Claims | ¢ / claim | ¢ / claim without caching | Saving | Cached share of input |
|---|---|---|---|---|---|
| first time (production-like) | 18 | 0.86 | 1.47 | 42% | escalation 69%, extraction 65%, resolution 88%, resolution_recheck 90% |
| repeat (eval re-runs) | 126 | 0.82 | 1.67 | 51% | escalation 96%, extraction 98%, resolution 91%, resolution_recheck 93% |
