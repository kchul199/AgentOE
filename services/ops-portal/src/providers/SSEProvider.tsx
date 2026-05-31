/**
 * SSEProvider — 4채널 SSE 구독 관리 (Phase N — N1.11).
 *
 * - authenticated 상태일 때만 연결. 로그아웃 시 자동 disconnect.
 * - 각 채널을 SseClient 인스턴스로 관리.
 * - useSSE(channel) hook 으로 최신 이벤트 데이터 구독.
 *
 * 채널:
 *   metrics, sessions.active, audit.tail, alerts
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { SseClient, SSE_CHANNELS, type SseChannelKey } from "../lib/sse";
import { useAuth } from "./AuthProvider";

// ── 타입 ──────────────────────────────────────────────────────────────────

export interface SseMessage {
  data: string;
  eventType: string;
  receivedAt: number;
}

type ChannelState = Map<SseChannelKey, SseMessage | null>;

interface SseContextValue {
  subscribe: (channel: SseChannelKey) => void;
  unsubscribe: (channel: SseChannelKey) => void;
  latest: (channel: SseChannelKey) => SseMessage | null;
}

// ── context ────────────────────────────────────────────────────────────────

const SseContext = createContext<SseContextValue | null>(null);

export function useSSE(channel: SseChannelKey): SseMessage | null {
  const ctx = useContext(SseContext);
  if (!ctx) throw new Error("useSSE must be used inside <SSEProvider>");

  // subscribe on mount, unsubscribe on unmount
  useEffect(() => {
    ctx.subscribe(channel);
    return () => ctx.unsubscribe(channel);
  }, [channel]); // eslint-disable-line react-hooks/exhaustive-deps

  return ctx.latest(channel);
}

// ── provider ───────────────────────────────────────────────────────────────

export function SSEProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth();

  // 채널별 SseClient 인스턴스
  const clients = useRef<Partial<Record<SseChannelKey, SseClient>>>({});
  // 채널 ref count (여러 컴포넌트가 같은 채널 구독 가능)
  const refCount = useRef<Partial<Record<SseChannelKey, number>>>({});
  // 최신 메시지 (렌더 트리거용)
  const [messages, setMessages] = useState<ChannelState>(new Map());

  const updateMessage = useCallback((channel: SseChannelKey, data: string, eventType: string) => {
    setMessages((prev) => {
      const next = new Map(prev);
      next.set(channel, { data, eventType, receivedAt: Date.now() });
      return next;
    });
  }, []);

  const openChannel = useCallback((channel: SseChannelKey) => {
    if (clients.current[channel]) return; // 이미 열려있음
    const url = SSE_CHANNELS[channel];
    const client = new SseClient(url);
    client.on("message", (data, eventType) => updateMessage(channel, data, eventType));
    client.connect();
    clients.current[channel] = client;
  }, [updateMessage]);

  const closeChannel = useCallback((channel: SseChannelKey) => {
    clients.current[channel]?.close();
    delete clients.current[channel];
    setMessages((prev) => {
      const next = new Map(prev);
      next.delete(channel);
      return next;
    });
  }, []);

  // ── authenticated 전환 / 로그아웃 처리 ────────────────────────────────────

  useEffect(() => {
    if (status !== "authenticated") {
      // 로그아웃 또는 로딩 중 — 모든 채널 닫기
      (Object.keys(clients.current) as SseChannelKey[]).forEach(closeChannel);
    }
    // authenticated 가 되면 subscribe() 호출 측이 openChannel() 트리거
  }, [status, closeChannel]);

  // ── unmount 시 전체 정리 ──────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      (Object.keys(clients.current) as SseChannelKey[]).forEach((ch) => {
        clients.current[ch]?.close();
      });
    };
  }, []);

  // ── public API ────────────────────────────────────────────────────────────

  const subscribe = useCallback((channel: SseChannelKey) => {
    refCount.current[channel] = (refCount.current[channel] ?? 0) + 1;
    if (status === "authenticated") {
      openChannel(channel);
    }
  }, [status, openChannel]);

  const unsubscribe = useCallback((channel: SseChannelKey) => {
    const count = (refCount.current[channel] ?? 1) - 1;
    refCount.current[channel] = count;
    if (count <= 0) {
      delete refCount.current[channel];
      closeChannel(channel);
    }
  }, [closeChannel]);

  const latest = useCallback((channel: SseChannelKey): SseMessage | null => {
    return messages.get(channel) ?? null;
  }, [messages]);

  const value: SseContextValue = { subscribe, unsubscribe, latest };

  return <SseContext.Provider value={value}>{children}</SseContext.Provider>;
}
