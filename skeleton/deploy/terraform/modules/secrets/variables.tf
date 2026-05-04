variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "secrets" {
  description = <<-EOT
    Secrets Manager 에 저장할 시크릿 정의.
    구조:
      {
        redis_auth_token = {
          description = "Redis AUTH"
          length      = 48
          special     = true
        }
        mongo_app_password = {
          description = "Mongo app user password"
          length      = 32
        }
        llm_api_keys = {
          description = "LLM provider API keys (multi)"
          json_keys   = ["openai", "anthropic", "groq"]
          value       = { openai = "...", anthropic = "...", groq = "..." }
        }
      }
    value 가 주어지면 외부 주입, 아니면 random_password 자동 생성.
  EOT
  type        = any
  default     = {}
}

variable "recovery_window_in_days" {
  description = "삭제 시 복구 기간. 0 이면 즉시 삭제 (테스트만)."
  type        = number
  default     = 7
}

# ── ESO IRSA ──────────────────────────────────────────────────────────────
variable "create_eso_role" {
  description = "External Secrets Operator IRSA role 생성 여부."
  type        = bool
  default     = true
}

variable "oidc_provider_arn" {
  description = "EKS OIDC provider ARN. eso_role 만들 때 필요."
  type        = string
  default     = ""
}

variable "oidc_provider_url" {
  description = "EKS OIDC provider URL."
  type        = string
  default     = ""
}

variable "eso_namespace" {
  description = "ESO 가 배포된 네임스페이스."
  type        = string
  default     = "external-secrets"
}

variable "eso_service_account" {
  description = "ESO controller ServiceAccount 이름."
  type        = string
  default     = "external-secrets"
}

variable "tags" {
  type    = map(string)
  default = {}
}
