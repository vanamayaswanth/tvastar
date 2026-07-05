export const env = {
  API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  WS_URL: process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000",
} as const;
