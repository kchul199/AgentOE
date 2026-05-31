/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 시나리오 저작 도구(별도 frontend 서비스) URL. 미설정 시 로컬 dev 포트로 폴백. */
  readonly VITE_SCENARIO_BUILDER_URL?: string;
  /** 로컬 개발 시 backend proxy target (vite.config.ts 에서 사용). */
  readonly VITE_API_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
