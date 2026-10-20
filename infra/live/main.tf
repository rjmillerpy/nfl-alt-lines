# The two hourly jobs on AWS (roadmap items 16-20): the odds logger at :05 and the report at :15, each a
# Fargate task started by EventBridge Scheduler, with their files in S3 and their ledgers in Postgres.
#
# Monthly cost, us-east-2, about $1-2 in all: Fargate for roughly a minute per run (~$0.70), public IPv4
# while tasks run (~$0.15), ECR for five ~900 MB images (~$0.45), S3 for ~400 MB (~$0.01), CloudWatch logs
# and one alarm (~$0.10, inside the free tier at first). SSM standard parameters, the VPC, IAM and the
# scheduler are free. The database is not created here: DATABASE_URL points at a free Neon or Supabase
# Postgres (or an RDS instance, about $15-20/month more).

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  ssm_prefix = "/${var.project}"
  image      = "${module.storage.repository_url}:${var.image_tag}"

  # Read by nfl_alt_lines/common/settings.py. S3_PREFIX stays empty: the bucket belongs to this app.
  storage_environment = {
    STORAGE_BACKEND = "s3"
    S3_BUCKET       = module.storage.bucket_name
  }

  # nfl_alt_lines/run_status.py logs one JSON "run finished" line per run, with its outcome.
  finished_run = "{ $.event = \"run finished\" }"
  failed_run   = "{ ($.event = \"run finished\") && ($.outcome = \"failed\") }"

  # Each job gets only the secrets it reads. DATABASE_URL is required for both: without it the jobs would
  # fall back to SQLite on the task's disk, which is thrown away when the task stops.
  logger_secret_names = ["DATABASE_URL", "ODDS_API_KEY"]
  report_secret_names = concat(["DATABASE_URL", "SMTP_APP_PASSWORD", "SMTP_USER"], sort(var.optional_report_settings))

  parameter_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}"
}

module "network" {
  source = "../modules/network"

  name               = var.project
  availability_zones = var.availability_zones
  extra_egress_ports = {
    "SMTPS: the report email through Gmail" = 465
    "Postgres: the ledger database"         = 5432
  }
}

module "storage" {
  source = "../modules/storage"

  bucket_name     = "${var.project}-data-${local.account_id}"
  repository_name = var.project
  live_tag        = var.image_tag
}

resource "aws_ecs_cluster" "jobs" {
  name = var.project

  setting {
    name  = "containerInsights"
    value = "disabled" # billed per metric; the jobs log what they did
  }
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "alert_email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

module "odds_logger" {
  source = "../modules/scheduled_job"

  enabled  = var.schedules_enabled
  project  = var.project
  name     = "odds-logger"
  region   = var.region
  image    = local.image
  command  = ["odds-logger"]
  schedule = "cron(5 * * * ? *)"
  cpu      = 256 # a quarter vCPU: a few API calls and a JSON write
  memory   = 512

  environment = local.storage_environment
  secrets     = { for name in local.logger_secret_names : name => "${local.parameter_arn}/${name}" }

  failure_pattern    = local.failed_run
  missed_run_pattern = local.finished_run
  alarm_topic_arns   = [aws_sns_topic.alerts.arn]

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.logger_task.arn
  scheduler_role_arn = aws_iam_role.scheduler.arn
  cluster_arn        = aws_ecs_cluster.jobs.arn
  subnet_ids         = module.network.subnet_ids
  security_group_id  = module.network.security_group_id
}

module "report" {
  source = "../modules/scheduled_job"

  enabled  = var.schedules_enabled
  project  = var.project
  name     = "report"
  region   = var.region
  image    = local.image
  command  = ["report"]
  schedule = "cron(15 * * * ? *)"
  cpu      = 512 # measured on the laptop: about 15 s on one core and 830 MB at peak, nflverse refresh included
  memory   = 2048

  environment = local.storage_environment
  secrets     = { for name in local.report_secret_names : name => "${local.parameter_arn}/${name}" }

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.report_task.arn
  scheduler_role_arn = aws_iam_role.scheduler.arn
  cluster_arn        = aws_ecs_cluster.jobs.arn
  subnet_ids         = module.network.subnet_ids
  security_group_id  = module.network.security_group_id

  failure_pattern    = local.failed_run
  missed_run_pattern = local.finished_run
  alarm_topic_arns   = [aws_sns_topic.alerts.arn]
}
