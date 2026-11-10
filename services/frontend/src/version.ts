// Injected at build time: Dockerfile ARG VITE_APP_VERSION (Jenkins passes the git sha). 'dev' for local runs.
export const APP_VERSION: string = (import.meta.env.VITE_APP_VERSION as string | undefined) || 'dev'
