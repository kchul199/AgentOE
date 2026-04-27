/**
 * 우상단 토스트 스택 — store.toasts 를 구독해 level 별 색상으로 렌더.
 *
 * 디자인:
 *   - success (녹색) / info (파랑) / error (빨강). Error 는 수동 dismiss 전용.
 *   - stack 가장 위가 최신. 최신 순 정렬 — createdAt desc.
 *   - 모든 토스트는 클릭 시 즉시 dismiss.
 */
import { useBuilderStore, type Toast } from "@/store/builderStore";

const LEVEL_STYLE: Record<Toast["level"], { bg: string; fg: string; border: string }> = {
  success: { bg: "#ecfdf5", fg: "#065f46", border: "#10b981" },
  info: { bg: "#eff6ff", fg: "#1e3a8a", border: "#3b82f6" },
  error: { bg: "#fef2f2", fg: "#991b1b", border: "#ef4444" },
};

export default function ToastStack(): JSX.Element | null {
  const toasts = useBuilderStore((s) => s.toasts);
  const dismiss = useBuilderStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;

  // 최신 순
  const ordered = [...toasts].sort((a, b) => b.createdAt - a.createdAt);

  return (
    <div
      aria-live="polite"
      style={{
        position: "fixed",
        top: 16,
        right: 16,
        zIndex: 100,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        maxWidth: 360,
      }}
    >
      {ordered.map((t) => {
        const s = LEVEL_STYLE[t.level];
        return (
          <div
            key={t.id}
            role="alert"
            onClick={() => dismiss(t.id)}
            style={{
              background: s.bg,
              color: s.fg,
              border: `1px solid ${s.border}`,
              borderLeft: `4px solid ${s.border}`,
              borderRadius: 6,
              padding: "10px 14px",
              fontSize: 13,
              cursor: "pointer",
              boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
              lineHeight: 1.4,
              wordBreak: "break-word",
            }}
            title="클릭해서 닫기"
          >
            <strong style={{ textTransform: "uppercase", fontSize: 10, letterSpacing: 0.6 }}>
              {t.level}
            </strong>
            <div style={{ marginTop: 2 }}>{t.message}</div>
          </div>
        );
      })}
    </div>
  );
}
