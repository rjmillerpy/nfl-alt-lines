# Build log

This public repository starts at the open-core split (2026-10-20); the work before it happened in the private
repository, whose history includes the model. This log is the history of the engineering, in order. Every
change after the split appears as its own commit here.

| Date | Milestone |
|---|---|
| 2026-10-14 | First version: odds logger, alternate-line pricing at several books, a graded ledger, a weekly betting plan by email |
| 2026-10-15 | CI: ruff, strict mypy, Bandit, pytest with a coverage floor, TruffleHog, Dependabot, CODEOWNERS; every module brought under [CODE_QUALITY_RULES.md](CODE_QUALITY_RULES.md) with structure tests (function and module size, nesting, a mirrored test per source file) |
| 2026-10-16 | Closing snapshots and closing line value (CLV) against the no-vig closing consensus; calibration in grading (Brier, log loss, reliability) |
| 2026-10-16 | Containerized: one entry point, multi-stage non-root image, docker compose, a local cloud stand-in (moto S3 and Postgres) |
| 2026-10-16 | State off local disk: a storage interface (local or S3) and the ledgers in SQLite or Postgres, with idempotent writes |
| 2026-10-16 | Terraform for AWS: network without a NAT gateway, S3, ECR, scheduled Fargate jobs, least-privilege IAM, GitHub OIDC deploys, remote state with S3-native locking, offline `terraform test` |
| 2026-10-17 | Applied and cut over to AWS: both hourly jobs on Fargate, secrets in SSM, Postgres through a connection pooler; a second email list for bets only |
| 2026-10-18 | Observability: one JSON log line per event with a run id, a `runs` table row per execution, CloudWatch alarms on a failed run and on a missed run |
| 2026-10-18 | Line shopping: each bet goes to the book paying the most for that exact line |
| 2026-10-19 | Line-movement snapshots every three hours within an API credit budget; a private weekly results email; prediction-market exchanges (with fees taken out of their prices) and an offshore book behind a licensed fallback |
| 2026-10-19 | Profiling in production: per-stage timings found two hot spots; prop grading went from a per-row index scan to one join (107 s to 0.05 s on 123,000 lines), report runs from 100 s to 47 s |
| 2026-10-20 | Per-player weighted averages vectorized (props for a game day from 167 s to 30 s, identical output); CLV per bet in the results email |
| 2026-10-20 | Python 3.12 to 3.14, tested first: identical report output on both, then deployed |
| 2026-10-20 | Open-core split: a strategy interface found by entry point, a public baseline strategy, the production strategy moved to a private package, and this mirror exported from an allowlist with a forbidden-term check ([docs/open-core-setup.md](docs/open-core-setup.md)); production's output stayed byte-identical at every step |
