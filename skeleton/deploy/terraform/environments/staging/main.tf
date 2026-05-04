# ──────────────────────────────────────────────────────────────────────────
# Staging environment — 모듈 컴포지션
# 적용 순서:
#   1. bootstrap-state 가 먼저 적용되어 backend.tf 의 S3/DDB 가 살아 있어야 함.
#   2. terraform init -migrate-state (최초 1회)
#   3. terraform apply -target=module.vpc → -target=module.eks → 전체 apply
#   (의존 순서대로 끊어 적용하면 첫 부트스트랩 디버깅이 쉬움)
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
    }
  }
}

# Atlas provider 는 환경변수로 자격 증명 받음:
#   MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY
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

# ── VPC ────────────────────────────────────────────────────────────────────
module "vpc" {
  source = "../../modules/vpc"

  project     = var.project
  environment = var.environment
  vpc_cidr    = var.vpc_cidr
  azs         = var.azs

  # Staging 비용 절감: NAT 1개. Prod 는 false.
  single_nat_gateway      = true
  enable_flow_logs        = true
  flow_log_traffic_type   = "REJECT"
  flow_log_retention_days = 14
  enable_vpc_endpoints    = true

  tags = local.common_tags
}

# ── EKS ────────────────────────────────────────────────────────────────────
module "eks" {
  source = "../../modules/eks"

  cluster_name       = local.name
  cluster_version    = var.eks_version
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  public_subnet_ids  = module.vpc.public_subnet_ids

  endpoint_public_access = true
  public_access_cidrs    = var.admin_cidrs

  node_instance_types = var.eks_node_instance_types
  node_desired_size   = var.eks_node_desired_size
  node_min_size       = var.eks_node_min_size
  node_max_size       = var.eks_node_max_size

  tags = local.common_tags
}

# ── ECR ────────────────────────────────────────────────────────────────────
# region 단위로 한 번만 만들면 충분하지만 환경별로 분리하면
# IAM 격리가 깔끔해서 staging/prod 각각 둠.
module "ecr" {
  source = "../../modules/ecr"

  project          = "${var.project}-${var.environment}"
  repository_names = ["backend", "vbgw", "frontend"]
  tags             = local.common_tags
}

# ── Secrets ────────────────────────────────────────────────────────────────
# 비밀번호는 자체 생성, LLM 키는 외부 주입 (terraform.tfvars / env 에서).
module "secrets" {
  source = "../../modules/secrets"

  project     = var.project
  environment = var.environment

  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url

  secrets = {
    redis_auth_token = {
      description = "Redis AUTH token"
      length      = 48
      special     = true
    }
    mongo_app_password = {
      description = "Mongo agentoe app user password"
      length      = 32
    }
  }

  tags = local.common_tags
}

# ── ElastiCache ────────────────────────────────────────────────────────────
module "redis" {
  source = "../../modules/elasticache"

  project     = var.project
  environment = var.environment
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.private_subnet_ids

  # EKS 워커 SG 가 닿을 수 있어야 함.
  allowed_security_group_ids = [module.eks.cluster_security_group_id]

  node_type          = var.redis_node_type
  num_cache_clusters = var.redis_num_cache_clusters

  auth_token = module.secrets.generated_passwords["redis_auth_token"]

  tags = local.common_tags
}

# ── MongoDB Atlas ──────────────────────────────────────────────────────────
# NAT EIP 들을 /32 로 allowlist.
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

# ── Cluster Bootstrap IAM (IRSA roles) ─────────────────────────────────────
module "cluster_bootstrap" {
  source = "../../modules/bootstrap"

  cluster_name      = module.eks.cluster_name
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url

  tags = local.common_tags
}

# ── ALB / DNS ──────────────────────────────────────────────────────────────
# 도메인이 주어졌을 때만 ACM/Route53 발급. K8s Ingress 가 ALB 를 만들면
# 그 다음에 alb_dns_name/zone_id 채워서 alias 생성.
module "alb_dns" {
  source = "../../modules/alb"

  domain_name      = var.domain_name
  primary_hostname = var.domain_name != "" ? "staging.api.${var.domain_name}" : ""
  alt_hostnames    = var.domain_name != "" ? ["staging.app.${var.domain_name}"] : []

  # 1차 apply 시점엔 false. ALB 생긴 뒤 alb_dns_name 박고 다시 apply.
  create_alias = false

  tags = local.common_tags
}
