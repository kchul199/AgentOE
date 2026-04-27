# ──────────────────────────────────────────────────────────────────────────
# GitHub Actions OIDC → AWS IAM
#
# 두 개의 role 을 만든다:
#   1) ecr_push_role     — 이미지 빌드/푸시 워크플로 전용 (ECR 만)
#   2) eks_deploy_role   — Helm 배포 워크플로 전용 (EKS describe + assume mapping role)
#
# trust 는 repo + ref 패턴으로 좁힌다. 예) "repo:org/agentoe:ref:refs/heads/main"
# 또는 "repo:org/agentoe:environment:staging" (GitHub Environment 게이트와 연동).
# ──────────────────────────────────────────────────────────────────────────

# OIDC provider — 한 계정에 단 하나만 존재. 이미 있으면 import 권장.
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  # GitHub 의 Actions OIDC root CA thumbprint — TLS 1.2 root.
  thumbprint_list = var.oidc_thumbprints

  tags = merge(var.tags, { Name = "github-actions-oidc" })
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn
  oidc_provider_url = "token.actions.githubusercontent.com"

  # repo:org/repo 부분 공통 prefix
  sub_repo_prefix = "repo:${var.github_org}/${var.github_repo}"
}

# ─── ECR push role ──────────────────────────────────────────────────────
data "aws_iam_policy_document" "ecr_push_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_provider_url}:sub"
      # main + tag (vX.Y.Z) push 만 허용. PR 은 차단.
      values = [
        "${local.sub_repo_prefix}:ref:refs/heads/main",
        "${local.sub_repo_prefix}:ref:refs/tags/v*",
        "${local.sub_repo_prefix}:environment:staging",
        "${local.sub_repo_prefix}:environment:production",
      ]
    }
  }
}

data "aws_iam_policy_document" "ecr_push" {
  statement {
    sid    = "EcrAuth"
    effect = "Allow"
    # GetAuthorizationToken 은 리소스 레벨 제한 불가 (AWS 한계).
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPushPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    # 우리 prefix 의 repo 만 — 다른 팀 repo 침범 방지.
    resources = [
      for repo in var.ecr_repository_names :
      "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${repo}"
    ]
  }
}

resource "aws_iam_role" "ecr_push" {
  name               = "${var.name_prefix}-gha-ecr-push"
  assume_role_policy = data.aws_iam_policy_document.ecr_push_trust.json
  description        = "GitHub Actions — ECR push only (build & publish images)"
  max_session_duration = 3600

  tags = var.tags
}

resource "aws_iam_role_policy" "ecr_push" {
  name   = "ecr-push"
  role   = aws_iam_role.ecr_push.id
  policy = data.aws_iam_policy_document.ecr_push.json
}

# ─── EKS deploy role ────────────────────────────────────────────────────
data "aws_iam_policy_document" "eks_deploy_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_provider_url}:sub"
      values = [
        # GitHub Environment 기반 — 승인 게이트와 연동.
        "${local.sub_repo_prefix}:environment:staging",
        "${local.sub_repo_prefix}:environment:production",
      ]
    }
  }
}

data "aws_iam_policy_document" "eks_deploy" {
  statement {
    sid    = "EksDescribe"
    effect = "Allow"
    actions = [
      "eks:DescribeCluster",
      "eks:ListClusters",
    ]
    # 같은 region 의 명시 클러스터만.
    resources = [
      for c in var.eks_cluster_names :
      "arn:aws:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${c}"
    ]
  }

  # secrets-manager 일부 RO — render-values.sh 가 비-시크릿 metadata 만 읽도록 한정.
  # (시크릿 값 자체는 ESO 가 in-cluster 로 동기화 — CI 가 원본 평문에 접근할 필요 없음.)
  dynamic "statement" {
    for_each = var.secrets_read_arns
    content {
      sid       = "SecretsMetadataRO${replace(statement.value, "/[^a-zA-Z0-9]/", "")}"
      effect    = "Allow"
      actions   = ["secretsmanager:DescribeSecret", "secretsmanager:ListSecretVersionIds"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role" "eks_deploy" {
  name                 = "${var.name_prefix}-gha-eks-deploy"
  assume_role_policy   = data.aws_iam_policy_document.eks_deploy_trust.json
  description          = "GitHub Actions — Helm upgrade / rollout (EKS describe only; in-cluster perms via aws-auth)"
  max_session_duration = 3600

  tags = var.tags
}

resource "aws_iam_role_policy" "eks_deploy" {
  name   = "eks-deploy"
  role   = aws_iam_role.eks_deploy.id
  policy = data.aws_iam_policy_document.eks_deploy.json
}

# ─── (옵션) terraform plan/apply 용 read role ───────────────────────────
# CI 에서 `terraform plan` 만 돌리는 경우 — RO 권한.
data "aws_iam_policy_document" "tf_plan_trust" {
  count = var.create_tf_plan_role ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["${local.sub_repo_prefix}:pull_request"]
    }
  }
}

resource "aws_iam_role" "tf_plan" {
  count                = var.create_tf_plan_role ? 1 : 0
  name                 = "${var.name_prefix}-gha-tf-plan"
  assume_role_policy   = data.aws_iam_policy_document.tf_plan_trust[0].json
  description          = "GitHub Actions PR — terraform plan (RO)"
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy_attachment" "tf_plan_readonly" {
  count      = var.create_tf_plan_role ? 1 : 0
  role       = aws_iam_role.tf_plan[0].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_caller_identity" "current" {}
