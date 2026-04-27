output "secret_arns" {
  description = "Secret 키 → ARN 매핑. ESO ExternalSecret 매니페스트에서 참조."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "secret_names" {
  description = "Secret 키 → 이름 매핑."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}

output "kms_key_arn" {
  description = "시크릿 봉인 KMS 키 ARN."
  value       = aws_kms_key.secrets.arn
}

output "eso_role_arn" {
  description = "External Secrets Operator IRSA role ARN. ServiceAccount annotation 에 박음."
  value       = try(aws_iam_role.eso[0].arn, null)
}

# 자동 생성된 비번을 다른 모듈에 전달 (sensitive).
output "generated_passwords" {
  description = "자동 생성된 random_password 결과. 다른 모듈 (Atlas user, Redis auth) 입력으로 흘림."
  value       = { for k, p in random_password.this : k => p.result }
  sensitive   = true
}
