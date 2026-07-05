export function NotificationToast({ message }: { message: string }) {
  return (
    <div role="alert" className="fixed bottom-4 right-4 rounded bg-blue-600 px-4 py-2 text-white">
      {message}
    </div>
  );
}
