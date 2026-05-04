terraform {
  required_version = ">= 1.7.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
  # 초기 부트스트랩이므로 로컬 state 로 시작. apply 후 이 파일의 backend 를
  # S3 로 전환하고 `terraform init -migrate-state` 로 옮긴다.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = var.project
      Environment = "shared"
      ManagedBy   = "terraform"
      Component   = "state-backend"
    }
  }
}

# ── KMS — state 암호화 전용 ────────────────────────────────────────────────
resource "aws_kms_key" "state" {
  description             = "${var.project} Terraform state encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  # 루트 + 명시 관리자 외에는 접근 불가. IAM 정책 쪽에서 별도 grant.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "state" {
  name          = "alias/${var.project}-tf-state"
  target_key_id = aws_kms_key.state.key_id
}

data "aws_caller_identity" "current" {}

# ── S3 버킷 ────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "state" {
  bucket = "${var.project}-tf-state-${data.aws_caller_identity.current.account_id}-${var.region}"

  # 부트스트랩 버킷은 치명적이므로 실수 방지 장치. 정말 파괴하려면 일시적으로
  # false 로 바꾼 뒤 apply → destroy.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 90일 이상된 noncurrent 버전은 자동 정리 (비용 억제)
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ── DynamoDB 락 테이블 ──────────────────────────────────────────────────────
resource "aws_dynamodb_table" "lock" {
  name         = "${var.project}-tf-state-lock"
  billing_mode = "PAY_PER_REQUEST" # 초저트래픽 → on-demand 가 훨씬 저렴
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
