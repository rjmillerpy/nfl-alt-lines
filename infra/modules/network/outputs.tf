output "subnet_ids" {
  value = [for subnet in aws_subnet.public : subnet.id]
}

output "security_group_id" {
  value = aws_security_group.jobs.id
}

output "egress_ports" {
  description = "Every port the jobs may connect out to."
  value       = toset(concat([aws_vpc_security_group_egress_rule.jobs_https.from_port], [for rule in aws_vpc_security_group_egress_rule.jobs_extra : rule.from_port]))
}
