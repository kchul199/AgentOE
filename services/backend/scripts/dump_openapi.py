#!/usr/bin/env python3
"""OpenAPI schema dump 스크립트 (Phase N — N1.10).

CI lint job 이 이 스크립트를 실행해 `openapi.json` 을 생성하고,
services/portal/openapi.config.ts 의 codegen 이 소비함.

사용:
    cd services/backend
    PYTHONPATH=. python scripts/dump_openapi.py [--out openapi.json]

CI (GitHub Actions):
    run: |
      cd services/backend
      pip install -r requirements.txt
      PYTHONPATH=. python scripts/dump_openapi.py --out ../../services/portal/openapi.json
"""

import argparse
import json
import os
import sys

# 필수 env 기본값 (CI 환경 — 실제 연결 없이 app import 목적)
_DEFAULTS = {
    "MONGODB_URI": "mongodb://localhost:27017/test",
    "REDIS_URL": "redis://localhost:6379",
    "JWT_SECRET": "ci-secret",
    "GROQ_API_KEY": "ci-key",
    "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/fake.json",
}

for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)

# app import (settings.Settings() 초기화됨)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402


def dump(out_path: str) -> None:
    schema = app.openapi()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    print(f"[dump_openapi] written → {out_path}  ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump FastAPI OpenAPI schema to JSON")
    parser.add_argument("--out", default="openapi.json", help="Output file path")
    args = parser.parse_args()
    dump(args.out)
