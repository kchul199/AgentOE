#!/usr/bin/env python3
"""
Portal 운영자 초기 계정 시드 스크립트.

사용법:
  # 기본 (admin/admin123 로컬 dev 계정)
  python scripts/seed_portal_admin.py

  # 사용자 지정
  python scripts/seed_portal_admin.py \\
    --username charls \\
    --password "MyP@ss!" \\
    --role portal:admin \\
    --mongo "mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"

환경변수로도 설정 가능:
  MONGODB_URI   MONGODB_DB_NAME   PORTAL_ADMIN_USERNAME   PORTAL_ADMIN_PASSWORD
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

try:
    import motor.motor_asyncio as motor
    import pymongo
    from passlib.context import CryptContext
except ImportError:
    print("의존성 부족. 다음을 설치하세요:")
    print("  pip install motor pymongo passlib[bcrypt]")
    sys.exit(1)

# ── 상수 ─────────────────────────────────────────────────────────────────────
VALID_ROLES = {"portal:viewer", "portal:operator", "portal:admin"}
DEFAULT_MONGO = "mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"
DEFAULT_DB    = "agentoe"

# bcrypt__truncate_error=False: auth_portal.py 와 동일 설정 (일관성 유지)
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def _hash(pw: str) -> str:
    return _pwd_ctx.hash(pw)


async def seed(
    mongo_uri: str,
    db_name: str,
    username: str,
    password: str,
    roles: list[str],
    totp_enabled: bool,
    force: bool,
) -> None:
    client = motor.AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5_000)
    try:
        # 연결 확인
        await client.admin.command("ping")
    except Exception as exc:
        print(f"[ERR] MongoDB 연결 실패: {exc}")
        print(f"      URI: {mongo_uri}")
        sys.exit(1)

    db   = client[db_name]
    coll = db["portal_users"]

    # 인덱스 보장
    await coll.create_index("username", unique=True, background=True)
    await coll.create_index("email",    unique=True, sparse=True, background=True)

    existing = await coll.find_one({"username": username})
    if existing and not force:
        print(f"[SKIP] '{username}' 계정이 이미 존재합니다. 덮어쓰려면 --force 를 사용하세요.")
        return

    doc = {
        "username":        username,
        "email":           f"{username}@local.dev",
        "hashed_password": _hash(password),
        "roles":           roles,
        "is_active":       True,
        "mfa_enabled":     totp_enabled,
        "mfa_secret_enc":  None,   # 첫 로그인 후 /auth/portal/mfa/enroll 로 등록
        "created_at":      datetime.now(tz=timezone.utc),
        "updated_at":      datetime.now(tz=timezone.utc),
    }

    if existing and force:
        await coll.replace_one({"username": username}, doc)
        print(f"[OK ] '{username}' 계정 갱신 완료.")
    else:
        await coll.insert_one(doc)
        print(f"[OK ] '{username}' 계정 생성 완료.")

    print(f"      roles   : {', '.join(roles)}")
    print(f"      mfa     : {'활성 (첫 로그인 후 /auth/portal/mfa/enroll 필요)' if totp_enabled else '비활성'}")
    print(f"      DB      : {db_name} → portal_users")
    client.close()


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Portal 초기 운영자 계정 시드")
    p.add_argument("--mongo",    default=os.environ.get("MONGODB_URI", DEFAULT_MONGO))
    p.add_argument("--db",       default=os.environ.get("MONGODB_DB_NAME", DEFAULT_DB))
    p.add_argument("--username", default=os.environ.get("PORTAL_ADMIN_USERNAME", "admin"))
    p.add_argument("--password", default=os.environ.get("PORTAL_ADMIN_PASSWORD", "admin123"))
    p.add_argument("--role",     default="portal:admin",
                   choices=sorted(VALID_ROLES), dest="role")
    p.add_argument("--no-mfa",   action="store_true", help="MFA 비활성화 (로컬 dev 편의)")
    p.add_argument("--force",    action="store_true", help="동일 username 이면 덮어쓰기")
    return p.parse_args()


def main() -> None:
    args = _parse()

    if len(args.password) < 6:
        print("[WARN] 비밀번호가 너무 짧습니다 (최소 6자 권장).")

    print("=" * 60)
    print(" AgentOE Portal — 초기 계정 시드")
    print("=" * 60)
    print(f"  MongoDB : {args.mongo.split('@')[-1]}")   # 패스워드 숨김
    print(f"  username: {args.username}")
    print(f"  role    : {args.role}")
    print()

    asyncio.run(
        seed(
            mongo_uri=args.mongo,
            db_name=args.db,
            username=args.username,
            password=args.password,
            roles=[args.role],
            totp_enabled=not args.no_mfa,
            force=args.force,
        )
    )

    print()
    print("다음 단계:")
    if not args.no_mfa:
        print("  1) http://localhost:5174 에서 로그인")
        print("  2) MFA 미등록 상태 → /auth/portal/mfa/enroll 로 자동 리다이렉트")
        print("  3) QR 스캔 후 Authenticator 앱 등록")
    else:
        print("  1) http://localhost:5174 에서 로그인 (MFA 없이)")


if __name__ == "__main__":
    main()
