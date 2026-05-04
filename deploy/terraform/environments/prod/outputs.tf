# 운영 / CI 가 사용할 prod 환경 식별자.

output "cluster_name" {
  description = "EKS prod 클러스터 이름. kubeconfig + GHA EKS_CLUSTER_NAME_PROD 등록."
  value       = module.eks.cluster_name
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "nat_public_ips" {
  description = "Atlas allowlist 에 자동 반영됨."
  value       = module.vpc.nat_public_ips
}

output "ecr_registry" {
  description = "GHA Variables.ECR_REGISTRY 에 등록 (계정.dkr.ecr.region.amazonaws.com)."
  value       = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

output "ecr_repos" {
  value = module.ecr.repository_urls
}

# ── Atlas / Redis ──────────────────────────────────────────────────────────
output "atlas_srv_host" {
  description = "Mongo connection string 에 사용할 host (prefix mongodb+srv:// 미포함)."
  value       = module.atlas.srv_address
  sensitive   = true
}

output "redis_primary_host" {
  value = module.redis.primary_endpoint
}

output "redis_reader_host" {
  value = module.redis.reader_endpoint
}

# ── IRSA ──────────────────────────────────────────────────────────────────
output "backend_irsa_role_arn" {
  value = module.cluster_bootstrap.backend_role_arn
}

output "alb_controller_role_arn" {
  value = module.cluster_bootstrap.alb_controller_role_arn
}

output "external_dns_role_arn" {
  value = module.cluster_bootstrap.external_dns_role_arn
}

output "eso_irsa_role_arn" {
  value = module.secrets.eso_role_arn
}

output "velero_role_arn" {
  description = "Velero ServiceAccount 의 eks.amazonaws.com/role-arn 어노테이션 값."
  value       = aws_iam_role.velero.arn
}

# ── ALB / DNS ─────────────────────────────────────────────────────────────
output "acm_certificate_arn" {
  value = module.alb_dns.acm_certificate_arn
}

output "route53_zone_id" {
  value = module.alb_dns.route53_zone_id
}

# ── GitHub OIDC (prod 별도 role) ──────────────────────────────────────────
output "github_ecr_push_role_arn" {
  description = "GHA Variables.AWS_ECR_PUSH_ROLE_ARN_PROD."
  value       = module.github_oidc.ecr_push_role_arn
}

output "github_eks_deploy_role_arn" {
  description = "GHA Variables.AWS_EKS_DEPLOY_ROLE_ARN_PROD."
  value       = module.github_oidc.eks_deploy_role_arn
}

# ── Velero S3 ─────────────────────────────────────────────────────────────
output "velero_backup_bucket" {
  value = aws_s3_bucket.velero.id
}

output "velero_backup_bucket_arn" {
  value = aws_s3_bucket.velero.arn
}

# ── WAF ────────────────────────────────────────────────────────────────────
output "wafv2_acl_arn" {
  description = "Helm values 의 alb.ingress.kubernetes.io/wafv2-acl-arn 에 사용."
  value       = var.wafv2_acl_arn
}
