/**
 * SSE 클라이언트 — 지수 백오프 + Last-Event-ID 재연결 (Phase N — N1.11).
 *
 * 사용:
 *   const client = new SseClient("/api/v1/stream/audit.tail");
 *   client.on("message", (data) => console.log(data));
 *   client.connect();
 *   // 정리
 *   client.close();
 *
 * 채널 경로:
 *   /api/v1/stream/metrics
 *   /api/v1/stream/sessions.active
 *   /api/v1/stream/audit.tail
 *   /api/v1/stream/alerts
 */

export type SseEventType = "message" | "heartbeat" | "open" | "error" | "close";

export type SseListener = (data: string, eventType: string) => void;

const MIN_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;
const JITTER_RATIO = 0.2; // ±20%

function withJitter(ms: number): number {
  const jitter = ms * JITTER_RATIO * (Math.random() * 2 - 1);
  return Math.min(MAX_DELAY_MS, Math.max(MIN_DELAY_MS, ms + jitter));
}

export class SseClient {
  private readonly url: string;
  private es: EventSource | null = null;
  private lastEventId: string = "";
  private delay: number = MIN_DELAY_MS;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed: boolean = false;

  private listeners: Map<string, Set<SseListener>> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  // ── 이벤트 구독 ───────────────────────────────────────────────────────────

  on(event: string, listener: SseListener): this {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(listener);
    return this;
  }

  off(event: string, listener: SseListener): this {
    this.listeners.get(event)?.delete(listener);
    return this;
  }

  private emit(event: string, data: string = "", eventType: string = event) {
    this.listeners.get(event)?.forEach((fn) => fn(data, eventType));
  }

  // ── 연결 ─────────────────────────────────────────────────────────────────

  connect(): void {
    if (this.closed) return;
    if (this.es) this._teardown();

    const url = this.lastEventId
      ? `${this.url}${this.url.includes("?") ? "&" : "?"}lastEventId=${encodeURIComponent(this.lastEventId)}`
      : this.url;

    const es = new EventSource(url, { withCredentials: true });
    this.es = es;

    es.onopen = () => {
      this.delay = MIN_DELAY_MS; // 성공 시 백오프 리셋
      this.emit("open");
    };

    es.onmessage = (ev) => {
      if (ev.lastEventId) this.lastEventId = ev.lastEventId;
      this.emit("message", ev.data, ev.type || "message");
    };

    // named event 핸들러 (heartbeat, audit, session, alert 등)
    const namedEvents = ["heartbeat", "audit", "session", "alert", "metric"];
    for (const name of namedEvents) {
      es.addEventListener(name, (ev: MessageEvent) => {
        if (ev.lastEventId) this.lastEventId = ev.lastEventId;
        this.emit(name, ev.data, name);
        // "message" 리스너도 receive (통합 청취 패턴 지원)
        this.emit("message", ev.data, name);
      });
    }

    es.onerror = () => {
      this._teardown();
      if (!this.closed) {
        this.emit("error");
        this._scheduleReconnect();
      }
    };
  }

  private _teardown() {
    if (this.es) {
      this.es.close();
      this.es = null;
    }
  }

  private _scheduleReconnect() {
    const wait = withJitter(this.delay);
    this.delay = Math.min(MAX_DELAY_MS, this.delay * 2);
    this.reconnectTimer = setTimeout(() => {
      if (!this.closed) this.connect();
    }, wait);
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._teardown();
    this.emit("close");
  }

  // 재사용 (예: 로그아웃 후 재로그인)
  reopen(): void {
    this.closed = false;
    this.lastEventId = "";
    this.delay = MIN_DELAY_MS;
    this.connect();
  }
}

// ── 채널 상수 ────────────────────────────────────────────────────────────────

export const SSE_CHANNELS = {
  METRICS:          "/api/v1/stream/metrics",
  SESSIONS_ACTIVE:  "/api/v1/stream/sessions.active",
  AUDIT_TAIL:       "/api/v1/stream/audit.tail",
  ALERTS:           "/api/v1/stream/alerts",
} as const;

export type SseChannelKey = keyof typeof SSE_CHANNELS;
