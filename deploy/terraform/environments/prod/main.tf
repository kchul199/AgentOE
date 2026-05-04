# ──────────────────────────────────────────────────────────────────────────
# Production environment — staging 기반 + prod-grade 강화
#
# Staging 과의 주요 차이:
#   - VPC: per-AZ NAT (single_nat_gateway=false)  — 가용성 ↑
#   - EKS: m7g.xlarge × 6 (vs c7g.large × 2)
#   - Redis: cache.r7g.large × 3 (Multi-AZ failover)
#   - Atlas: M30 + PIT enabled + 옵션 multi-region
#   - admin_cidrs 더 좁게 (VPN exit 만)
#   - GitHub OIDC IAM role 별도 (sub: refs/tags/v* + production environment)
#   - Velero S3 backup bucket
#   - WAFv2 ACL ALB Ingress 에 명시적 attach
# ──────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.14"
    }
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.18"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      CostCenter  = "agentoe-prod"
    }
  }
}

# DR region (Velero S3 cross-region replica 등에 사용)
provider "aws" {
  alias  = "dr"
  region = var.dr_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Purpose     = "dr-secondary"
    }
  }
}

provider "mongodbatlas" {}

provider "kubernetes" {
  host                   = module.eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks.cluster_ca_certificate)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
  }
}

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_ca_certificate)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
    }
  }
}

locals {
  name = "${var.project}-${var.environment}"

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

data "aws_caller_identity" "current" {}

# ── VPC — prod 는 per-AZ NAT (가용성 우선) ────────────────────────────────
module "vpc" {
  source = "../../modules/vpc"

  project     = var.project
  environment = var.environment
  vpc_cidr    = var.vpc_cidr
  azs         = var.azs

  single_nat_gateway      = false              # ★ per-AZ NAT
  enable_flow_logs        = true
  flow_log_traffic_type   = "ALL"              # prod 는 ALL (감사용)
  flow_log_retention_days = 90
  enable_vpc_endpoints    = true

  tags = local.common_tags
}

# ── EKS — prod 노드풀 ────────────────────────────────────────────────────
module "eks" {
  source = "../../modules/eks"

  cluster_name       = local.name
  cluster_version    = var.eks_version
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  public_subnet_ids  = module.vpc.public_subnet_ids

  endpoint_public_access = true                # private endpoint 도 검토 (VPN 필수 시 false)
  public_access_cidrs    = var.admin_cidrs     # VPN exit IP 만

  node_instance_types = var.eks_node_instance_types
  node_desired_size   = var.eks_node_desired_size
  node_min_size       = var.eks_node_min_size
  node_max_size       = var.eks_node_max_size

  tags = local.common_tags
}

# ── ECR — prod 별도 prefix ────────────────────────────────────────────────
module "ecr" {
  source = "../../modules/ecr"

  project = "${var.project}-${var.environment}"
  repository_names = [
    "backend", "frontend",
    "vbgw-ai", "vbgw-bridge", "vbgw-orchestrator",
  ]
  tags = local.common_tags
}

# ── Secrets ───────────────────────────────────────────────────────────────
module "secrets" {
  source = "../../modules/secrets"

  project     = var.project
  environment = var.environment

  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url

  secrets = {
    redis_auth_token = {
      description = "Redis AUTH token (prod)"
      length      = 64
      special     = true
    }
    mongo_app_password = {
      description = "Mongo agentoe app user password (prod)"
      length      = 48
    }
  }

  tags = local.common_tags
}

# ── ElastiCache — Multi-AZ failover ───────────────────────────────────────
module "redis" {
  source = "../../modules/elasticache"

  project     = var.project
  environment = var.environment
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.private_subnet_ids

  allowed_security_group_ids = [module.eks.cluster_security_group_id]

  node_type          = var.redis_node_type
  num_cache_clusters = var.redis_num_cache_clusters

  auth_token = module.secrets.generated_passwords["redis_auth_token"]

  tags = local.common_tags
}

# ── MongoDB Atlas ─────────────────────────────────────────────────────────
locals {
  nat_cidrs = [for ip in module.vpc.nat_public_ips : "${ip}/32"]
}

module "atlas" {
  source = "../../modules/atlas"

  project     = var.project
  environment = var.environment
  org_id      = var.atlas_org_id

  instance_size = var.atlas_instance_size
  db_password   = module.secrets.generated_passwords["mongo_app_password"]
  allowed_cidrs = concat(local.nat_cidrs, var.admin_cidrs)

  tags = local.common_tags
}

# ── Cluster Bootstrap IAM (IRSA) ──────────────────────────────────────────
module "cluster_bootstrap" {
  source = "../../modules/bootstrap"

  cluster_name      = module.eks.cluster_name
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url

  tags = local.common_tags
}

# ── ALB / ACM / Route53 ──────────────────────────────────────────────────
module "alb_dns" {
  source = "../../modules/alb"

  domain_name      = var.domain_name
  primary_hostname = var.domain_name != "" ? "api.${var.domain_name}" : ""
  alt_hostnames    = var.domain_name != "" ? ["app.${var.domain_name}"] : []

  create_alias = false

  tags = local.common_tags
}

# ── GitHub OIDC IAM (prod role 별도) ──────────────────────────────────────
# Phase 2-F 의 모듈 재사용. trust 의 sub 패턴이 prod 환경 한정.
module "github_oidc" {
  source = "../../modules/github-oidc"

  name_prefix          = local.name
  aws_region           = var.region
  github_org           = var.github_org
  github_repo          = var.github_repo
  create_oidc_provider = false                  # staging 이 이미 만든 것 재사용

  ecr_repository_names = [
    for r in ["backend", "frontend", "vbgw-ai", "vbgw-bridge", "vbgw-orchestrator"] :
    "${var.project}-${var.environment}/${r}"
  ]
  eks_cluster_names = [module.eks.cluster_name]

  tags = local.common_tags
}

# ── Velero backup bucket (S3 with cross-region replication) ──────────────
resource "aws_s3_bucket" "velero" {
  bucket        = "${local.name}-velero-backups-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.velero_backup_bucket_force_destroy

  tags = merge(local.common_tags, { Purpose = "velero-backups" })
}

resource "aws_s3_bucket_versioning" "velero" {
  bucket = aws_s3_bucket.velero.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "velero" {
  bucket = aws_s3_bucket.velero.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "velero" {
  bucket = aws_s3_bucket.velero.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter { prefix = "backups/" }

    expiration {
      days = var.velero_backup_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_public_access_block" "velero" {
  bucket = aws_s3_bucket.velero.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Velero IRSA role ─────────────────────────────────────────────────────
data "aws_iam_policy_document" "velero_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:velero:velero"]
    }
  }
}

data "aws_iam_policy_document" "velero" {
  statement {
    sid    = "EBSVolumeOps"
    effect = "Allow"
    actions = [
      "ec2:DescribeVolumes",
      "ec2:DescribeSnapshots",
      "ec2:CreateTags",
      "ec2:CreateVolume",
      "ec2:CreateSnapshot",
      "ec2:DeleteSnapshot",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "S3BackupBucket"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.velero.arn}/*"]
  }

  statement {
    sid       = "S3BucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.velero.arn]
  }
}

resource "aws_iam_role" "velero" {
  name               = "${local.name}-velero"
  assume_role_policy = data.aws_iam_policy_document.velero_trust.json
  description        = "Velero backup/restore — EBS snapshot + S3"
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "velero" {
  name   = "velero-backup"
  role   = aws_iam_role.velero.id
  policy = data.aws_iam_policy_document.velero.json
}
