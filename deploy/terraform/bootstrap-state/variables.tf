variable "project" {
  description = "프로젝트 식별자. 버킷/테이블 이름 접두사."
  type        = string
  default     = "agentoe"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project))
    error_message = "project 는 소문자/숫자/하이픈, 3~21자."
  }
}

variable "region" {
  description = "State 백엔드 리전. 모든 env 는 이 리전의 S3/DDB 를 공유."
  type        = string
  default     = "ap-northeast-2"
}
