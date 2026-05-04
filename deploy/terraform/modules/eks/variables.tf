variable "cluster_name" {
  description = "EKS 클러스터 이름. 보통 ${project}-${environment}."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{0,99}$", var.cluster_name))
    error_message = "EKS 이름은 알파벳/숫자/하이픈, 영문으로 시작."
  }
}

variable "cluster_version" {
  description = "Kubernetes 버전. EKS 가 지원하는 마이너 버전만 (예: 1.29)."
  type        = string
  default     = "1.29"
}

variable "vpc_id" {
  description = "EKS 가 배포될 VPC ID."
  type        = string
}

variable "private_subnet_ids" {
  description = "워커가 배치될 프라이빗 서브넷 IDs (3 AZ)."
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "ALB 등 퍼블릭 리소스용 서브넷 IDs. 컨트롤플레인 ENI 도 여기에 분산."
  type        = list(string)
}

variable "endpoint_public_access" {
  description = "API server public endpoint 활성화. true 권장 + public_access_cidrs 제한."
  type        = bool
  default     = true
}

variable "public_access_cidrs" {
  description = "Public endpoint 접근 허용 CIDR. 회사 VPN/Bastion 출구 IP 만 허용."
  type        = list(string)
  default     = []
}

variable "cluster_log_types" {
  description = "활성화할 컨트롤플레인 로그 타입."
  type        = list(string)
  default     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
}

variable "log_retention_days" {
  description = "컨트롤플레인 CW Log 보존일."
  type        = number
  default     = 30
}

# ── 노드그룹 ────────────────────────────────────────────────────────────
variable "node_instance_types" {
  description = "노드 인스턴스 타입. Graviton (c7g/m7g) 권장."
  type        = list(string)
  default     = ["c7g.large"]
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 6
}

variable "node_ami_type" {
  description = "AL2_ARM_64 (Graviton), AL2_x86_64, BOTTLEROCKET_ARM_64 등."
  type        = string
  default     = "AL2_ARM_64"
}

variable "node_disk_size" {
  description = "노드 EBS 디스크 (GiB)."
  type        = number
  default     = 50
}

# ── 애드온 버전 ──────────────────────────────────────────────────────────
# 명시 버전 고정. EKS 콘솔 → 클러스터 → Add-ons 에서 호환 버전 확인 후 갱신.
variable "vpc_cni_version" {
  type    = string
  default = "v1.18.1-eksbuild.3"
}

variable "coredns_version" {
  type    = string
  default = "v1.11.1-eksbuild.8"
}

variable "kube_proxy_version" {
  type    = string
  default = "v1.29.3-eksbuild.2"
}

variable "ebs_csi_version" {
  type    = string
  default = "v1.30.0-eksbuild.1"
}

variable "tags" {
  description = "병합될 추가 태그."
  type        = map(string)
  default     = {}
}
