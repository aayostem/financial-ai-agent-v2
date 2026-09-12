# =============================================================================
# iam.tf
# Two IRSA roles, scoped narrowly to what each actually needs -- not a
# shared "do everything" role, but also not a separate module per role.
# =============================================================================

data "aws_caller_identity" "current" {}

# ── External Secrets Operator: reads the Secrets Manager secret this stack
#    creates, writes it into a Kubernetes Secret in-cluster. This is what
#    replaces committing base64 secrets to git, per your own migration plan.
module "irsa_external_secrets" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name = "${var.project_name}-external-secrets"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["external-secrets:external-secrets"]
    }
  }
}

resource "aws_iam_role_policy" "external_secrets_read" {
  name = "secrets-manager-read"
  role = module.irsa_external_secrets.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = [aws_secretsmanager_secret.app.arn]
    }]
  })
}

# ── App pods (api + agent): read/write the S3 buckets they actually use.
module "irsa_app" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name = "${var.project_name}-app"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["financial-ai:${var.project_name}-api", "financial-ai:${var.project_name}-agent"]
    }
  }
}

resource "aws_iam_role_policy" "app_s3_access" {
  name = "s3-access"
  role = module.irsa_app.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
      Resource = flatten([
        for b in aws_s3_bucket.this : [b.arn, "${b.arn}/*"]
      ])
    }]
  })
}
