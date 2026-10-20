# infra: the two hourly jobs on AWS (roadmap Phase 4, items 16-20)

```
EventBridge Scheduler ──:05──▶ ECS Fargate task: nfl-alt-lines odds-logger ──▶ S3 (raw/odds/...)
                      ──:15──▶ ECS Fargate task: nfl-alt-lines report      ──▶ S3 (reports/...) + Gmail
                                     │  secrets from SSM Parameter Store at start     ledgers ──▶ Postgres
                                     └─ logs ──▶ CloudWatch ──failed or missed run──▶ alarm ──▶ SNS email
GitHub Actions (push to main, all checks green) ──OIDC──▶ ECR: image :<sha>, tag `live` moved to it
```

| Folder | What | State |
|---|---|---|
| `bootstrap/` | The S3 bucket for Terraform state (versioned, encrypted, TLS only) | local, applied once |
| `live/` | Network, bucket, ECR, cluster, both jobs, IAM roles, alarms, GitHub deploy role | in the bootstrap bucket, S3-native locking |
| `modules/network` | VPC, two public subnets, no NAT gateway; jobs may only connect out (443, 465, 5432) | |
| `modules/storage` | Data bucket, ECR repository (commit tags immutable, only `live` moves) | |
| `modules/scheduled_job` | Task definition, schedule, log group, optional failed-run and missed-run alarms | |

Every stack is tested offline (`terraform test`, mock AWS provider: no credentials, nothing created) in CI.

## Cost

About **$1-2 a month**, us-east-2: Fargate for about a minute per run (~$0.70), public IPv4 while tasks run
(~$0.15), ECR for five ~900 MB images (~$0.45), S3 for ~400 MB (~$0.01), CloudWatch logs and four alarms
(~$0.10). Free: SSM standard parameters, VPC, IAM, the scheduler, SNS email.

Not included: **the database**. `DATABASE_URL` points at any Postgres. Free: the **Neon** or **Supabase** free
tier (a few hundred MB each, at the time of writing; pick a us-east-2 / Ohio region); the ledgers are well
under 1 MB today. RDS's smallest instance
would add about $15-20/month after the free tier, plus the network rules for it.

Avoided on purpose: a NAT gateway (~$32/month), Container Insights (billed per metric), VPC endpoints
(~$7/month each).

## First deploy

Run from the repository root with the admin profile (`AWS_PROFILE=radshift-admin`; the account already has
GitHub's OIDC provider from rad-shift). Nothing below costs money until step 2.

1. **Database.** A free Postgres. On Supabase, sharing a project with another app, give this one its own
   login and schema, so neither app can touch the other's tables. In the dashboard's SQL editor:

   ```sql
   create role nfl_alt_lines login password '<a long random password>';
   grant nfl_alt_lines to postgres;  -- Supabase's postgres is not a superuser: without it, "must be able to SET ROLE"
   create schema nfl_alt_lines authorization nfl_alt_lines;
   alter role nfl_alt_lines set search_path = nfl_alt_lines;  -- the jobs' tables are created here
   ```

   Then take the **Session pooler** string from the dashboard's Connect button (port 5432, host
   `aws-…pooler.supabase.com`), not the direct `db.<ref>.supabase.co` host: on the free plan that one is
   IPv6-only and the tasks have IPv4 only. Make it
   `postgresql+psycopg://nfl_alt_lines.<project ref>:<password>@<pooler host>:5432/postgres?sslmode=require`.
