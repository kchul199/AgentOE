/**
 * 검증 패널 — 오류·경고 이슈를 severity 별로 구분 렌더.
 *
 * 개선 (Dark-Pro 리디자인):
 *   - 오류 / 경고 섹션 분리
 *   - 아이콘 + code + message 3-레이어
 *   - 정상 시 녹색 배지 표시
 */
import type { GraphValidationIssue } from "@/lib/dsl";

interface Props {
  issues: GraphValidationIssue[];
}

export default function ValidationPanel({ issues }: Props): JSX.Element {
  const errors   = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");

  if (issues.length === 0) {
    return (
      <div className="section">
        <div className="validation-ok">
          <span className="validation-ok-icon">✓</span>
          <span>구조 오류 없음 — 백엔드 전송 가능</span>
        </div>
      </div>
    );
  }

  return (
    <div className="section">
      {errors.length > 0 && (
        <>
          <h2>오류 ({errors.length})</h2>
          {errors.map((it, i) => (
            <div key={`e_${i}`} className="issue error">
              <span className="issue-icon">✕</span>
              <div className="issue-body">
                <div className="issue-code">{it.code}</div>
                <div className="issue-msg">{it.message}</div>
              </div>
            </div>
          ))}
        </>
      )}

      {warnings.length > 0 && (
        <>
          <h2 style={{ marginTop: errors.length > 0 ? 14 : 0 }}>
            경고 ({warnings.length})
          </h2>
          {warnings.map((it, i) => (
            <div key={`w_${i}`} className="issue warning">
              <span className="issue-icon">⚠</span>
              <div className="issue-body">
                <div className="issue-code">{it.code}</div>
                <div className="issue-msg">{it.message}</div>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
