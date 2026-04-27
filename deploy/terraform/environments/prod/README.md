# Prod Terraform — placeholder

Staging 의 `main.tf` / `variables.tf` / `outputs.tf` / `backend.tf` 를 복사한 뒤
다음을 변경합니다.

| 항목 | Staging | Prod |
| --- | --- | --- |
| `environment` | `staging` | `prod` |
| `vpc_cidr` | `10.50.0.0/16` | `10.60.0.0/16` |
| `single_nat_gateway` | `true` | `false` (AZ 당 1개) |
| `flow_log_traffic_type` | `REJECT` | `ALL` |
| `eks_node_instance_types` | `["c7g.large"]` | `["m7g.xlarge","m7g.2xlarge"]` |
| `eks_node_min_size / desired` | `2` | `3` |
| `redis_node_type` | `cache.t4g.small` | `cache.r7g.large` |
| `redis_num_cache_clusters` | `2` | `3` |
| `atlas_instance_size` | `M10` | `M30` |
| `endpoint_public_access` | `true` (좁게) | `false` (Bastion 만) |
| `recovery_window_in_days` | `7` | `30` |
| `prevent_destroy` (state 버킷) | `true` | `true` |

`backend.tf` 의 key 도 `environments/prod/terraform.tfstate` 로 분리.

Apply 시 staging 적용 결과를 1주일 이상 안정화한 뒤 적용 권장.
