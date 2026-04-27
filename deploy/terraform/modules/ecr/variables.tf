variable "project" {
  description = "리포지토리 prefix (예: agentoe → agentoe/backend)."
  type        = string
}

variable "repository_names" {
  description = "생성할 리포 suffix. 보통 backend / vbgw / frontend."
  type        = list(string)
  default     = ["backend", "vbgw", "frontend"]
}

variable "tags" {
  description = "병합될 추가 태그."
  type        = map(string)
  default     = {}
}
