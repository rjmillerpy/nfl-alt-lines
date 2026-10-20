# One scheduled job (roadmap items 17, 18, 20 and 23): a Fargate task definition that runs `nfl-alt-lines
# <command>`, an EventBridge Scheduler schedule that starts it, its CloudWatch log group, and optionally two
# alarms from its logs: one on a failed run, one on a missed run (no finished run in a whole window).
#
# A Fargate task, not Lambda: the image is about 900 MB (LightGBM, pandas, scikit-learn), which makes a
# Lambda image slow to start and close to its limits; a task that runs for a minute an hour costs cents.

locals {
  family   = "${var.project}-${var.name}"
  log_name = "/ecs/${local.family}"

  container = {
    name      = var.name
    image     = var.image
    essential = true
    command   = var.command

    # Settings that are not secret go in plainly; every secret is a reference to an SSM parameter, which
    # ECS reads at start with the execution role and hands to the process as an environment variable
    # (rule 17). No secret value is ever in Terraform, its state or the task definition.
    environment = [for name in sort(keys(var.environment)) : { name = name, value = var.environment[name] }]
    secrets     = [for name in sort(keys(var.secrets)) : { name = name, valueFrom = var.secrets[name] }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.job.name
        awslogs-region        = var.region
        awslogs-stream-prefix = var.name
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "job" {
  name              = local.log_name
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "job" {
  family                   = local.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  container_definitions    = jsonencode([local.container])

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # what the CI docker job builds
  }
}

resource "aws_scheduler_schedule" "job" {
  name                         = local.family
  description                  = "Runs `nfl-alt-lines ${join(" ", var.command)}` on Fargate."
  schedule_expression          = var.schedule
  schedule_expression_timezone = "UTC" # the jobs run every hour, so the zone only matters for reading the logs
  state                        = var.enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF" # the report reads what the logger wrote ten minutes earlier; keep the times exact
  }

  target {
    arn      = var.cluster_arn
    role_arn = var.scheduler_role_arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.job.arn
      launch_type         = "FARGATE"
      platform_version    = "LATEST"
      task_count          = 1

      network_configuration {
        assign_public_ip = true # no NAT gateway: the task reaches the internet with its own public IP
        subnets          = var.subnet_ids
        security_groups  = [var.security_group_id]
      }
    }

    # If ECS cannot start the task (a capacity blip), try again for a few minutes. A repeated run is
    # harmless: logged prices are unique per market update (roadmap item 13) and the report is rebuilt.
    retry_policy {
      maximum_event_age_in_seconds = var.retry_window_seconds
      maximum_retry_attempts       = var.retry_attempts
    }
  }
}

resource "aws_cloudwatch_log_metric_filter" "failures" {
  count = var.failure_pattern == null ? 0 : 1

  name           = "${local.family}-failures"
  log_group_name = aws_cloudwatch_log_group.job.name
  pattern        = var.failure_pattern

  metric_transformation {
    name          = "${var.name}-failures"
    namespace     = var.project
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "failures" {
  count = var.failure_pattern == null ? 0 : 1

  alarm_name          = "${local.family}-failed"
  alarm_description   = "The ${var.name} job logged a failure (${var.failure_pattern}). Its log group: ${local.log_name}."
  namespace           = var.project
  metric_name         = aws_cloudwatch_log_metric_filter.failures[0].metric_transformation[0].name
  statistic           = "Sum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching" # no log line at all is a missed run: Phase 5's alarm
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns
}

# A missed run: the task never started (a bad image, a missing secret, the scheduler), was killed before it
# could log (out of memory), or the schedule stopped. The failure alarm cannot see any of these, since there
# is no line to match; here no line is the alarm.
resource "aws_cloudwatch_log_metric_filter" "runs" {
  count = var.missed_run_pattern == null ? 0 : 1

  name           = "${local.family}-runs"
  log_group_name = aws_cloudwatch_log_group.job.name
  pattern        = var.missed_run_pattern

  metric_transformation {
    name      = "${var.name}-runs"
    namespace = var.project
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "missed" {
  count = var.missed_run_pattern == null ? 0 : 1

  alarm_name          = "${local.family}-missed"
  alarm_description   = "No ${var.name} run finished in ${var.missed_run_window_seconds / 3600} hour(s). Its log group: ${local.log_name}."
  namespace           = var.project
  metric_name         = aws_cloudwatch_log_metric_filter.runs[0].metric_transformation[0].name
  statistic           = "Sum"
  period              = var.missed_run_window_seconds
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching" # no finished run in the window is exactly what this alarm is for
  actions_enabled     = var.enabled # a paused schedule misses every run on purpose
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns
}
