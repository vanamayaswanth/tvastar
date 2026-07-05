export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <nav className="w-64 border-r">{/* Sidebar */}</nav>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
