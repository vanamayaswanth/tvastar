export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN");
}

export function formatCurrency(amount: number): string {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(amount);
}

export function formatPhone(phone: string): string {
  // ponytail: Basic Indian phone formatting. Upgrade: libphonenumber if international needed.
  return phone.replace(/(\d{5})(\d{5})/, "$1 $2");
}
