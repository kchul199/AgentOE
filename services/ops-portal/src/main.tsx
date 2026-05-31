/**
 * 앱 진입점 (Phase N — N1.11).
 *
 * Provider 순서:
 *   QueryClientProvider → AuthProvider → SSEProvider → BrowserRouter → App
 *
 * - QueryClientProvider: react-query 캐시 공유
 * - AuthProvider: 인증 상태 + 자동 refresh
 * - SSEProvider: SSE 채널 (authenticated 후 활성화)
 * - BrowserRouter: 라우팅
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./providers/AuthProvider";
import { SSEProvider } from "./providers/SSEProvider";
import "./styles/app.css";
import App from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <SSEProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </SSEProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>
);
