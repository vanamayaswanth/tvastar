import { useWebSocket } from "@/shared/hooks/useWebSocket";

export function useNotifications() {
  const { lastMessage } = useWebSocket("/ws/notifications");
  return { lastNotification: lastMessage };
}
