# Where the jobs keep their files (common/storage.py with STORAGE_BACKEND=s3) and the image they run.

resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
}

# Reports and the email mark are overwritten every hour; versioning keeps the previous copies for a while,
# so a bad run can be undone. Odds snapshots are never overwritten, so they cost nothing extra.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-overwritten-copies"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.old_versions_kept_for_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.data]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyAnythingButTls"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.data]
}

# Every image is tagged with its commit and can never be overwritten, except the one moving tag the tasks
# run (var.live_tag): a deploy points it at a new commit's image, and a rollback points it back.
resource "aws_ecr_repository" "jobs" {
  name                 = var.repository_name
  image_tag_mutability = "IMMUTABLE_WITH_EXCLUSION"

  image_tag_mutability_exclusion_filter {
    filter      = var.live_tag
    filter_type = "WILDCARD"
  }

  image_scanning_configuration {
    scan_on_push = true # basic scanning: free
  }
}

resource "aws_ecr_lifecycle_policy" "jobs" {
  repository = aws_ecr_repository.jobs.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the newest images only; each is about 900 MB at $0.10/GB-month"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.images_kept
      }
      action = { type = "expire" }
    }]
  })
}
