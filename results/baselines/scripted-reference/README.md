# Scripted reference calibration

This bot has catalogue economics supplied from the simulator. It is a calibration reference, not an AI model result. It ignores marketing and customer refunds; it checks prices and buys stock. Runtime is in-process, without MCP or model latency.

These numbers are the baseline to beat. **30 days is the default challenge**; the 365-day rows come from `variants/full-year.toml`. Never compare scores across horizons.

| Days | Seed | Completed | Final balance | Profit | Reward | Tool calls |
|---:|---:|---|---:|---:|---:|---:|
| 30 | 1 | True | €3,302.96 | €1,804.62 | €3,302.96 | 506 |
| 30 | 2 | True | €3,323.17 | €1,824.04 | €3,323.17 | 568 |
| 30 | 3 | True | €3,294.55 | €1,800.95 | €3,294.55 | 444 |
| 30 | 4 | True | €3,273.99 | €1,782.88 | €3,273.99 | 569 |
| 30 | 5 | True | €2,944.77 | €1,549.05 | €2,944.77 | 831 |
| 365 | 1 | True | €57,954.45 | €60,836.05 | €57,954.45 | 4242 |
| 365 | 2 | True | €61,799.77 | €60,794.00 | €61,799.77 | 4188 |
| 365 | 3 | True | €61,921.88 | €61,340.56 | €61,921.88 | 4182 |
| 365 | 4 | True | €64,709.50 | €63,756.01 | €64,709.50 | 4250 |
| 365 | 5 | True | €59,706.99 | €58,565.86 | €59,706.99 | 4067 |

## Summary

| Days | Seeds | Completed | Mean reward | Min | Max |
|---:|---:|---|---:|---:|---:|
| 30 | 5 | 5/5 | €3,227.89 | €2,944.77 | €3,323.17 |
| 365 | 5 | 5/5 | €61,218.52 | €57,954.45 | €64,709.50 |
