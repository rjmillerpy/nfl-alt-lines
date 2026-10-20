# Least privilege, one role per job of work:
#  - execution role: ECS itself pulls the image, writes the logs and reads the secrets. The app never has it.
#  - one task role per job: what that job's own code may do. Both read the whole bucket; each writes only the
#    folders it owns (the paths in nfl_alt_lines: odds/snapshots.py, nflverse.py, weather.py, page.py).
#  - scheduler role: EventBridge Scheduler starts these two tasks in this cluster, and nothing else.
#  - GitHub deploy role (OIDC): main pushes images to this one repository, and nothing else.
# Policies are jsonencode()d rather than aws_iam_policy_document, so the offline tests can read them, and
# each is its own local: in one map, every role would depend on every policy, which is a cycle.

locals {
  ecs_tasks_service = "ecs-tasks.amazonaws.com"
  bucket_arn        = module.storage.bucket_arn

  job_writes = {
    logger = ["raw/odds/*"]
    report = ["raw/nflverse/*", "raw/weather/*", "reports/*"]
  }

  github_oidc_host = "token.actions.githubusercontent.com"
  sts_audience     = "sts.amazonaws.com"
  deploy_subject   = "repo:${var.github_repository}:ref:refs/heads/${var.deploy_branch}"
  one_hour_seconds = 3600

  log_group_arns = [
    for job in [module.odds_logger, module.report] :
    "arn:aws:logs:${var.region}:${local.account_id}:log-group:${job.log_group_name}:*"
  ]

  # Only callers in this account (confused-deputy protection).
  this_account = { StringEquals = { "aws:SourceAccount" = local.account_id } }

  policy_ecs_tasks_trust = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = local.ecs_tasks_service }
      Action    = "sts:AssumeRole"
      Condition = local.this_account
    }]
  }

  # Written out instead of AmazonECSTaskExecutionRolePolicy, which may pull any image and write any log.
  policy_execution = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "EcrLoginIsAccountWide", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Sid      = "PullThisAppsImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = module.storage.repository_arn
      },
      {
        Sid      = "WriteThisAppsLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = local.log_group_arns
      },
      {
        Sid      = "ReadThisAppsSecrets" # SecureStrings under the default aws/ssm key need no kms grant
        Effect   = "Allow"
        Action   = "ssm:GetParameters"
        Resource = "${local.parameter_arn}/*"
      },
    ]
  }

  policy_scheduler_trust = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = local.this_account
    }]
  }

  policy_scheduler = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RunTheTwoJobsInThisCluster"
        Effect    = "Allow"
        Action    = "ecs:RunTask"
        Resource  = [for job in [module.odds_logger, module.report] : "${job.task_definition_family_arn}:*"]
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.jobs.arn } }
      },
      {
        # A task names its roles, so starting it means passing them: only these three, only to ECS tasks.
        # Without the condition, PassRole is a privilege escalation.
        Sid       = "PassOnlyTheJobsRoles"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = [aws_iam_role.execution.arn, aws_iam_role.logger_task.arn, aws_iam_role.report_task.arn]
        Condition = { StringEquals = { "iam:PassedToService" = local.ecs_tasks_service } }
      },
    ]
  }

  policy_github_trust = {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "GitHubActionsOnTheDeployBranch"
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      # StringEquals, not StringLike: pull requests, other branches, tags and forks have another `sub`.
      Condition = {
        StringEquals = {
          "${local.github_oidc_host}:aud" = local.sts_audience
          "${local.github_oidc_host}:sub" = local.deploy_subject
        }
      }
    }]
  }

  # A deploy is an image push and a retag: the tasks run the `live` tag, so nothing in ECS changes and the
  # role needs no ECS, IAM or PassRole permission at all.
  policy_github_deploy = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "EcrLoginIsAccountWide", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Sid    = "PushAndRetagInThisRepository"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
        ]
        Resource = module.storage.repository_arn
      },
    ]
  }

  # Both jobs read the whole bucket and write only their own folders.
  job_storage = {
    for job, writes in local.job_writes : job => {
      Version = "2012-10-17"
      Statement = [
        { Sid = "ListTheBucket", Effect = "Allow", Action = "s3:ListBucket", Resource = local.bucket_arn },
        { Sid = "ReadAnything", Effect = "Allow", Action = "s3:GetObject", Resource = "${local.bucket_arn}/*" },
        {
          Sid      = "WriteOwnFolders"
          Effect   = "Allow"
          Action   = "s3:PutObject"
          Resource = [for path in writes : "${local.bucket_arn}/${path}"]
        },
      ]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.project}-task-execution"
  assume_role_policy = jsonencode(local.policy_ecs_tasks_trust)
}

resource "aws_iam_role_policy" "execution" {
  name   = "pull-log-read-secrets"
  role   = aws_iam_role.execution.id
  policy = jsonencode(local.policy_execution)
}

resource "aws_iam_role" "logger_task" {
  name               = "${var.project}-odds-logger-task"
  assume_role_policy = jsonencode(local.policy_ecs_tasks_trust)
}

resource "aws_iam_role_policy" "logger_task" {
  name   = "odds-snapshots"
  role   = aws_iam_role.logger_task.id
  policy = jsonencode(local.job_storage.logger)
}

resource "aws_iam_role" "report_task" {
  name               = "${var.project}-report-task"
  assume_role_policy = jsonencode(local.policy_ecs_tasks_trust)
}

resource "aws_iam_role_policy" "report_task" {
  name   = "downloads-and-reports"
  role   = aws_iam_role.report_task.id
  policy = jsonencode(local.job_storage.report)
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler"
  assume_role_policy = jsonencode(local.policy_scheduler_trust)
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "run-the-jobs"
  role   = aws_iam_role.scheduler.id
  policy = jsonencode(local.policy_scheduler)
}

# Roadmap item 19: GitHub Actions gets short-lived credentials through OIDC, so no access key is stored in
# the repository. The account's OIDC provider already exists (one per account; rad-shift created it).
data "aws_iam_openid_connect_provider" "github" {
  url = "https://${local.github_oidc_host}"
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${var.project}-github-deploy"
  description          = "Assumed by GitHub Actions on ${var.github_repository}@${var.deploy_branch} to push images."
  max_session_duration = local.one_hour_seconds
  assume_role_policy   = jsonencode(local.policy_github_trust)
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "push-images"
  role   = aws_iam_role.github_deploy.id
  policy = jsonencode(local.policy_github_deploy)
}
