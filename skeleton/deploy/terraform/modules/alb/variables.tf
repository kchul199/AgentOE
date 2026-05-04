variable "domain_name" {
  description = "Route53 hosted zone 도메인. 빈 문자열이면 ACM/DNS 단계 스킵."
  type        = string
  default     = ""
}

variable "primary_hostname" {
  description = "발급할 인증서의 primary host (예: staging.api.agentoe.io)."
  type        = string
  default     = ""
}

variable "alt_hostnames" {
  description = "추가 SAN 엔트리. (예: staging.app.agentoe.io)"
  type        = list(string)
  default     = []
}

variable "create_alias" {
  description = "ALB DNS alias 레코드 생성 여부."
  type        = bool
  default     = false
}

variable "alb_dns_name" {
  description = "ALB DNS 이름 (K8s Ingress 가 만든 후 채움)."
  type        = string
  default     = ""
}

variable "alb_zone_id" {
  description = "ALB hosted zone ID. ALB region 마다 고정값."
  type        = string
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
