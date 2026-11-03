export const CATEGORIES = ["数码", "家电", "服饰", "鞋靴", "家居", "食品", "饰品", "奢侈品"];

export const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export type Customer = {
  customer_id: string;
  vip_level: string;
  register_date: string;
  refund_history_count: number;
  test_scenario: string | null;
  test_hints: string[];
};

export type ProductCard = {
  product_id: string;
  title: string;
  brand: string;
  subtitle: string;
  category: string;
  price: number;
  original_price: number | null;
  rating: number;
  review_count: number;
  monthly_sales: number;
  tags: string[];
  icon: string;
  palette: string;
  is_fragile: boolean;
  is_customized: boolean;
};

export type ProductDetail = ProductCard & {
  highlights: string[];
  description: string;
  specs: { label: string; value: string }[];
  skus: { sku_id: string; sku_name: string }[];
  reviews: { user: string; rating: number; date: string; sku_name: string; content: string }[];
  service_promises: { title: string; detail: string }[];
};

export type ProductCategory = { category: string; product_count: number };

export type OrderStatus = "PendingShipment" | "Shipped" | "Delivered";

export type CustomerOrder = {
  order_id: string;
  customer_id: string;
  product_id: string;
  product_category: string | null;
  quantity: number;
  order_date: string;
  shipped_date: string | null;
  promised_ship_date: string | null;
  estimated_delivery_date: string | null;
  delivered_date: string | null;
  amount: number;
  payment_status: string;
  order_status: OrderStatus;
  product: ProductCard | null;
};

export type LogisticsEvent = {
  carrier: string;
  exception_type: string;
  exception_recorded_date: string;
  resolved: boolean;
};

export type OrderItem = {
  order_item_id: string;
  order_id: string;
  product_id: string;
  sku_id: string;
  sku_name: string;
  unit_price: number;
  quantity: number;
  discount_amount: number;
  paid_amount: number;
  shipped_product_id: string | null;
  shipped_quantity: number | null;
};

export type OrderTracking = {
  order_id: string;
  carrier: string | null;
  tracking_number: string | null;
  current_status: string | null;
  checkpoints: { time: string; location: string; description: string }[];
};

export type OrderDetail = CustomerOrder & {
  logistics: LogisticsEvent[];
  order_items: OrderItem[];
  refunded_amount: number;
  tracking: OrderTracking | null;
};

export type EvidenceRecord = {
  evidence_id: string;
  request_id: string;
  original_filename: string;
  media_type: string;
  file_size_bytes: number;
  verification_status: string;
  customer_description: string | null;
  submitted_date?: string;
  file_url?: string;
  review_notes?: string | null;
};

export type CustomerEvidenceReview = {
  evidence_id: string;
  verification_status: "Verified" | "Rejected";
  review_notes: string | null;
  reviewed_at: string | null;
};

export type CustomerRequestAction = {
  action_id: string; action_type: string; workflow_status: string;
  external_transaction_id: string | null; created_at: string | null; updated_at: string | null;
};

export type ReturnOrder = {
  return_order_id: string;
  request_id: string;
  order_id: string;
  quantity: number;
  refundable_amount: number | string;
  workflow_status: "AwaitingShipment" | "InTransit" | "AwaitingInspection" | "InspectionAccepted" | "InspectionRejected" | string;
  return_carrier: string | null;
  return_tracking_number: string | null;
  shipped_at: string | null;
  received_at: string | null;
  inspected_at: string | null;
  inspection_result: string | null;
  inspection_notes: string | null;
  return_address?: { recipient: string; phone: string; address: string };
};

export type CustomerServiceRequestProgress = {
  request_id: string; order_id: string; request_type: string; request_reason: string;
  requested_quantity: number; request_date: string; workflow_status: string;
  decision: string | null; approved_quantity: number | null; approved_amount: number | null;
  resolution_notes: string | null; resolved_at: string | null;
  evidence_reviews: CustomerEvidenceReview[]; actions: CustomerRequestAction[];
  evidence: EvidenceRecord[];
  return_order: ReturnOrder | null;
};

export async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function yuan(value: number | null | undefined): string {
  return `¥${(value ?? 0).toFixed(2)}`;
}

export function compactCount(value: number): string {
  if (value >= 10000) return `${(value / 10000).toFixed(1).replace(/\.0$/, "")}万+`;
  if (value >= 1000) return `${Math.floor(value / 100) * 100}+`;
  return String(value);
}

export const orderStatusLabels: Record<OrderStatus, string> = {
  PendingShipment: "待发货",
  Shipped: "运输中",
  Delivered: "已签收",
};

export const vipLabels: Record<string, string> = {
  Normal: "普通会员",
  Silver: "白银会员",
  Gold: "黄金会员",
  Platinum: "铂金会员",
};

export const requestWorkflowStatusLabels: Record<string, string> = {
  Pending: "待处理",
  InReview: "审核中",
  NeedsReflection: "复核中",
  EscalatedToHuman: "已转人工",
  ExecutionPending: "退款处理中",
  Executing: "退款处理中",
  ReturnAwaitingShipment: "待寄回商品",
  ReturnInTransit: "退货运输中",
  ReturnAwaitingInspection: "退货验收中",
  Closed: "已关闭",
};

export const RETURN_CARRIERS = ["顺丰速运", "京东物流", "中通快递", "圆通速递", "韵达快递", "申通快递", "邮政 EMS"];

export const evidenceStatusLabels: Record<string, string> = {
  Pending: "待审核",
  MachineReviewed: "机审通过",
  NeedsHumanReview: "待人工复核",
  Verified: "已通过",
  Rejected: "未通过",
};
