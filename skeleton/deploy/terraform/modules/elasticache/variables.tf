variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "프라이빗 서브넷 ID. 최소 2개 (Multi-AZ)."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "6379 인바운드를 허용할 SG (EKS 워커 SG, ALB SG 등)."
  type        = list(string)
  default     = []
}

variable "engine_version" {
  description = "Redis 엔진 버전. 7.x 권장 (7.0/7.1)."
  type        = string
  default     = "7.1"
}

variable "node_type" {
  description = "Staging: cache.t4g.small, Prod: cache.r7g.large+"
  type        = string
  default     = "cache.t4g.small"
}

variable "num_cache_clusters" {
  description = "Primary + replica 총 노드 수. 2 이상이면 자동 페일오버 + Multi-AZ 활성화."
  type        = number
  default     = 2

  validation {
    condition     = var.num_cache_clusters >= 1
    error_message = "최소 1개 이상."
  }
}

variable "auth_token" {
  description = "Redis AUTH 토큰. Secrets Manager 에서 주입."
  type        = string
  sensitive   = true
}

variable "maxmemory_policy" {
  description = "캐시 용도면 allkeys-lru, 세션이면 noeviction 권장."
  type        = string
  default     = "allkeys-lru"
}

variable "snapshot_retention_days" {
  description = "RDB 스냅샷 보존일. 0 이면 비활성."
  type        = number
  default     = 7
}

variable "snapshot_window" {
  description = "스냅샷 시작 윈도우 (UTC)."
  type        = string
  default     = "17:00-19:00"
}

variable "maintenance_window" {
  description = "유지보수 윈도우 (UTC)."
  type        = string
  default     = "sun:19:00-sun:21:00"
}

variable "enable_logging" {
  description = "Slow/Engine log CloudWatch 발행."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "tags" {
  type    = map(string)
  default = {}
}
