variable "project" {
  description = "Name prefix, and the CloudWatch namespace of the failure metric."
  type        = string
}

variable "name" {
  description = "The job: the container's name and the second half of every resource name."
  type        = string
}

variable "region" {
  type = string
}

variable "image" {
  description = "Image URI with its tag."
  type        = string
}

variable "command" {
  description = "Arguments after the image's `nfl-alt-lines` entry point, e.g. [\"report\"]."
  type        = list(string)
}

variable "schedule" {
  description = "EventBridge Scheduler expression, e.g. cron(5 * * * ? *)."
  type        = string

  validation {
    condition     = can(regex("^(cron|rate)\\(.+\\)$", var.schedule))
    error_message = "Use a cron(...) or rate(...) expression."
  }
}

variable "enabled" {
  description = "Whether the schedule starts the job; a disabled one keeps everything else in place."
  type        = bool
  default     = true
}

variable "cpu" {
  description = "Fargate CPU units (1024 = one vCPU)."
  type        = number
}

variable "memory" {
  description = "Fargate memory in MiB; must be a valid size for the CPU chosen."
  type        = number
}

variable "environment" {
  description = "Settings that are not secret, by environment variable name."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "SSM parameter ARNs, by the environment variable name the job reads."
  type        = map(string)
  default     = {}
}

variable "execution_role_arn" {
  description = "Used by ECS itself: pull the image, write logs, read the secrets."
  type        = string
}

variable "task_role_arn" {
  description = "What the job's own code may do in AWS."
  type        = string
}

variable "cluster_arn" {
  type = string
}

variable "scheduler_role_arn" {
  description = "The role EventBridge Scheduler uses to start the task."
  type        = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "retry_attempts" {
  description = "How often the scheduler retries a task ECS could not start."
  type        = number
  default     = 2
}

variable "retry_window_seconds" {
  description = "How late a retried start may be; the report follows the logger by ten minutes."
  type        = number
  default     = 600

  validation {
    condition     = var.retry_window_seconds >= 60 && var.retry_window_seconds <= 3600
    error_message = "Between one minute and one hour, so a retry never runs into the next hour's run."
  }
}

variable "failure_pattern" {
  description = "CloudWatch Logs filter pattern for a failed run; null for no alarm."
  type        = string
  default     = null
}

variable "alarm_period_seconds" {
  type    = number
  default = 3600
}

variable "alarm_topic_arns" {
  description = "SNS topics the failure alarm notifies."
  type        = list(string)
  default     = []
}

variable "missed_run_pattern" {
  description = "CloudWatch Logs filter pattern for a finished run (any outcome); null for no missed-run alarm."
  type        = string
  default     = null
}

variable "missed_run_window_seconds" {
  description = "Alarm when a whole window of this length passes without a finished run."
  type        = number
  default     = 3600

  validation {
    condition     = var.missed_run_window_seconds >= 3600 && var.missed_run_window_seconds % 3600 == 0
    error_message = "Whole hours: the jobs run hourly, so a shorter window would alarm between runs."
  }
}
