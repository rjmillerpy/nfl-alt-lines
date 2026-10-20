terraform {
  required_version = ">= 1.16.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
  }

  # Roadmap item 16: state in the bucket infra/bootstrap/ creates, locked with S3's own conditional writes
  # (use_lockfile), so no DynamoDB table. The bucket name holds the account id, so it is passed at init:
  #   terraform init -backend-config="bucket=$(terraform -chdir=../bootstrap output -raw state_bucket)"
  backend "s3" {
    key          = "nfl-alt-lines/live.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = var.project
      managed_by = "terraform"
    }
  }
}
