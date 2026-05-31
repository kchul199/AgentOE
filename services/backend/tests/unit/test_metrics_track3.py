"""Track 3: Prometheus 메트릭 확장 단위 테스트.

대상:
  - record_quota_check — 레이블 정규화, in-process + Prometheus 이중 기록
  - record_llm_usage   — tokens/cost 분기, 0 무시
  - record_jwks_lookup — 허용 결과값 필터
  - record_jwks_refresh — success/failure 히스토그램 관측
  - generate_prometheus_metrics() — fallback 텍스트 포맷에 신규 메트릭 포함
"""

from __future__ import annotations

import pytest

from app.core import metrics as m


@pytest.fixture(autouse=True)
def _reset_store():
    """각 테스트 시작 시 in-process store 재생성."""
    m._store = m._MetricsStore()
    yield


# ── record_quota_check ────────────────────────────────────────────────────────


def test_quota_check_ok_path() -> None:
    m.record_quota_check("tenant-A", scope="none", result="ok")
    ctr = m._store.llm_quota_checks["tenant-A:none:ok"]
    assert ctr.get() == 1.0


def test_quota_check_fallback_path() -> None:
    m.record_quota_check("tenant-A", scope="tokens", result="fallback")
    assert m._store.llm_quota_checks["tenant-A:tokens:fallback"].get() == 1.0


def test_quota_check_normalizes_unknown_labels() -> None:
    # 허용 목록 밖의 값은 조용히 none/ok 로 정규화
    m.record_quota_check("tenant-A", scope="exotic", result="strange")
    assert m._store.llm_quota_checks["tenant-A:none:ok"].get() == 1.0


def test_quota_check_accumulates() -> None:
    for _ in range(3):
        m.record_quota_check("tenant-A", scope="cost", result="reject")
    assert m._store.llm_quota_checks["tenant-A:cost:reject"].get() == 3.0


# ── record_llm_usage ──────────────────────────────────────────────────────────


def test_llm_usage_records_both_axes() -> None:
    m.record_llm_usage("tenant-A", model="llama3-70b", tokens=100, cost_cents=1.25)
    assert m._store.llm_tokens_consumed["tenant-A:llama3-70b"].get() == 100.0
    assert m._store.llm_cost_cents["tenant-A:llama3-70b"].get() == 1.25


def test_llm_usage_skips_zero_both() -> None:
    m.record_llm_usage("tenant-A", model="llama3-70b", tokens=0, cost_cents=0.0)
    assert "tenant-A:llama3-70b" not in m._store.llm_tokens_consumed
    assert "tenant-A:llama3-70b" not in m._store.llm_cost_cents


def test_llm_usage_tokens_only() -> None:
    m.record_llm_usage("tenant-A", model="llama3-8b", tokens=50, cost_cents=0)
    assert m._store.llm_tokens_consumed["tenant-A:llama3-8b"].get() == 50.0
    assert "tenant-A:llama3-8b" not in m._store.llm_cost_cents


# ── record_jwks_lookup ────────────────────────────────────────────────────────


def test_jwks_lookup_valid_results() -> None:
    for r in ["hit", "miss", "force_refresh", "fail"]:
        m.record_jwks_lookup(r)
    for r in ["hit", "miss", "force_refresh", "fail"]:
        assert m._store.jwks_lookups[r].get() == 1.0


def test_jwks_lookup_ignores_invalid() -> None:
    m.record_jwks_lookup("bogus")
    assert "bogus" not in m._store.jwks_lookups


# ── record_jwks_refresh ───────────────────────────────────────────────────────


def test_jwks_refresh_success_histogram() -> None:
    m.record_jwks_refresh(0.15, success=True)
    m.record_jwks_refresh(0.30, success=True)
    stats = m._store.jwks_refresh_duration_s["success"].stats()
    assert stats["count"] == 2
    assert pytest.approx(stats["sum"], rel=1e-6) == 0.45


def test_jwks_refresh_failure_separate_bucket() -> None:
    m.record_jwks_refresh(1.0, success=False)
    assert m._store.jwks_refresh_duration_s["failure"].stats()["count"] == 1
    assert m._store.jwks_refresh_duration_s["success"].stats()["count"] == 0


# ── generate_prometheus_metrics fallback 포맷 ─────────────────────────────────


def test_prometheus_text_includes_new_metrics(monkeypatch) -> None:
    """prometheus_client 가 없을 때의 fallback 텍스트에 신규 메트릭이 포함되는지."""
    # 실제 설치 상태와 무관하게 fallback 경로를 강제 관측
    monkeypatch.setattr(m, "_PROMETHEUS_AVAILABLE", False)

    m.record_quota_check("tenant-A", scope="tokens", result="fallback")
    m.record_llm_usage("tenant-A", model="llama3-70b", tokens=100, cost_cents=2.0)
    m.record_jwks_lookup("hit")
    m.record_jwks_refresh(0.2, success=True)

    text, ctype = m.generate_prometheus_metrics()
    assert ctype.startswith("text/plain")
    # 헬프/타입/실제 레이블이 모두 출력되는지 sanity-check
    assert "agentoe_llm_quota_checks_total" in text
    assert 'tenant="tenant-A"' in text
    assert 'scope="tokens"' in text
    assert 'result="fallback"' in text
    assert "agentoe_llm_tokens_consumed_total" in text
    assert 'model="llama3-70b"' in text
    assert "agentoe_llm_cost_cents_total" in text
    assert "agentoe_jwks_lookups_total" in text
    assert 'result="hit"' in text
    assert "agentoe_jwks_refresh_duration_seconds" in text


# ── Track 2 P2: WS back-pressure 메트릭 ────────────────────────────────────────


def test_set_ws_queue_depth_records_gauge() -> None:
    m.set_ws_queue_depth("tenant-A", 3)
    assert m._store.ws_send_queue_depth["tenant-A"].get() == 3.0
    # 게이지는 set 이므로 값이 덮어써짐
    m.set_ws_queue_depth("tenant-A", 0)
    assert m._store.ws_send_queue_depth["tenant-A"].get() == 0.0


def test_record_ws_drop_valid_kinds() -> None:
    for k in ["audio", "event", "full"]:
        m.record_ws_drop("tenant-A", k)
    for k in ["audio", "event", "full"]:
        assert m._store.ws_drops[f"tenant-A:{k}"].get() == 1.0


def test_record_ws_drop_ignores_invalid_kind() -> None:
    """카디널리티 보호: 알 수 없는 kind 는 조용히 drop."""
    m.record_ws_drop("tenant-A", "unknown_kind")
    assert "tenant-A:unknown_kind" not in m._store.ws_drops


def test_record_ws_drop_accumulates() -> None:
    for _ in range(5):
        m.record_ws_drop("tenant-B", "audio")
    assert m._store.ws_drops["tenant-B:audio"].get() == 5.0


def test_prometheus_text_includes_ws_metrics(monkeypatch) -> None:
    """fallback 텍스트에 ws 게이지/카운터가 포함되는지."""
    monkeypatch.setattr(m, "_PROMETHEUS_AVAILABLE", False)
    m.set_ws_queue_depth("tenant-X", 7)
    m.record_ws_drop("tenant-X", "audio")
    m.record_ws_drop("tenant-X", "event")

    text, _ = m.generate_prometheus_metrics()
    assert "agentoe_ws_send_queue_depth" in text
    assert 'tenant="tenant-X"' in text
    assert "agentoe_ws_drops_total" in text
    assert 'kind="audio"' in text
    assert 'kind="event"' in text
