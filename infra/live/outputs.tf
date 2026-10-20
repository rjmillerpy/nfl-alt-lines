output "repository_url" {
  description = "Where images go: <url>:<commit sha>, and the `live` tag the tasks run."
  value       = module.storage.repository_url
}

output "bucket_name" {
  description = "S3_BUCKET for copying the laptop's files in (nfl_alt_lines.go_live)."
  value       = module.storage.bucket_name
}

output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable (not a secret: an ARN grants nothing alone)."
  value       = aws_iam_role.github_deploy.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.jobs.name
}

output "log_groups" {
  value = [module.odds_logger.log_group_name, module.report.log_group_name]
}