2. **State bucket.** `terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply`
3. **The stack, schedules off** (they are on by default; until the cutover the laptop's cron still runs).

   ```
   terraform -chdir=infra/live init -backend-config="bucket=$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
   terraform -chdir=infra/live apply -var schedules_enabled=false   # optional: -var alert_email=... (or a gitignored terraform.tfvars)
   ```

   With `alert_email`, AWS emails a confirmation link; the alarm only reaches you once it is clicked.
4. **Secrets.** `infra/scripts/put_parameters.sh` stores ODDS_API_KEY, SMTP_USER, SMTP_APP_PASSWORD and the
   optional BANKROLL / REPORT_EMAIL_TO / REPORT_EMAIL_BETS_TO / REPORT_SUBJECT_PREFIX from `.env`, and asks for
   the cloud DATABASE_URL. It prints names only. If it reports fewer optional settings than all four, set
   `optional_report_settings` to match (a task referencing a missing parameter fails to start).
5. **First image.** Set the repository variable `AWS_DEPLOY_ROLE_ARN` to `terraform -chdir=infra/live output
   -raw deploy_role_arn`; from then on every green push to main deploys. Until GitHub Actions minutes are
   available for this private repository, push from the laptop instead:

   ```
   repo=$(terraform -chdir=infra/live output -raw repository_url); sha=$(git rev-parse HEAD)
   aws ecr get-login-password | docker login --username AWS --password-stdin "${repo%%/*}"
   docker build -t "$repo:$sha" . && docker push "$repo:$sha"
   docker tag "$repo:$sha" "$repo:live" && docker push "$repo:live"
   ```

6. **Cut over** (within one hour, between :20 and :00, so no run is missed or doubled):
   1. Comment out both nfl-alt-lines lines in `crontab -e`.
   2. `uv run python -m nfl_alt_lines.go_live --bucket $(terraform -chdir=infra/live output -raw bucket_name)`
      copies data/ and the ledgers in (it asks for the DATABASE_URL; safe to re-run).
   3. `terraform -chdir=infra/live apply` (schedules on, the default)
   4. Watch the next runs: `aws logs tail /ecs/nfl-alt-lines-odds-logger --follow` (and `-report`). Done when
      the :15 email arrives with the laptop closed (Phase 4's "done when").

## Day to day

- **Deploy:** merge to main. CI pushes `:<sha>` and moves `live`; the next run uses it. Nothing in ECS changes.
- **Roll back:** point `live` at an earlier commit's image (ECR keeps five):

  ```
  manifest=$(aws ecr batch-get-image --repository-name nfl-alt-lines --image-ids imageTag=<old sha> --query 'images[0].imageManifest' --output text)
  aws ecr put-image --repository-name nfl-alt-lines --image-tag live --image-manifest "$manifest"
  ```

- **Rotate a key:** edit `.env`, re-run `infra/scripts/put_parameters.sh`; the next run reads the new value.
- **Pause everything:** `terraform -chdir=infra/live apply -var schedules_enabled=false` (and, to fall back
  to the laptop, re-enable its cron lines and set DATABASE_URL / STORAGE_BACKEND in `.env` to the cloud ones,
  or run go_live in reverse by hand).
- **A run by hand:** `aws ecs run-task` with the job's task definition, launch type FARGATE, one of the
  subnets, the jobs security group and `assignPublicIp=ENABLED` (what the schedule does).

## Why it is built this way

- **Fargate, not Lambda:** the image is ~900 MB (LightGBM, pandas, scikit-learn), slow to cold-start as a
  Lambda image and close to its limits; a one-minute task an hour costs cents.
- **A moving `live` tag, not a new task definition per deploy:** the deploy role then needs ECR push only (no
  ECS, IAM or `iam:PassRole`), and Terraform never fights CI over the task definition. Every image is still
  pinned by commit, and ECS records the digest each task ran.
- **Secrets in SSM, injected by ECS:** values never reach Terraform, its state, the task definition or the
  image; the execution role reads only `/nfl-alt-lines/*`, and each job gets only the secrets it uses.
- **Schedules on by default, off for the first apply:** while the laptop's cron also runs, every odds call
  (API credits) and email would happen twice; once live, a plain apply must not pause the jobs.
- **Two alarms per job:** a failed run (its "run finished" log line says `failed`) and a missed run (no
  "run finished" line in an hour: the task never started, was killed, or the schedule stopped). The second
  catches what the first cannot see, since a run that never logs has no line to match.
