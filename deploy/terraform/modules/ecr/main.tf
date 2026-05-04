# ──────────────────────────────────────────────────────────────────────────
# ECR 모듈
# - 서비스별 (backend / vbgw / frontend) 리포지토리 생성.
# - immutable tag 강제 → 같은 태그 재push 차단 (롤백/감사 안정성).
# - Scan-on-push 활성화 → CI 단계에서 취약점 가시화.
# - 라이프사이클 정책으로 오래된 untagged 이미지 자동 만료 (스토리지 비용 억제).
# ──────────────────────────────────────────────────────────────────────────

locals {
  tags = merge(var.tags, { Component = "ecr" })
}

resource "aws_ecr_repository" "this" {
  for_each = toset(var.repository_names)

  name                 = "${var.project}/${each.value}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.tags, { Name = "${var.project}/${each.value}" })
}

# 라이프사이클: untagged 이미지 7일 후 만료, 태그된 이미지는 최근 30개 유지.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep last 30 tagged images"
        selection = {
          tagStatus   = "tagged"
          tagPatternList = ["*"]
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      },
    ]
  })
}

# 푸시는 CI OIDC 페더레이션으로, 풀은 EKS 노드 IAM 으로 — 모듈 외부에서 grant.
# 다만 cross-account pull 같은 케이스를 위해 repo policy 자리는 비워둠.
# resource "aws_ecr_repository_policy" "this" { ... }
