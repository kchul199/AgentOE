variable "project" {
  type    = string
  default = "agentoe"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

# DR 보조 region (cold-DR 또는 multi-region Atlas).
# 같은 region 으로 두면 DR 비활성.
variable "dr_region" {
  type    = string
  default = "ap-northeast-1"  # Tokyo (가장 가까운 region — RTT 최소)
}

# ── 네트워크 ────────────────────────────────────────────────────────────────
variable "vpc_cidr" {
  description = "Prod 는 staging 과 다른 CIDR — VPC peering 시 충돌 방지."
  type        = string
  default     = "10.60.0.0/16"
}

variable "azs" {
  description = "3 AZ. Seoul 은 a/b/c 보장."
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2b", "ap-northeast-2c"]
}

# ── EKS ────────────────────────────────────────────────────────────────────
variable "eks_version" {
  type    = string
  default = "1.29"
}

variable "eks_node_instance_types" {
  description = "Prod 기본: m7g.xlarge (Graviton, 4 vCPU / 16 GiB). c7g.xlarge 도 검토."
  type        = list(string)
  default     = ["m7g.xlarge"]
}

variable "eks_node_desired_size" {
  type    = number
  default = 6
}

variable "eks_node_min_size" {
  type    = number
  default = 6
}

variable "eks_node_max_size" {
  type    = number
  default = 30
}

# ── Redis ──────────────────────────────────────────────────────────────────
variable "redis_node_type" {
  description = "Prod: cache.r7g.large (16 GiB). Cache 부족 시 r7g.xlarge."
  type        = string
  default     = "cache.r7g.large"
}

variable "redis_num_cache_clusters" {
  description = "Primary + 2 replicas. Multi-AZ failover."
  type        = number
  default     = 3
}

# ── MongoDB Atlas ──────────────────────────────────────────────────────────
variable "atlas_org_id" {
  description = "MongoDB Atlas Organization ID. tfvars 로 주입."
  type        = string
}

variable "atlas_instance_size" {
  description = "Prod: M30 (~ $340/월). 트래픽 폭증 시 M40 으로 in-place upgrade."
  type        = string
  default     = "M30"
}

variable "atlas_pit_enabled" {
  description = "Atlas Point-in-Time recovery — prod 는 항상 true. M10 은 미지원이라 staging 은 false 가능."
  type        = bool
  default     = true
}

variable "atlas_dr_region_enabled" {
  description = "true 면 dr_region 에 Atlas read replica/electable 노드 추가 (multi-region cluster)."
  type        = bool
  default     = false  # 비용 큼 — 결정 후 활성
}

# ── Admin CIDR ─────────────────────────────────────────────────────────────
variable "admin_cidrs" {
  description = "Prod kubectl 접속 허용 — VPN 출구 IP 만. staging 보다 더 좁게."
  type        = list(string)
}

# ── DNS / WAF ──────────────────────────────────────────────────────────────
variable "domain_name" {
  description = "Prod 호스트 — api.${domain_name}, app.${domain_name}."
  type        = string
  default     = ""
}

variable "wafv2_acl_arn" {
  description = "ALB Ingress 가 사용할 기존 WAFv2 ACL ARN (별도 모듈/콘솔에서 관리)."
  type        = string
  default     = ""
}

# ── GitHub OIDC ────────────────────────────────────────────────────────────
variable "github_org" {
  type    = string
  default = "kchul199"
}

variable "github_repo" {
  type    = string
  default = "agentoe"   # monorepo 통합 후 새 이름
}

# ── Velero (k8s 백업) ──────────────────────────────────────────────────────
variable "velero_backup_bucket_force_destroy" {
  description = "false 권장 — 백업 버킷 실수로 destroy 안 되게 보호."
  type        = bool
  default     = false
}

variable "velero_backup_retention_days" {
  type    = number
  default = 90
}
