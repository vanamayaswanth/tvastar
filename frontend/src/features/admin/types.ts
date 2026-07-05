/** Admin TypeScript interfaces */
export interface TenantHealth {
  tenantId: string;
  activeWorkflows: number;
  errorRate: number;
  slaCompliance: number;
}
