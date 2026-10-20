| run | n | routing acc | false auto-accepts | false returns | unneeded reviews | FHIR match acc | field acc | hard-scan field acc | escalated | p50 s | p95 s | avg $/claim |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| luna only (2026-10-19T11:44) | 32 | 91% | 0 | 1 | 2 | 97% | 95.2% | 86.2% | 0% | 18.2 | 27.8 | $0.0015 |
| sol only (2026-10-19T11:45) | 32 | 94% | 0 | 0 | 2 | 94% | 96.2% | 87.7% | 0% | 19.0 | 33.9 | $0.0189 |
| cascade (luna→sol) (2026-10-19T11:45) | 32 | 100% | 0 | 0 | 0 | 94% | 95.0% | 85.4% | 41% | 20.9 | 67.8 | $0.0104 |
| cascade + confirm-before-reject (2026-10-19T12:58) | 32 | 97% | 0 | 0 | 1 | 94% | 95.7% | 87.7% | 50% | 24.2 | 59.5 | $0.0117 |
