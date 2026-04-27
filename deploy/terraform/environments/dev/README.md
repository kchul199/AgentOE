# Dev Terraform — placeholder

개인/팀 단위 ephemeral 환경 용도. Staging 의 `main.tf` 를 그대로 복사하되
다음을 추가/변경합니다.

| 항목 | 권장 값 |
| --- | --- |
| `environment` | `dev` (또는 `dev-${USER}`) |
| `vpc_cidr` | `10.40.0.0/16` |
| `single_nat_gateway` | `true` |
| `enable_flow_logs` | `false` (비용 절감) |
| `eks_node_instance_types` | `["t3.medium"]` 또는 spot 혼합 |
| `eks_node_min/desired` | `1` |
| `redis_node_type` | `cache.t4g.micro` |
| `redis_num_cache_clusters` | `1` (HA 비활성) |
| `atlas_instance_size` | `M0` (free) 또는 `M2` |
| `recovery_window_in_days` | `0` (테스트 즉시 삭제) |

또한 `terraform destroy` 가 일반적인 생애주기이므로 `lifecycle.prevent_destroy`
는 모두 빼는 편이 운영이 단순합니다.
