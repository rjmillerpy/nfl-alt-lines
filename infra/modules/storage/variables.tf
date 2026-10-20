variable "bucket_name" {
  description = "The data bucket (S3_BUCKET in the tasks)."
  type        = string
}

variable "repository_name" {
  description = "The ECR repository for the jobs' image."
  type        = string
}

variable "live_tag" {
  description = "The one mutable image tag: what the scheduled tasks run."
  type        = string
  default     = "live"
}

variable "images_kept" {
  description = "How many images ECR keeps; older ones are deleted. Enough to roll back a few deploys."
  type        = number
  default     = 5

  validation {
    condition     = var.images_kept >= 2
    error_message = "Keep at least two images, or a rollback has nothing to go back to."
  }
}

variable "old_versions_kept_for_days" {
  description = "Days an overwritten report or email mark stays recoverable."
  type        = number
  default     = 14
}
