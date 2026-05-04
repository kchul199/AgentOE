variable "project" {
  description = "프로젝트 식별자. 리소스 네임/태그 접두사."
  type        = string
}

variable "environment" {
  description = "환경 식별자 (staging/prod/dev). 태그 및 리소스명에 사용."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR. /16 권장. /20 으로 4분할되어 public/private/intra x 3AZ 로 쓰임."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr 는 유효한 CIDR 표기여야 함 (예: 10.50.0.0/16)."
  }
}

variable "azs" {
  description = "사용할 AZ 리스트. 최소 2개 권장. EKS HA 위해 3개 표준."
  type        = list(string)

  validation {
    condition     = length(var.azs) >= 2
    error_message = "AZ 는 최소 2개 이상 필요 (EKS 컨트롤플레인 요구사항)."
  }
}

variable "single_nat_gateway" {
  description = "true 면 NAT GW 1개만 생성 (Staging/Dev). false 면 AZ 당 1개 (Prod)."
  type        = bool
  default     = true
}

variable "enable_flow_logs" {
  description = "VPC Flow Logs 활성화. CloudWatch Logs 비용 발생."
  type        = bool
  default     = true
}

variable "flow_log_traffic_type" {
  description = "ALL / ACCEPT / REJECT. Staging 은 REJECT 권장 (비용/효용 균형)."
  type        = string
  default     = "REJECT"

  validation {
    condition     = contains(["ALL", "ACCEPT", "REJECT"], var.flow_log_traffic_type)
    error_message = "ALL / ACCEPT / REJECT 중 하나."
  }
}

variable "flow_log_retention_days" {
  description = "Flow Log 보존 기간. 14일이면 비용/디버깅 균형 양호."
  type        = number
  default     = 14
}

variable "enable_vpc_endpoints" {
  description = "ECR/STS/Logs/Secrets PrivateLink 활성화. NAT 트래픽 절감 및 보안성 향상."
  type        = bool
  default     = true
}

variable "tags" {
  description = "병합될 추가 태그."
  type        = map(string)
  default     = {}
}
