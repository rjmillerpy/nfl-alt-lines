variable "region" {
  description = "AWS region; us-east-2, where the rest of this account's resources live."
  type        = string
  default     = "us-east-2"
}

variable "project" {
  description = "Name prefix, SSM parameter folder and `project` tag."
  type        = string
  default     = "nfl-alt-lines"
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-2a", "us-east-2b"]
}

variable "github_repository" {
  description = "owner/name of the repository whose main branch may push images."
  type        = string
  default     = "rjmillerpy/nfl-alt-lines-private"
}

variable "deploy_branch" {
  type    = string
  default = "main"
}

variable "schedules_enabled" {
  description = "Start the jobs on their schedules (live since 2026-10-17). Turn off to pause, or while the laptop's cron runs: both at once would double every odds call and email."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "The image tag the tasks run; the deploy workflow moves it to each new commit's image."
  type        = string
  default     = "live"
}

variable "optional_report_settings" {
  description = "Report settings kept in SSM only when set in .env (scripts/put_parameters.sh prints which)."
  type        = set(string)
  default     = ["BANKROLL", "REPORT_EMAIL_BETS_TO", "REPORT_EMAIL_TO", "REPORT_SUBJECT_PREFIX"]

  validation {
    condition     = length(setsubtract(var.optional_report_settings, ["BANKROLL", "REPORT_EMAIL_BETS_TO", "REPORT_EMAIL_TO", "REPORT_SUBJECT_PREFIX"])) == 0
    error_message = "Only BANKROLL, REPORT_EMAIL_BETS_TO, REPORT_EMAIL_TO and REPORT_SUBJECT_PREFIX are optional report settings."
  }
}

variable "alert_email" {
  description = "Where the failure alarm emails; AWS sends a confirmation link first. Empty: nobody is emailed. Keep it in a gitignored *.tfvars, not in git."
  type        = string
  default     = ""

  validation {
    condition     = var.alert_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address or empty."
  }
}
