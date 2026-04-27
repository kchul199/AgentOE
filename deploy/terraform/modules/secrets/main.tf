# ──────────────────────────────────────────────────────────────────────────
# Secrets 모듈
# - Redis AUTH / Mongo password / LLM API keys 를 Secrets Manager 에 저장.
# - External Secrets Operator (ESO) 가 IRSA 로 읽어 K8s Secret 으로 동기화.
# - random_password 로 자체 생성 + 외부 주입 둘 다 지원.
# - SecretsManager 회전 람다는 Phase 2.
# ──────────────────────────────────────────────────────────────────────────

locals {
  prefix = "${var.project}/${var.environment}"

  tags = merge(var.tags, { Component = "secrets" })

  # 자동 생성 vs. 외부 주입 결정.
  # value 가 명시되면 그걸 사용, 아니면 random_password 결과 사용.
  managed_secrets = {
    for k, v in var.secrets : k => {
      description    = v.description
      explicit_value = lookup(v, "value", null)
      length         = lookup(v, "length", 32)
      special        = lookup(v, "special", false)
      json_keys      = lookup(v, "json_keys", null)
    }
  }
}

# ── KMS — 시크릿 봉인 ────────────────────────────────────────────────────
resource "aws_kms_key" "secrets" {
  description             = "${local.prefix} secrets encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.tags
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.project}-${var.environment}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

# ── 비밀번호 자동 생성 ────────────────────────────────────────────────────
resource "random_password" "this" {
  for_each = {
    for k, v in local.managed_secrets : k => v
    if v.explicit_value == null
  }

  length  = each.value.length
  special = each.value.special
  # 일부 엔진은 특수문자 제약 → 보수적으로 영숫자만 default.
  override_special = "!@#$%^&*()-_=+[]{}"
}

# ── Secrets Manager ──────────────────────────────────────────────────────
resource "aws_secretsmanager_secret" "this" {
  for_each = local.managed_secrets

  name        = "${local.prefix}/${each.key}"
  description = each.value.description
  kms_key_id  = aws_kms_key.secrets.arn

  recovery_window_in_days = var.recovery_window_in_days

  tags = merge(local.tags, { SecretKey = each.key })
}

resource "aws_secretsmanager_secret_version" "this" {
  for_each      = local.managed_secrets
  secret_id     = aws_secretsmanager_secret.this[each.key].id

  secret_string = (
    each.value.json_keys != null
    # JSON 형태로 packing — ESO 가 key 별로 picker 가능.
    ? jsonencode({
        for jk in each.value.json_keys : jk => (
          each.value.explicit_value != null
          ? lookup(each.value.explicit_value, jk, "")
          : random_password.this[each.key].result
        )
      })
    : (
        each.value.explicit_value != null
        ? each.value.explicit_value
        : random_password.this[each.key].result
      )
  )

  lifecycle {
    # 회전이 외부에서 일어나면 tf 가 매번 되돌리지 않도록.
    ignore_changes = [secret_string]
  }
}

# ── ESO IRSA Role ────────────────────────────────────────────────────────
# K8s ServiceAccount external-secrets/external-secrets 가 assume.
data "aws_caller_identity" "current" {}

resource "aws_iam_role" "eso" {
  count = var.create_eso_role ? 1 : 0
  name  = "${var.project}-${var.environment}-external-secrets"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(var.oidc_provider_url, "https://", "")}:sub" = "system:serviceaccount:${var.eso_namespace}:${var.eso_service_account}"
          "${replace(var.oidc_provider_url, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "eso" {
  count = var.create_eso_role ? 1 : 0
  name  = "secret-read"
  role  = aws_iam_role.eso[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecretVersionIds",
        ]
        # 이 env 의 secret 만 — 다른 env 시크릿에 손 못 대게.
        Resource = [
          "arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:${local.prefix}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.secrets.arn
      },
    ]
  })
}
