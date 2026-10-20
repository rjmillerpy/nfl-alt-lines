output "bucket_name" {
  value = aws_s3_bucket.data.id
}

output "bucket_arn" {
  value = aws_s3_bucket.data.arn
}

output "repository_arn" {
  value = aws_ecr_repository.jobs.arn
}

output "repository_url" {
  value = aws_ecr_repository.jobs.repository_url
}

output "mutable_tags" {
  description = "The only tags that may move to another image."
  value       = [for f in aws_ecr_repository.jobs.image_tag_mutability_exclusion_filter : f.filter]
}
