# A VPC with public subnets and no NAT gateway: the tasks get a public IP for the few minutes they run, so
# they can reach ECR, S3, The Odds API, nflverse on GitHub, Gmail's SMTP server and the database directly.
# Nothing listens, so the security group allows no inbound traffic at all. A NAT gateway would cost about
# $32/month on its own; the public IPs cost $0.005 an hour each while a task runs (cents a month).

locals {
  subnets = {
    for index, az in var.availability_zones :
    az => cidrsubnet(var.cidr, 8, index)
  }
}

resource "aws_vpc" "main" {
  cidr_block           = var.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = var.name }
}

resource "aws_subnet" "public" {
  for_each = local.subnets

  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false # the schedules ask for a public IP per task; nothing else gets one

  tags = { Name = "${var.name}-public-${each.key}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# The VPC's default security group allows traffic between its members; take every rule away from it so
# nothing can end up using it by accident.
resource "aws_default_security_group" "locked" {
  vpc_id = aws_vpc.main.id
}

resource "aws_security_group" "jobs" {
  name        = "${var.name}-jobs"
  description = "Scheduled jobs: outbound only, nothing listens"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "jobs_https" {
  security_group_id = aws_security_group.jobs.id
  description       = "HTTPS: ECR, S3, CloudWatch Logs, SSM, The Odds API, nflverse, weather"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "jobs_extra" {
  for_each = var.extra_egress_ports

  security_group_id = aws_security_group.jobs.id
  description       = each.key
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
  cidr_ipv4         = "0.0.0.0/0"
}
