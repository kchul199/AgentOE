output "project_id" {
  description = "Atlas Project ID. 이후 IP allowlist 등 추가 작업에 참조."
  value       = mongodbatlas_project.this.id
}

output "cluster_id" {
  description = "Cluster 고유 ID."
  value       = mongodbatlas_advanced_cluster.this.cluster_id
}

output "cluster_name" {
  description = "Atlas 클러스터 이름."
  value       = mongodbatlas_advanced_cluster.this.name
}

output "connection_strings" {
  description = "표준 / SRV 연결 문자열 (PrivateLink, peering 등 모드별)."
  value       = mongodbatlas_advanced_cluster.this.connection_strings
  sensitive   = true
}

output "privatelink_endpoint_id" {
  description = "PrivateLink 엔드포인트 ID. 비활성 시 null."
  value       = try(mongodbatlas_privatelink_endpoint.this[0].endpoint_service_id, null)
}

output "privatelink_service_name" {
  description = "AWS 측 VPC endpoint 가 연결할 서비스 이름."
  value       = try(mongodbatlas_privatelink_endpoint.this[0].endpoint_service_name, null)
}
