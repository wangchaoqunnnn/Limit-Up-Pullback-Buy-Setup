/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 仅开发态 Vite proxy 使用，不进入前端产物 */
  readonly VITE_API_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module '*.css';
