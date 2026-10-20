output "state_bucket" {
  description = "Put this in infra/live/backend.hcl as `bucket`."
  value       = aws_s3_bucket.state.id
}
