# Offline tests of the module on its own (mock provider). Run from infra/modules/scheduled_job/: terraform test

mock_provider "aws" {
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:us-east-2:123456789012:task-definition/test-job:1" }
  }
}

variables {
  project            = "test"
  name               = "job"
  region             = "us-east-2"
  image              = "123456789012.dkr.ecr.us-east-2.amazonaws.com/test:live"
  command            = ["report"]
  schedule           = "cron(15 * * * ? *)"
  cpu                = 256
  memory             = 512
  execution_role_arn = "arn:aws:iam::123456789012:role/execution"
  task_role_arn      = "arn:aws:iam::123456789012:role/task"
  scheduler_role_arn = "arn:aws:iam::123456789012:role/scheduler"
  cluster_arn        = "arn:aws:ecs:us-east-2:123456789012:cluster/test"
  subnet_ids         = ["subnet-1", "subnet-2"]
  security_group_id  = "sg-1"
  secrets            = { DATABASE_URL = "arn:aws:ssm:us-east-2:123456789012:parameter/test/DATABASE_URL" }
}

run "no_failure_pattern_means_no_alarm" {
  command = plan
  assert {
    condition     = output.alarm_name == null && length(aws_cloudwatch_log_metric_filter.failures) == 0
    error_message = "Without a failure pattern there is nothing to alarm on."
  }
}

run "logs_go_to_the_jobs_own_group" {
  command = plan
  assert {
    condition     = aws_cloudwatch_log_group.job.name == "/ecs/test-job" && aws_cloudwatch_log_group.job.retention_in_days == 30
    error_message = "One log group per job, kept 30 days (CloudWatch keeps logs forever by default, and bills for it)."
  }
}

run "secrets_are_references_not_values" {
  command = plan
  assert {
    condition     = jsondecode(aws_ecs_task_definition.job.container_definitions)[0].secrets == [{ name = "DATABASE_URL", valueFrom = "arn:aws:ssm:us-east-2:123456789012:parameter/test/DATABASE_URL" }]
    error_message = "A secret reaches the container as an SSM reference that ECS resolves at start."
  }
}

run "a_failure_pattern_alarms_on_the_first_failure" {
  command = plan
  variables { failure_pattern = "\"FAILED\"" }
  assert {
    condition     = aws_cloudwatch_metric_alarm.failures[0].threshold == 1 && aws_cloudwatch_metric_alarm.failures[0].treat_missing_data == "notBreaching"
    error_message = "One failed run is enough to alarm; an hour with no logs is not a failure."
  }
}

run "no_missed_run_pattern_means_no_missed_alarm" {
  command = plan
  assert {
    condition     = output.missed_alarm_name == null && length(aws_cloudwatch_log_metric_filter.runs) == 0
    error_message = "Without a pattern for a finished run there is nothing to count."
  }
}

run "an_hour_without_a_finished_run_alarms" {
  command = plan
  variables { missed_run_pattern = "{ $.event = \"run finished\" }" }
  assert {
    condition = output.missed_alarm == {
      period = 3600, comparison = "LessThanThreshold", treat_missing_data = "breaching", actions_enabled = true
    }
    error_message = "No logs at all must count as a missed run, within the hour."
  }
}

run "a_paused_schedule_does_not_page_about_missed_runs" {
  command = plan
  variables {
    missed_run_pattern = "{ $.event = \"run finished\" }"
    enabled            = false
  }
  assert {
    condition     = output.missed_alarm.actions_enabled == false
    error_message = "Paused on purpose: every run is missed, and nobody should be emailed about it."
  }
}

run "rejects_a_missed_run_window_shorter_than_the_schedule" {
  command = plan
  variables { missed_run_window_seconds = 1800 }
  expect_failures = [var.missed_run_window_seconds]
}

run "rejects_a_schedule_that_is_not_cron_or_rate" {
  command = plan
  variables { schedule = "every hour" }
  expect_failures = [var.schedule]
}

run "rejects_a_retry_window_that_reaches_the_next_run" {
  command = plan
  variables { retry_window_seconds = 7200 }
  expect_failures = [var.retry_window_seconds]
}
