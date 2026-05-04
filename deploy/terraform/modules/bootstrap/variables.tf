variable "cluster_name" {
  description = "EKS 클러스터 이름. role 이름 prefix 로 사용."
  type        = string
}

variable "oidc_provider_arn" {
  type = string
}

variable "oidc_provider_url" {
  type = string
}

variable "create_alb_controller_role" {
  description = "AWS Load Balancer Controller IRSA role 생성."
  type        = bool
  default     = true
}

variable "create_external_dns_role" {
  description = "external-dns IRSA role 생성."
  type        = bool
  default     = true
}

variable "create_backend_role" {
  description = "agentoe-backend IRSA role 생성."
  type        = bool
  default     = true
}

variable "backend_namespace" {
  description = "백엔드 SA 네임스페이스."
  type        = string
  default     = "agentoe"
}

variable "backend_service_account" {
  description = "백엔드 ServiceAccount 이름."
  type        = string
  default     = "agentoe-backend"
}

variable "tags" {
  type    = map(string)
  default = {}
}
