# k6

```bash
# 1) generate signed initData for synthetic users (dev/staging only)
docker compose exec api python -m app.cli fake-initdata 500 > infra/k6/initdata.txt

# 2) API load: ramps to VUS concurrent users
k6 run -e BASE=https://api.example.com -e VUS=2000 -e INIT_DATA_FILE=infra/k6/initdata.txt infra/k6/load.js

# 3) edge streaming (Range/206) with a real token
k6 run -e BASE=https://api.example.com -e TOKEN=$TOKEN infra/k6/stream.js
```

`load.js` models one Mini App session: open (auth + home) → search → play. Each VU
sleeps 6–10 s per loop, so **1 VU ≈ 1 active user**; 10,000 concurrent users is
`-e VUS=10000`, which needs several load generators (`k6 run --out ...` on 4–6 hosts,
or k6 Cloud). A single machine comfortably drives ~2,000 VUs.

Thresholds fail the run if the §10 budget is missed: API p95 > 200 ms,
search p95 > 100 ms, or more than 1% non-2xx.
