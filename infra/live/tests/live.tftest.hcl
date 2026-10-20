# Offline tests: a mock AWS provider, so no credentials, no cost and nothing created. Each run pins a decision
# that would be expensive, unsafe or quietly wrong to lose. Run from infra/live/: terraform test

mock_provider "aws" {
  # Mocks invent random strings for computed values; ARN-shaped arguments are validated, so give the ones that
  # feed other resources a real shape.
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-2:123456789012:cluster/nfl-alt-lines" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn                  = "arn:aws:ecs:us-east-2:123456789012:task-definition/mock:1"
      arn_without_revision = "arn:aws:ecs:us-east-2:123456789012:task-definition/mock"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::nfl-alt-lines-data-123456789012", id = "nfl-alt-lines-data-123456789012" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-2:123456789012:repository/nfl-alt-lines"
      repository_url = "123456789012.dkr.ecr.us-east-2.amazonaws.com/nfl-alt-lines"
    }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-2:123456789012:nfl-alt-lines-alerts" }
  }

  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "123456789012" }
  }
  override_data {
    target = data.aws_iam_openid_connect_provider.github
    values = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
}

run "jobs_run_at_five_and_fifteen_past_every_hour" {
  assert {
    condition     = module.odds_logger.schedule.expression == "cron(5 * * * ? *)" && module.report.schedule.expression == "cron(15 * * * ? *)"
    error_message = "The logger runs at :05 and the report at :15, as the laptop's cron did."
  }
  assert {
    condition     = module.odds_logger.schedule.flexible_window == "OFF" && module.report.schedule.flexible_window == "OFF"
    error_message = "A flexible window could start the report before the logger has written."
  }
  assert {
    condition     = module.odds_logger.schedule.assign_public_ip && module.report.schedule.assign_public_ip
    error_message = "Without a NAT gateway a task with no public IP cannot reach ECR or the internet."
  }
}

# Runs set every variable they test, so a local terraform.tfvars (the deployment's own values) cannot change
# what is being tested.

run "enabling_turns_both_schedules_on" {
  command = plan
  variables { schedules_enabled = true }
  assert {
    condition     = module.odds_logger.schedule.state == "ENABLED" && module.report.schedule.state == "ENABLED"
    error_message = "Live: both jobs start on their schedules."
  }
}

run "pausing_turns_both_schedules_off" {
  command = plan
  variables { schedules_enabled = false }
  assert {
    condition     = module.odds_logger.schedule.state == "DISABLED" && module.report.schedule.state == "DISABLED"
    error_message = "schedules_enabled = false must stop both jobs (to pause, or while the laptop's cron runs)."
  }
}

run "each_job_gets_only_the_secrets_it_reads" {
  variables { optional_report_settings = ["BANKROLL", "REPORT_EMAIL_BETS_TO", "REPORT_EMAIL_TO", "REPORT_SUBJECT_PREFIX"] }
  # Written out on purpose: comparing against the stack's own locals would let a change pass silently.
  assert {
    condition     = toset([for s in module.odds_logger.container.secrets : s.name]) == toset(["DATABASE_URL", "ODDS_API_KEY"])
    error_message = "The logger needs the odds key and the database, and nothing to send email with."
  }
  assert {
    condition = toset([for s in module.report.container.secrets : s.name]) == toset(
      ["DATABASE_URL", "SMTP_APP_PASSWORD", "SMTP_USER", "BANKROLL", "REPORT_EMAIL_BETS_TO", "REPORT_EMAIL_TO", "REPORT_SUBJECT_PREFIX"]
    )
    error_message = "The report needs email and the database, and not the odds key."
  }
  assert {
    condition = alltrue([
      for s in concat(module.odds_logger.container.secrets, module.report.container.secrets) :
      s.valueFrom == "arn:aws:ssm:us-east-2:123456789012:parameter/nfl-alt-lines/${s.name}"
    ])
    error_message = "Every secret is a reference to this app's SSM parameter of the same name."
  }
}

run "secrets_never_travel_as_plain_environment" {
  assert {
    condition = alltrue([
      for e in concat(module.odds_logger.container.environment, module.report.container.environment) :
      !contains(["DATABASE_URL", "ODDS_API_KEY", "SMTP_APP_PASSWORD", "SMTP_USER", "REPORT_EMAIL_TO", "REPORT_EMAIL_BETS_TO"], e.name)
    ])
    error_message = "A secret or an email address in `environment` would be readable in the task definition."
  }
  assert {
    condition     = { for e in module.report.container.environment : e.name => e.value } == { STORAGE_BACKEND = "s3", S3_BUCKET = "nfl-alt-lines-data-123456789012" }
    error_message = "The jobs must keep their files in this stack's bucket."
  }
}

run "report_without_optional_settings" {
  command = plan
  variables { optional_report_settings = [] }
  assert {
    condition     = toset([for s in module.report.container.secrets : s.name]) == toset(["DATABASE_URL", "SMTP_APP_PASSWORD", "SMTP_USER"])
    error_message = "Optional settings that are not in SSM must not be referenced: the task would fail to start."
  }
}

