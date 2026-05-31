"""AWS KMS envelope encryption helper (Phase N — N5.2).

설계:
  - boto3 (동기) 를 asyncio.get_event_loop().run_in_executor 로 감싸서
    FastAPI async 컨텍스트에서 non-blocking 하게 호출.
  - aiobotocore 미설치 환경에서도 동작하도록 executor 방식 선택.
  - 클라이언트 인스턴스는 모듈 레벨 lazy-singleton (스레드 안전 — boto3 Session은 멀티스레드 safe).

사용처:
  - auth_portal.py _encrypt_mfa_secret_kms / _decrypt_mfa_secret_kms

저장 포맷 (base64):
  [ 2 bytes big-endian: len(encrypted_dek) ]
  [ N bytes: encrypted_dek                  ]  ← KMS Encrypt 결과 Ciphertextblob
  [ 12 bytes: AES-GCM nonce                 ]
  [ M bytes: AES-GCM ciphertext+tag         ]
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any, TypedDict

import structlog

logger = structlog.get_logger(__name__)

# ── lazy singleton ─────────────────────────────────────────────────────────────

_kms_client = None
_kms_lock = asyncio.Lock()


def _get_boto_client(region: str) -> Any:
    """스레드/이벤트루프 안전 boto3 KMS 클라이언트 (lazy init)."""
    global _kms_client
    if _kms_client is None:
        import boto3  # type: ignore[import-untyped]

        _kms_client = boto3.client("kms", region_name=region)
    return _kms_client


# ── async wrappers ─────────────────────────────────────────────────────────────


class DataKeyPair(TypedDict):
    plaintext_dek: bytes  # AES-256 DEK — 메모리에만 보관, 저장 금지
    encrypted_dek: bytes  # KMS Ciphertextblob — 저장 가능


async def kms_generate_data_key(key_id: str, region: str = "ap-northeast-2") -> DataKeyPair:
    """KMS GenerateDataKey → AES-256 DEK 평문 + 암호화 본 반환.

    반환된 plaintext_dek 는 AES-GCM 암호화에만 사용하고 즉시 파기.
    encrypted_dek 는 ciphertext 와 함께 DB 에 저장.

    Args:
        key_id: AWS KMS key ARN 또는 alias (e.g. "alias/agentoe-portal-mfa")
        region: AWS region (settings.PORTAL_KMS_REGION)

    Returns:
        DataKeyPair with plaintext_dek and encrypted_dek

    Raises:
        RuntimeError: KMS 호출 실패 시
    """
    loop = asyncio.get_event_loop()
    client = _get_boto_client(region)

    def _call() -> DataKeyPair:
        try:
            resp = client.generate_data_key(KeyId=key_id, KeySpec="AES_256")
            return {
                "plaintext_dek": bytes(resp["Plaintext"]),
                "encrypted_dek": bytes(resp["CiphertextBlob"]),
            }
        except Exception as exc:
            raise RuntimeError(f"KMS GenerateDataKey failed: {exc}") from exc

    return await loop.run_in_executor(None, _call)


async def kms_decrypt_dek(encrypted_dek: bytes, region: str = "ap-northeast-2") -> bytes:
    """KMS Decrypt → DEK 평문 복원.

    Args:
        encrypted_dek: kms_generate_data_key 에서 반환된 encrypted_dek
        region: AWS region

    Returns:
        AES-256 DEK 평문 (32 bytes)

    Raises:
        RuntimeError: KMS 호출 실패 시
    """
    loop = asyncio.get_event_loop()
    client = _get_boto_client(region)

    def _call() -> bytes:
        try:
            resp = client.decrypt(CiphertextBlob=encrypted_dek)
            return bytes(resp["Plaintext"])
        except Exception as exc:
            raise RuntimeError(f"KMS Decrypt failed: {exc}") from exc

    return await loop.run_in_executor(None, _call)


# ── 저장 포맷 직렬화 / 역직렬화 ────────────────────────────────────────────────


def pack_kms_payload(encrypted_dek: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """[ 2B len_dek | encrypted_dek | 12B nonce | ciphertext ] 직렬화."""
    if len(nonce) != 12:
        raise ValueError(f"Nonce must be 12 bytes, got {len(nonce)}")
    header = struct.pack(">H", len(encrypted_dek))
    return header + encrypted_dek + nonce + ciphertext


def unpack_kms_payload(raw: bytes) -> tuple[bytes, bytes, bytes]:
    """[ 2B len_dek | encrypted_dek | 12B nonce | ciphertext ] 역직렬화.

    Returns:
        (encrypted_dek, nonce, ciphertext)

    Raises:
        ValueError: 포맷 오류
    """
    if len(raw) < 2:
        raise ValueError("KMS payload too short")
    (dek_len,) = struct.unpack_from(">H", raw, 0)
    offset = 2
    if len(raw) < offset + dek_len + 12:
        raise ValueError("KMS payload truncated")
    encrypted_dek = raw[offset : offset + dek_len]
    nonce = raw[offset + dek_len : offset + dek_len + 12]
    ciphertext = raw[offset + dek_len + 12 :]
    return encrypted_dek, nonce, ciphertext
