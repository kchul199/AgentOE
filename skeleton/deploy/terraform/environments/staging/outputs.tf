# ──────────────────────────────────────────────────────────────────────────
# 환경 outputs
# - kubeconfig 생성, Helm values 작성, 다른 stack 의 remote_state lookup 에 쓰임.
# - 민감값은 sensitive=true 로 표시 → CI 콘솔에 노출되지 않음.
# ──────────────────────────────────────────────────────────────────────────

# ── Cluster ───────────────────────────────────────────────────────────────
output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_version" {
  value = module.eks.cluster_version
}

output "oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

# kubeconfig 생성 명령:
#   aws eks update-kubeconfig --name <cluster_name> --region <region> --alias agentoe-staging
output "kubeconfig_command" {
  description = "kubectl 설정 한 줄 명령."
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --alias ${var.project}-${var.environment}"
}

# ── Network ───────────────────────────────────────────────────────────────
output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "nat_public_ips" {
  description = "Atlas IP allowlist 또는 외부 webhook 화이트리스트에 등록할 NAT EIP."
  value       = module.vpc.nat_public_ips
}

# ── ECR ────────────────────────────────────────────────────────────────────
output "ecr_repository_urls" {
  description = "CI 가 푸시할 ECR 리포 URL 들."
  value       = module.ecr.repository_urls
}

# ── Redis ──────────────────────────────────────────────────────────────────
output "redis_primary_endpoint" {
  value = module.redis.primary_endpoint_address
}

output "redis_reader_endpoint" {
  value = module.redis.reader_endpoint_address
}

output "redis_port" {
  value = module.redis.port
}

# ── MongoDB Atlas ──────────────────────────────────────────────────────────
output "atlas_project_id" {
  value = module.atlas.project_id
}

output "atlas_cluster_name" {
  value = module.atlas.cluster_name
}

output "atlas_connection_strings" {
  value     = module.atlas.connection_strings
  sensitive = true
}

# ── IAM Role ARNs (IRSA) ──────────────────────────────────────────────────
output "irsa_alb_controller" {
  value = module.cluster_bootstrap.alb_controller_role_arn
}

output "irsa_external_dns" {
  value = module.cluster_bootstrap.external_dns_role_arn
}

output "irsa_backend" {
  description = "agentoe-backend ServiceAccount annotation 에 박을 ARN."
  value       = module.cluster_bootstrap.backend_role_arn
}

output "irsa_external_secrets" {
  description = "ESO 컨트롤러 SA 에 박을 ARN."
  value       = module.secrets.eso_role_arn
}

# ── Secrets ────────────────────────────────────────────────────────────────
output "secret_arns" {
  description = "Secret 키 → ARN 매핑. ExternalSecret 매니페스트 작성에 사용."
  value       = module.secrets.secret_arns
}

# ── DNS / ACM ──────────────────────────────────────────────────────────────
output "acm_certificate_arn" {
  description = "Ingress annotation alb.ingress.kubernetes.io/certificate-arn 에 박음."
  value       = module.alb_dns.certificate_arn
}
