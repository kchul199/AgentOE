variable "name_prefix" {
  description = "리소스 이름 prefix (예: agentoe-staging)"
  type        = string
}

variable "aws_region" {
  description = "ECR / EKS resource region"
  type        = string
}

variable "github_org" {
  description = "GitHub 조직 / 사용자 이름"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository 이름"
  type        = string
}

variable "create_oidc_provider" {
  description = "이 모듈에서 OIDC provider 도 생성할지. false 면 existing_oidc_provider_arn 필수."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "create_oidc_provider=false 일 때 사용할 기존 provider ARN"
  type        = string
  default     = ""
}

variable "oidc_thumbprints" {
  description = "GitHub OIDC root CA thumbprints. 2 개 (root + intermediate) 권장."
  type        = list(string)
  default = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

variable "ecr_repository_names" {
  description = "이 role 이 push 할 수 있는 ECR repository 이름들 (예: [\"agentoe-staging/backend\", ...])"
  type        = list(string)
}

variable "eks_cluster_names" {
  description = "이 role 이 describe 할 수 있는 EKS 클러스터 이름들"
  type        = list(string)
}

variable "secrets_read_arns" {
  description = "(옵션) deploy role 이 metadata 만 읽도록 허용할 Secrets Manager ARN 들"
  type        = list(string)
  default     = []
}

variable "create_tf_plan_role" {
  description = "terraform plan 전용 RO role 도 생성할지 (PR 워크플로용)"
  type        = bool
  default     = false
}

variable "tags" {
  description = "공통 태그"
  type        = map(string)
  default     = {}
}