run "rejects_an_unknown_optional_setting" {
  command = plan
  variables { optional_report_settings = ["ODDS_API_KEY"] }
  expect_failures = [var.optional_report_settings]
}

run "rejects_a_malformed_alert_email" {
  command = plan
  variables { alert_email = "not-an-address" }
  expect_failures = [var.alert_email]
}

run "nobody_is_emailed_without_an_address" {
  command = plan
  variables { alert_email = "" }
  assert {
    condition     = length(aws_sns_topic_subscription.alert_email) == 0
    error_message = "An empty alert_email must not create a subscription."
  }
}

run "every_job_alarms_on_a_failed_or_a_missed_run_and_emails" {
  variables { alert_email = "alerts@test.example" }
  assert {
    condition = (
      [module.odds_logger.alarm_name, module.report.alarm_name] == ["nfl-alt-lines-odds-logger-failed", "nfl-alt-lines-report-failed"]
    )
    error_message = "A run that finishes as failed must raise an alarm, for either job (roadmap item 20)."
  }
  assert {
    condition = (
      [module.odds_logger.missed_alarm_name, module.report.missed_alarm_name] == ["nfl-alt-lines-odds-logger-missed", "nfl-alt-lines-report-missed"]
    )
    error_message = "An hour with no finished run must raise an alarm, for either job (roadmap item 23)."
  }
  assert {
    condition     = aws_sns_topic_subscription.alert_email[0].endpoint == "alerts@test.example"
    error_message = "The alarm topic must email the alert address."
  }
}

run "tasks_are_sized_from_the_measured_run" {
  assert {
    condition     = module.report.size.memory >= 2048 && module.odds_logger.size.memory == 512
    error_message = "The report peaked at 830 MB on the laptop; 2 GB leaves room for nflverse refreshes."
  }
}

run "github_may_only_push_images_from_main" {
  assert {
    condition = jsondecode(aws_iam_role.github_deploy.assume_role_policy).Statement[0].Condition.StringEquals == {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
      "token.actions.githubusercontent.com:sub" = "repo:rjmillerpy/nfl-alt-lines-private:ref:refs/heads/main"
    }
    error_message = "Only a workflow on this repository's main branch may assume the deploy role."
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.github_deploy.policy).Statement :
      alltrue([for action in flatten([statement.Action]) : startswith(action, "ecr:")])
    ])
    error_message = "The deploy role pushes images only: no ECS, IAM or PassRole."
  }
}

run "scheduler_starts_only_these_jobs_and_passes_only_their_roles" {
  assert {
    condition     = jsondecode(aws_iam_role_policy.scheduler.policy).Statement[0].Condition.ArnEquals["ecs:cluster"] == "arn:aws:ecs:us-east-2:123456789012:cluster/nfl-alt-lines"
    error_message = "RunTask must be limited to this cluster."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.scheduler.policy).Statement[1].Condition.StringEquals["iam:PassedToService"] == "ecs-tasks.amazonaws.com"
    error_message = "PassRole without a service condition is a privilege escalation."
  }
}

run "each_job_writes_only_its_own_folders" {
  assert {
    condition     = jsondecode(aws_iam_role_policy.logger_task.policy).Statement[2].Resource == ["arn:aws:s3:::nfl-alt-lines-data-123456789012/raw/odds/*"]
    error_message = "The logger writes odds snapshots and nothing else."
  }
  assert {
    condition = toset(jsondecode(aws_iam_role_policy.report_task.policy).Statement[2].Resource) == toset([
      "arn:aws:s3:::nfl-alt-lines-data-123456789012/raw/nflverse/*",
      "arn:aws:s3:::nfl-alt-lines-data-123456789012/raw/weather/*",
      "arn:aws:s3:::nfl-alt-lines-data-123456789012/reports/*",
    ])
    error_message = "The report writes its downloads and reports, and cannot overwrite odds snapshots."
  }
}

run "execution_role_is_narrower_than_the_managed_policy" {
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.execution.policy).Statement :
      statement.Resource != "*" || statement.Action == "ecr:GetAuthorizationToken"
    ])
    error_message = "Only the ECR login may be account-wide; image, logs and secrets are this app's alone."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.execution.policy).Statement[3].Resource == "arn:aws:ssm:us-east-2:123456789012:parameter/nfl-alt-lines/*"
    error_message = "The execution role may read this app's parameters only."
  }
}

run "network_allows_only_the_outbound_ports_in_use" {
  assert {
    condition     = module.network.egress_ports == toset([443, 465, 5432])
    error_message = "HTTPS, Gmail's SMTPS and Postgres; nothing else out, and no inbound rule at all."
  }
}

run "only_the_live_tag_can_move" {
  assert {
    condition     = module.storage.mutable_tags == ["live"]
    error_message = "Commit-tagged images must stay immutable, so every deploy can be traced and rolled back."
  }
}

run "container_insights_stay_off" {
  assert {
    condition     = one([for s in aws_ecs_cluster.jobs.setting : s.value if s.name == "containerInsights"]) == "disabled"
    error_message = "Container Insights bills per task-hour; the jobs log what they did."
  }
}
