output "vpc_id" {
  description = "VPC ID. EKS/Elasticache 등 모든 모듈이 받음."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "VPC CIDR. SG ingress 룰에서 자주 쓰임."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "퍼블릭 서브넷 ID 리스트. ALB 배치용."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "프라이빗 서브넷 ID 리스트. EKS 워커/Elasticache 배치용."
  value       = aws_subnet.private[*].id
}

output "intra_subnet_ids" {
  description = "외부 라우팅 없는 서브넷. PrivateLink 인터페이스 엔드포인트 등."
  value       = aws_subnet.intra[*].id
}

output "nat_gateway_ids" {
  description = "NAT GW ID 리스트. 디버깅/모니터링용 reference."
  value       = aws_nat_gateway.this[*].id
}

output "nat_public_ips" {
  description = "NAT EIP 목록. Atlas IP allowlist 에 등록 필요."
  value       = aws_eip.nat[*].public_ip
}

output "private_route_table_ids" {
  description = "프라이빗 라우팅 테이블 ID. 추가 VPC endpoint association 용."
  value       = aws_route_table.private[*].id
}

output "intra_route_table_id" {
  description = "intra 라우팅 테이블 ID."
  value       = aws_route_table.intra.id
}

output "azs" {
  description = "사용한 AZ 리스트."
  value       = var.azs
}
