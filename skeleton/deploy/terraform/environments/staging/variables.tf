variable "project" {
  type    = string
  default = "agentoe"
}

variable "environment" {
  type    = string
  default = "staging"
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

# ── 네트워크 ────────────────────────────────────────────────────────────────
variable "vpc_cidr" {
  type    = string
  default = "10.50.0.0/16"
}

variable "azs" {
  description = "3 AZ 고정. Seoul 은 a/b/c 보장."
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2b", "ap-northeast-2c"]
}

# ── EKS ────────────────────────────────────────────────────────────────────
variable "eks_version" {
  type    = string
  default = "1.29"
}

variable "eks_node_instance_types" {
  description = "Staging 기본값: c7g.large (Graviton). 프로덕션은 m7g.xlarge+ 권장."
  type        = list(string)
  default     = ["c7g.large"]
}

variable "eks_node_desired_size" {
  type    = number
  default = 2
}

variable "eks_node_min_size" {
  type    = number
  default = 2
}

variable "eks_node_max_size" {
  type    = number
  default = 6
}

# ── Redis ──────────────────────────────────────────────────────────────────
variable "redis_node_type" {
  description = "Staging: cache.t4g.small. Prod: cache.r7g.large+"
  type        = string
  default     = "cache.t4g.small"
}

variable "redis_num_cache_clusters" {
  description = "Primary + replica 총 개수. Staging 은 2 (primary+1 replica)."
  type        = number
  default     = 2
}

# ── MongoDB Atlas ──────────────────────────────────────────────────────────
variable "atlas_org_id" {
  description = "MongoDB Atlas Organization ID. UI 에서 확인해 주입."
  type        = string
  # default 없음 — 반드시 tfvars 로 주입
}

variable "atlas_instance_size" {
  description = "Staging: M10 (~ $60/월). Prod: M30 (~ $340/월)."
  type        = string
  default     = "M10"
}

# ── Admin CIDR (kubectl 접속 허용 범위) ───────────────────────────────────
variable "admin_cidrs" {
  description = "EKS public endpoint 및 Atlas 에 kubectl/UI 가 닿을 수 있는 CIDR 화이트리스트."
  type        = list(string)
  # default 없음 — 반드시 tfvars 로. VPN 출구 IP 권장.
}

# ── DNS ────────────────────────────────────────────────────────────────────
variable "domain_name" {
  description = "루트 도메인. 예: agentoe.io → staging 호스트는 staging.api.agentoe.io."
  type        = string
  default     = ""
}
