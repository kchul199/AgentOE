output "ecr_push_role_arn" {
  description = "GitHub Actions 가 ECR push 시 assume 할 role ARN"
  value       = aws_iam_role.ecr_push.arn
}

output "eks_deploy_role_arn" {
  description = "GitHub Actions 가 Helm 배포 시 assume 할 role ARN. EKS aws-auth ConfigMap 에 추가해야 in-cluster API 호출 가능."
  value       = aws_iam_role.eks_deploy.arn
}

output "tf_plan_role_arn" {
  description = "terraform plan 용 RO role (생성된 경우만)"
  value       = try(aws_iam_role.tf_plan[0].arn, "")
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN — 다른 모듈에서 trust 작성 시 참조"
  value       = local.oidc_provider_arn
}
