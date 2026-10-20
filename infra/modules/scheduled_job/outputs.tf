output "task_definition_arn" {
  value = aws_ecs_task_definition.job.arn
}

output "task_definition_family_arn" {
  description = "The family's ARN without the revision, for IAM resources that cover every revision."
  value       = aws_ecs_task_definition.job.arn_without_revision
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.job.name
}

output "schedule_name" {
  value = aws_scheduler_schedule.job.name
}

output "container" {
  description = "The container definition: environment values and the secrets' parameter ARNs, never secret values."
  value       = local.container
}

output "schedule" {
  value = {
    expression       = aws_scheduler_schedule.job.schedule_expression
    state            = aws_scheduler_schedule.job.state
    flexible_window  = aws_scheduler_schedule.job.flexible_time_window[0].mode
    assign_public_ip = aws_scheduler_schedule.job.target[0].ecs_parameters[0].network_configuration[0].assign_public_ip
  }
}

output "size" {
  value = { cpu = tonumber(aws_ecs_task_definition.job.cpu), memory = tonumber(aws_ecs_task_definition.job.memory) }
}

output "alarm_name" {
  description = "The failure alarm, or null when the job has no failure pattern."
  value       = one(aws_cloudwatch_metric_alarm.failures[*].alarm_name)
}

output "missed_alarm_name" {
  description = "The missed-run alarm, or null when the job has no missed-run pattern."
  value       = one(aws_cloudwatch_metric_alarm.missed[*].alarm_name)
}

output "missed_alarm" {
  value = one([for alarm in aws_cloudwatch_metric_alarm.missed : {
    period             = alarm.period
    comparison         = alarm.comparison_operator
    treat_missing_data = alarm.treat_missing_data
    actions_enabled    = alarm.actions_enabled
  }])
}
