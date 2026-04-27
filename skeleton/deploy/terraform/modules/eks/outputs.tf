output "cluster_name" {
  description = "EKS 클러스터 이름."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "API 서버 엔드포인트 (kubectl/Helm provider 가 사용)."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_ca_certificate" {
  description = "API 서버 CA. base64 디코딩 후 kubeconfig 에 박힘."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_security_group_id" {
  description = "클러스터에 EKS 가 자동 생성한 SG ID. 노드/애드온 통신 허용용."
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "cluster_version" {
  description = "운영 중인 K8s 버전."
  value       = aws_eks_cluster.this.version
}

output "oidc_provider_arn" {
  description = "IRSA 용 OIDC provider ARN. 다른 모듈/SA 가 trust policy 에 박음."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_provider_url" {
  description = "OIDC issuer URL (https:// 포함)."
  value       = aws_iam_openid_connect_provider.this.url
}

output "node_role_arn" {
  description = "워커 노드 IAM role ARN."
  value       = aws_iam_role.node.arn
}

output "kms_key_arn" {
  description = "K8s 시크릿 암호화 KMS 키. 별도 IAM 정책에서 grant 필요시 참조."
  value       = aws_kms_key.secrets.arn
}
