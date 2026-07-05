import { useEffect, useRef, useState } from "react";

export function useWebSocket(url: string) {
  const ws = useRef<WebSocket | null>(null);
  const [lastMessage, setLastMessage] = useState<unknown>(null);

  useEffect(() => {
    ws.current = new WebSocket(url);
    ws.current.onmessage = (e) => setLastMessage(JSON.parse(e.data));
    return () => ws.current?.close();
  }, [url]);

  return { lastMessage, ws: ws.current };
}
