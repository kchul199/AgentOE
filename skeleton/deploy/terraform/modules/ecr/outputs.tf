output "repository_urls" {
  description = "리포지토리 이름 → URL 매핑. CI 가 image push 대상으로 사용."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "repository_arns" {
  description = "리포지토리 이름 → ARN 매핑. IAM 정책에서 resource 로 박을 때 사용."
  value       = { for k, r in aws_ecr_repository.this : k => r.arn }
}
