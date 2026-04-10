# AgentOE — Multi-tenant Agentic AI Voice Callbot Platform

차세대 멀티테넌트 Agentic AI 음성 콜봇 오케스트레이션 플랫폼

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11 + FastAPI |
| Database | MongoDB 7.0 Replica Set |
| Cache | Redis 7.2 |
| STT | Groq Whisper Large v3 Turbo |
| LLM | Groq Llama 4 Scout / 3.3 70B |
| TTS | Google Neural2 (ko-KR) |
| Voice Gateway | VBGW (gRPC + WebSocket) |

## 주요 기능

- **세션 FSM**: IDLE → LISTENING → PROCESSING → INFERRING → RESPONDING → ENDED
- **Policy Gate**: G1(조회) ~ G5(법무) 5단계 리스크 분류
- **Kill Switch**: 테넌트 / 기능 / 시나리오 레벨 비상 정지
- **MCP 거버넌스**: Whitelist 기반 외부 커넥터 관리
- **멀티테넌트**: JWT 기반 테넌트 격리 + RBAC 8개 역할

## Quick Start (Local)

```bash
# 1. 환경변수 설정
cp .env.example .env
vi .env   # GROQ_API_KEY, JWT_SECRET 등 입력

# 2. Docker Compose로 전체 스택 기동
docker compose up -d

# 3. MongoDB Replica Set 초기화
mongosh "mongodb://admin:pass@localhost:27017/?authSource=admin" mongo/init_schema.js

# 4. API 헬스체크
curl http://localhost:8000/api/v1/health
```

## Project Structure

```
agentoe/
├── backend/
│   ├── app/
│   │   ├── api/v1/routers/    # FastAPI 라우터 (auth, sessions, ...)
│   │   ├── core/              # config, database, auth, exceptions, logging
│   │   ├── domain/            # session_fsm.py, policy_gate.py
│   │   └── repositories/      # MongoDB Motor 레포지토리
│   ├── tests/
│   │   └── unit/              # pytest 단위 테스트
│   ├── pyproject.toml
│   └── Dockerfile
├── mongo/
│   └── init_schema.js         # 9개 컬렉션 + 인덱스 초기화
└── .github/workflows/
    ├── ci.yml                 # lint + test + build
    ├── deploy-staging.yml     # develop 브랜치 → Staging 자동배포
    └── deploy-production.yml  # v* 태그 → Production 배포
```

## Development

```bash
# 의존성 설치
cd backend && pip install -e ".[dev]"

# 테스트 실행
pytest tests/ -v

# 코드 포맷
black app/ tests/
isort app/ tests/
```

## Branch Strategy

- `main`: 프로덕션 릴리즈
- `develop`: 통합 브랜치 (Staging 자동배포)
- `feature/AGT-{id}-{desc}`: 기능 개발
- `bugfix/AGT-{id}-{desc}`: 버그 수정
- `hotfix/AGT-{id}-{desc}`: 긴급 수정 (main에서 분기)

## License

Private — AgentOE Team
