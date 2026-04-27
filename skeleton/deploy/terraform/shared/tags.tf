# 모든 리소스에 강제될 공통 태그.
# 비용 분석/오너십 추적/감사가 가능하려면 모든 리소스에 동일 키셋이 박혀 있어야 한다.
locals {
  common_tags = {
    Project     = "agentoe"
    ManagedBy   = "terraform"
    # 환경별 모듈 입력 var.environment 와 합쳐서 최종 태그 생성됨.
  }
}

variable "additional_tags" {
  description = "환경별/모듈별 추가 태그. common_tags 와 merge 됨."
  type        = map(string)
  default     = {}
}
