variable "region" {
  description = "AWS region; us-east-2, where the rest of this account's resources live."
  type        = string
  default     = "us-east-2"
}

variable "project" {
  description = "Name prefix and the `project` tag on every resource."
  type        = string
  default     = "nfl-alt-lines"
}
