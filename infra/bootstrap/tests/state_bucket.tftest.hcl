# Offline: a mock AWS provider, so no credentials, no cost and nothing created. Run: terraform test

mock_provider "aws" {
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "123456789012" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::nfl-alt-lines-tfstate-123456789012" }
  }
}

run "bucket_name_is_unique_per_account" {
  command = plan
  assert {
    condition     = aws_s3_bucket.state.bucket == "nfl-alt-lines-tfstate-123456789012"
    error_message = "The state bucket name must carry the account id: S3 names are global."
  }
}

run "state_is_versioned_private_and_encrypted" {
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "Without versioning a bad apply cannot be rolled back."
  }
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.state.block_public_acls,
      aws_s3_bucket_public_access_block.state.block_public_policy,
      aws_s3_bucket_public_access_block.state.ignore_public_acls,
      aws_s3_bucket_public_access_block.state.restrict_public_buckets,
    ])
    error_message = "State holds account details: every public-access block must be on."
  }
  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.state.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"
    error_message = "State must be encrypted at rest."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.state.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "Requests without TLS must be denied."
  }
}
