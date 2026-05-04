import type { GraphValidationIssue } from "@/lib/dsl";

interface Props {
  issues: GraphValidationIssue[];
}

export default function ValidationPanel({ issues }: Props): JSX.Element {
  if (issues.length === 0) {
    return (
      <div className="section validation">
        <h2>검증</h2>
        <div className="ok">✓ 구조 오류 없음 — 백엔드 전송 가능</div>
      </div>
    );
  }
  return (
    <div className="section validation">
      <h2>검증 ({issues.length})</h2>
      {issues.map((it, i) => (
        <div key={i} className={`issue ${it.severity}`}>
          <strong>[{it.code}]</strong> {it.message}
        </div>
      ))}
    </div>
  );
}
