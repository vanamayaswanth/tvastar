export interface Pagination {
  page: number;
  page_size: number;
  total: number;
}

export interface ApiResponse<T> {
  data: T;
  pagination?: Pagination;
}
