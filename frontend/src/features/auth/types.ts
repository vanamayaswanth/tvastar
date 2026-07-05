export interface User {
  id: string;
  email: string;
  name: string;
  role: "super_admin" | "tenant_admin" | "sales_manager" | "salesperson";
  tenant_id: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
}
