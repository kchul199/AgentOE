output "alb_controller_role_arn" {
  description = "AWS Load Balancer Controller IRSA role ARN. SA annotation 에 박음."
  value       = try(aws_iam_role.alb_controller[0].arn, null)
}

output "external_dns_role_arn" {
  description = "external-dns IRSA role ARN."
  value       = try(aws_iam_role.external_dns[0].arn, null)
}

output "backend_role_arn" {
  description = "agentoe-backend IRSA role ARN."
  value       = try(aws_iam_role.backend[0].arn, null)
}

output "backend_role_name" {
  description = "백엔드 role 이름. 추가 정책 attach 용."
  value       = try(aws_iam_role.backend[0].name, null)
}
