variable "name" {
  description = "Name prefix for the network's resources."
  type        = string
}

variable "cidr" {
  description = "The VPC's address range; each subnet takes a /24 of it."
  type        = string
  default     = "10.42.0.0/16"
}

variable "availability_zones" {
  description = "One public subnet per zone; two, so a zone outage does not stop the jobs."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "Use at least two availability zones."
  }
}

variable "extra_egress_ports" {
  description = "Outbound ports besides 443, by purpose, e.g. SMTP submission (587) and Postgres (5432)."
  type        = map(number)
  default     = {}
}
