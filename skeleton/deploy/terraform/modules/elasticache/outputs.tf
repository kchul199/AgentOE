output "primary_endpoint_address" {
  description = "Primary writer 엔드포인트. 앱이 이 주소로 연결."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "reader_endpoint_address" {
  description = "Replica reader 엔드포인트. 읽기 전용 라우팅용."
  value       = aws_elasticache_replication_group.this.reader_endpoint_address
}

output "configuration_endpoint_address" {
  description = "Cluster mode 엔드포인트. 단일 RG 모드면 null."
  value       = aws_elasticache_replication_group.this.configuration_endpoint_address
}

output "port" {
  value = aws_elasticache_replication_group.this.port
}

output "security_group_id" {
  description = "Redis SG ID. 워커 SG 등에서 egress allow 시 참조."
  value       = aws_security_group.this.id
}

output "replication_group_id" {
  value = aws_elasticache_replication_group.this.id
}
