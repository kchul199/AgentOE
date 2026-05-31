#!/usr/bin/env python3
"""MFA secret KMS 마이그레이션 스크립트 (Phase N — N5.2).

레거시 env-var AES-GCM 암호화된 mfa_secret_enc 를 KMS envelope 방식으로 재암호화.

사전 조건:
  - MONGODB_URI, MONGODB_DB_NAME 환경변수 설정
  - PORTAL_MFA_ENVELOPE_KEY (기존 env-var key hex) — 레거시 복호화용
  - PORTAL_KMS_KEY_ID (AWS KMS key ARN 또는 alias) — 신규 암호화용
  - PORTAL_KMS_REGION (기본값: ap-northeast-2)
  - AWS 자격증명 설정 (OIDC / AccessKey / ~/.aws/credentials)

실행 방법:
  # dry-run (실제 수정 없음)
  python scripts/migrate_mfa_to_kms.py --dry-run

  # 실제 마이그레이션
  python scripts/migrate_mfa_to_kms.py

  # 특정 username 만
  python scripts/migrate_mfa_to_kms.py --username admin@agentoe.io

진행 로직:
  1. portal_users 컬렉션에서 mfa_enabled=True 이고 mfa_secret_enc 가 "kms:" 로 시작하지 않는 사용자 조회
  2. 레거시 방식으로 복호화 → KMS 방식으로 재암호화
  3. DB 업데이트 (mfa_secret_enc, mfa_kms_migrated_at)
  4. 성공/실패 카운트 출력

안전 보장:
  - 이미 "kms:" prefix 인 secret 은 건너뜀
  - 개별 사용자 실패 시 해당 사용자만 SKIP, 전체 중단 없음
  - --dry-run 으로 사전 검증 가능
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from datetime import datetime, timezone


def _decrypt_legacy(ciphertext: str) -> str:
    """레거시 env-var AES-GCM 또는 base64 복호화."""
    key_hex = os.environ.get("PORTAL_MFA_ENVELOPE_KEY", "")
    if not key_hex:
        return base64.b64decode(ciphertext).decode()
    key = bytes.fromhex(key_hex)[:32]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(ciphertext)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


async def _encrypt_kms(secret: str, key_id: str, region: str) -> str:
    """KMS envelope 암호화 → kms:<b64> 포맷."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    # kms_client 를 직접 import (app.core.config 의존성 없이)
    import asyncio
    import struct
    import boto3  # type: ignore[import-untyped]

    loop = asyncio.get_event_loop()
    client = boto3.client("kms", region_name=region)

    def _generate() -> tuple[bytes, bytes]:
        resp = client.generate_data_key(KeyId=key_id, KeySpec="AES_256")
        return bytes(resp["Plaintext"]), bytes(resp["CiphertextBlob"])

    plaintext_dek, encrypted_dek = await loop.run_in_executor(None, _generate)
    nonce = os.urandom(12)
    ct = AESGCM(plaintext_dek).encrypt(nonce, secret.encode(), None)
    # pack: 2B len_dek + encrypted_dek + 12B nonce + ct
    header = struct.pack(">H", len(encrypted_dek))
    payload = header + encrypted_dek + nonce + ct
    return "kms:" + base64.b64encode(payload).decode()


async def migrate(
    dry_run: bool = True,
    target_username: str | None = None,
) -> None:
    import motor.motor_asyncio  # type: ignore[import-untyped]

    mongo_uri = os.environ["MONGODB_URI"]
    db_name = os.environ.get("MONGODB_DB_NAME", "agentoe")
    kms_key_id = os.environ.get("PORTAL_KMS_KEY_ID", "")
    kms_region = os.environ.get("PORTAL_KMS_REGION", "ap-northeast-2")

    if not kms_key_id:
        print("❌ PORTAL_KMS_KEY_ID 환경변수 미설정. 종료.", file=sys.stderr)
        sys.exit(1)

    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
    db = client[db_name]
    col = db["portal_users"]

    # 대상 사용자 조회 — mfa_enabled=True 이고 kms: prefix 없는 것
    query: dict = {
        "mfa_enabled": True,
        "mfa_secret_enc": {"$exists": True, "$not": {"$regex": "^kms:"}},
    }
    if target_username:
        query["username"] = target_username

    users = await col.find(query, {"_id": 1, "username": 1, "mfa_secret_enc": 1}).to_list(None)
    print(f"마이그레이션 대상: {len(users)}명" + (" (dry-run)" if dry_run else ""))

    ok_count = 0
    skip_count = 0
    err_count = 0

    for user in users:
        uid = str(user["_id"])
        uname = user.get("username", uid)
        enc_legacy = user.get("mfa_secret_enc", "")

        if enc_legacy.startswith("kms:"):
            print(f"  SKIP {uname} — 이미 KMS 포맷")
            skip_count += 1
            continue

        try:
            secret = _decrypt_legacy(enc_legacy)
        except Exception as e:
            print(f"  ERR  {uname} — 레거시 복호화 실패: {e}")
            err_count += 1
            continue

        try:
            new_enc = await _encrypt_kms(secret, kms_key_id, kms_region)
        except Exception as e:
            print(f"  ERR  {uname} — KMS 암호화 실패: {e}")
            err_count += 1
            continue

        if dry_run:
            print(f"  DRY  {uname} — kms: 접두사로 재암호화 예정 (len={len(new_enc)})")
        else:
            await col.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "mfa_secret_enc": new_enc,
                    "mfa_kms_migrated_at": datetime.now(timezone.utc),
                }},
            )
            print(f"  OK   {uname} — 마이그레이션 완료")
        ok_count += 1

    print()
    print(f"결과: 성공={ok_count}, 건너뜀={skip_count}, 오류={err_count}")
    if dry_run:
        print("※ dry-run 모드 — DB 수정 없음. 실제 실행은 --no-dry-run 옵션 사용.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MFA secret KMS 마이그레이션")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="DB 수정 없이 대상만 출력 (기본값)")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        help="실제 마이그레이션 실행")
    parser.add_argument("--username", type=str, default=None,
                        help="특정 username 만 마이그레이션")
    args = parser.parse_args()

    asyncio.run(migrate(dry_run=args.dry_run, target_username=args.username))


if __name__ == "__main__":
    main()
