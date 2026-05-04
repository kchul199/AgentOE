# docker/ — 통합 docker-compose

monorepo 통합 후 단일 위치에서 모든 docker-compose 파일 관리.

## 파일 매트릭스

| 파일                          | 무엇                                                | 옛 위치                                            |
|-------------------------------|-----------------------------------------------------|---------------------------------------------------|
| `compose.backend.yml`         | AgentOE backend stack (mongo+redis+api+nginx)       | `skeleton/docker-compose.yml`                      |
| `compose.backend.dev.yml`     | dev override (live reload, mongo-express)           | `skeleton/docker-compose.dev.yml`                  |
| `compose.vbgw.yml`            | vbgw stack (FS+orchestrator+bridge+vbgw-ai+redis)   | `vbgw_v2/vbgw-freeswitch/docker-compose.yml`       |
| `compose.vbgw.canary.yml`     | vbgw canary 환경                                     | `vbgw_v2/vbgw-freeswitch/docker-compose.canary.yml` |
| `compose.vbgw.prod.yml`       | vbgw prod-like 환경                                  | `vbgw_v2/vbgw-freeswitch/docker-compose.prod.yml`   |
| `compose.integration.yml`     | cross-stack 통합 (bridge → backend gRPC redirect)   | 옛 AgenticOE_v2/skeleton + vbgw_v2 양쪽 override    |

## 사용 패턴

### Backend dev only
```bash
docker compose -f docker/compose.backend.yml \
               -f docker/compose.backend.dev.yml up -d
```

### vbgw dev only
```bash
docker compose -f docker/compose.vbgw.yml up -d
```

### 통합 (backend + vbgw bridge → backend gRPC)
```bash
docker network create agentoe-vbgw-bridge || true
docker compose -f docker/compose.backend.yml \
               -f docker/compose.backend.dev.yml \
               -f docker/compose.vbgw.yml \
               -f docker/compose.integration.yml up -d

# smoke 검증
python3 scripts/integration/smoke_grpc_client.py
```

또는 단일 명령:
```bash
./scripts/integration/dev-integration.sh up
```

## monorepo 후 변경점

옛 cross-project (AgenticOE_v2 ↔ vbgw_v2) 일 때는 **외부 docker network** + **양쪽 compose 의 override** 가 필요. 이제 단일 repo 라:
- 외부 network 만들기는 그대로 (서비스 격리상 useful)
- 양쪽 override 가 한 디렉토리에서 관리됨 (`docker/`)
- Build context 가 monorepo 루트 기준 — `../services/{name}/` 로 일관

## 다음 개선 후보

- `docker/compose.all.yml` — 한 파일에 모든 service 묶은 fully-integrated compose (현재는 override 조합)
- `docker/Makefile` — `make backend-up`, `make integration-up` 등 단축
