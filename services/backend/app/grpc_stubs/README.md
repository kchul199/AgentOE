# app/grpc_stubs/ — vendored proto stubs

> ⚠️ **이 디렉토리의 .py 파일은 자동 생성물입니다. 직접 편집 금지.**

## 동기화

Canonical proto: `skeleton/contracts/proto/voicebot.proto`

```bash
# 1) stub 재생성
cd skeleton/contracts && make gen-python

# 2) backend 로 vendor (또는 CI 가 자동 검증)
cp skeleton/contracts/gen/python/voicebot* skeleton/backend/app/grpc_stubs/voicebot/

# 3) import path 패치 — 한 줄만 갱신:
#    "import voicebot_pb2 as voicebot__pb2"
#    →
#    "from app.grpc_stubs.voicebot import voicebot_pb2 as voicebot__pb2"
sed -i.bak '/^import voicebot_pb2/c\
from app.grpc_stubs.voicebot import voicebot_pb2 as voicebot__pb2' \
  skeleton/backend/app/grpc_stubs/voicebot/voicebot_pb2_grpc.py
rm skeleton/backend/app/grpc_stubs/voicebot/voicebot_pb2_grpc.py.bak
```

## 왜 vendor 하나

- 단일 `pip install -e .` 로 backend 가 stub import 가능 — PYTHONPATH hack 불필요
- Docker 이미지 안에 contracts/ 디렉토리 안 들어가도 됨 (build context 분리)
- CI 가 stub drift 검증 가능 — 가장 단순한 패턴

## CI 검증

`validate.yml` 에 다음 단계 추가 권장:
```yaml
- name: Verify vendored proto stubs match canonical
  working-directory: skeleton/contracts
  run: |
    make gen-python
    diff -q gen/python/voicebot_pb2.py     ../backend/app/grpc_stubs/voicebot/voicebot_pb2.py
    diff -q gen/python/voicebot_pb2.pyi    ../backend/app/grpc_stubs/voicebot/voicebot_pb2.pyi
    # _grpc.py 는 import path 만 다르므로 별도 비교
```

## 사용 예

```python
from app.grpc_stubs.voicebot import voicebot_pb2 as pb
from app.grpc_stubs.voicebot import voicebot_pb2_grpc as pb_grpc

class MyServicer(pb_grpc.VoicebotAiServiceServicer):
    async def StreamSession(self, request_iterator, context):
        async for chunk in request_iterator:  # AudioChunk
            ...
            yield pb.AiResponse(type=pb.AiResponse.STT_RESULT, text_content="...")
```
