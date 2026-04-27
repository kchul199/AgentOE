variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "org_id" {
  description = "MongoDB Atlas Organization ID."
  type        = string
}

variable "atlas_region" {
  description = "Atlas 리전 식별자. AWS Seoul 은 AP_NORTHEAST_2."
  type        = string
  default     = "AP_NORTHEAST_2"
}

variable "instance_size" {
  description = "M10 (Staging), M30+ (Prod)."
  type        = string
  default     = "M10"
}

variable "mongo_version" {
  description = "Major 버전. '6.0', '7.0' 등."
  type        = string
  default     = "7.0"
}

variable "db_username" {
  description = "앱 DB 유저 이름."
  type        = string
  default     = "agentoe_app"
}

variable "db_password" {
  description = "앱 DB 유저 비번. random_password + Secrets Manager 에서 생성/관리."
  type        = string
  sensitive   = true
}

variable "app_database_name" {
  description = "앱이 사용할 DB 이름 (콘솔/시나리오 저장)."
  type        = string
  default     = "agentoe"
}

variable "additional_database_roles" {
  description = "추가로 부여할 (role, db) 쌍. 예: [{role_name='read', database_name='audit'}]."
  type = list(object({
    role_name     = string
    database_name = string
  }))
  default = []
}

variable "allowed_cidrs" {
  description = "Atlas 접근 허용 CIDR. NAT EIP/32 + 관리자 CIDR."
  type        = list(string)
  default     = []
}

variable "enable_privatelink" {
  description = "Atlas PrivateLink 활성화. AWS 측 endpoint 는 모듈 외부에서 wiring."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
