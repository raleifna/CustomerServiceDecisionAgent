import { API_BASE, OrderItem, OrderTracking, ProductCard } from "../shop/api";

export type AiRecommendation = {
  decision: string | null;
  approved_quantity: number | null;
  approved_amount: number | null;
  decision_confidence: number | null;
  matched_rule_id: string | null;
  rule_hits: string[];
  reasons: string[];
};

export type StaffRequest = {
  request_id: string;
  order_id: string;
  customer_id: string;
  request_type: string;
  request_reason: string;
  target_order_item_id: string | null;
  requested_quantity: number;
  request_date: string;
  workflow_status: string;
  decision: string | null;
  reasons: string[];
  rule_hits: string[];
  required_actions: string[];
  assigned_to: string | null;
  lease_expires_at: string | null;
  row_version: number;
  approved_quantity: number | null;
  approved_amount: number | null;
  decision_confidence: number | null;
  resolved_by: string | null;
  resolution_notes: string | null;
  resolved_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  product: ProductCard | null;
  order_amount: number | null;
  ai_recommendation: AiRecommendation | null;
};

export type StaffEvidence = {
  evidence_id: string;
  request_id: string;
  original_filename: string;
  media_type: string;
  verification_status: string;
  submitted_date: string;
  file_url: string;
  customer_description: string | null;
  review_notes: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
};

export type StaffAction = {
  action_id: string;
  request_id: string;
  action_type: string;
  workflow_status: string;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  external_transaction_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type StaffQueue = {
  service_requests: StaffRequest[];
  evidence: StaffEvidence[];
  failed_actions: StaffAction[];
};

export type StaffReturnOrder = {
  return_order_id: string;
  request_id: string;
  order_id: string;
  customer_id: string;
  order_item_id: string;
  quantity: number;
  refundable_amount: number;
  workflow_status: string;
  return_carrier: string | null;
  return_tracking_number: string | null;
  shipped_at: string | null;
  received_at: string | null;
  inspected_at: string | null;
  inspection_result: string | null;
  inspection_notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  request_reason: string | null;
  product: ProductCard | null;
};

export type RequestItem = {
  request_item_id: string;
  request_id: string;
  order_item_id: string;
  requested_quantity: number;
  approved_quantity: number | null;
  approved_amount: number | null;
};

export type StaffDetail = {
  service_request: StaffRequest;
  order: Record<string, unknown> | null;
  customer: {
    customer_id: string;
    vip_level: string;
    register_date: string | null;
    refund_history_count: number;
    canceled_order_count?: number;
    source?: "v2";
  } | null;
  logistics: { carrier: string; exception_type: string; exception_recorded_date: string; resolved: boolean }[];
  evidence: StaffEvidence[];
  order_items: OrderItem[];
  request_items: RequestItem[];
  tracking: OrderTracking | null;
  return_order: StaffReturnOrder | null;
  actions: StaffAction[];
  customer_requests: StaffRequest[];
};

export type StaffIdentity = { staff_id: string; roles: string[] };

export class StaffApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function staffFetch<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}/staff${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : Array.isArray(body?.detail) ? body.detail[0]?.msg : null;
    throw new StaffApiError(detail || `请求失败 (${response.status})`, response.status);
  }
  return body as T;
}

// Requests a human can act on: the backend's claimable statuses.
export const HUMAN_QUEUE_STATUSES = ["EscalatedToHuman", "NeedsReflection"];

export const requestStatusLabels: Record<string, string> = {
  Pending: "待处理",
  InReview: "审核中",
  NeedsReflection: "待复核 / 补证",
  EscalatedToHuman: "转人工",
  ExecutionPending: "待执行",
  Executing: "执行中",
  ReturnAwaitingShipment: "待客户寄回",
  ReturnInTransit: "退货寄回中",
  ReturnAwaitingInspection: "退货待验收",
  Closed: "已关闭",
};

export const requestStatusTone: Record<string, string> = {
  EscalatedToHuman: "red",
  NeedsReflection: "orange",
  Pending: "blue",
  InReview: "blue",
  ExecutionPending: "blue",
  Executing: "blue",
  ReturnAwaitingShipment: "orange",
  ReturnInTransit: "blue",
  ReturnAwaitingInspection: "orange",
  Closed: "gray",
};

export const decisionLabels: Record<string, string> = {
  Approve: "批准",
  PartialApprove: "部分批准",
  Reject: "拒绝",
  NoActionRequired: "无需处理 / 关闭",
};

export const evidenceStatusLabels: Record<string, string> = {
  Pending: "待审核",
  MachineReviewed: "机审通过",
  NeedsHumanReview: "待人工复核",
  Verified: "已通过",
  Rejected: "已驳回",
};

export const returnStatusLabels: Record<string, string> = {
  AwaitingShipment: "等待客户寄回",
  InTransit: "寄回运输中",
  AwaitingInspection: "已收货待验收",
  InspectionAccepted: "验收通过",
  InspectionRejected: "验收不通过",
};

export const actionTypeLabels: Record<string, string> = {
  RefundPayment: "原路退款",
  CancelOrder: "取消订单",
  CreateExchange: "换货",
  ReshipItem: "补发商品",
};

export const actionStatusLabels: Record<string, string> = {
  Pending: "待执行",
  Retrying: "重试中",
  Executing: "执行中",
  Succeeded: "已成功",
  Failed: "失败",
};

export const vipLabels: Record<string, string> = {
  Normal: "普通会员",
  Bronze: "青铜会员",
  Silver: "白银会员",
  Gold: "黄金会员",
  Platinum: "铂金会员",
};

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

// "等待 3 小时" style relative age, from an ISO timestamp or a plain date.
export function ageText(value: string | null | undefined): string {
  if (!value) return "—";
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (Number.isNaN(minutes)) return "—";
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} 小时`;
  return `${Math.floor(minutes / 1440)} 天`;
}

export function ageHours(value: string | null | undefined): number {
  if (!value) return 0;
  return (Date.now() - new Date(value).getTime()) / 3600000;
}

const APPROVING = new Set(["Approve", "PartialApprove"]);

/** Mirrors services/ai_review_service.classify on the backend. */
export function aiAgreement(ai: AiRecommendation | null, human: string | null, humanAmount: number | null): string {
  if (!ai?.decision) return "no_suggestion";
  if (ai.decision === human) {
    if (APPROVING.has(ai.decision) && ai.approved_amount != null && humanAmount != null && Math.abs(ai.approved_amount - humanAmount) > 0.01) return "amount_differs";
    return "agree";
  }
  if (APPROVING.has(ai.decision) && human && APPROVING.has(human)) return "amount_differs";
  if (APPROVING.has(ai.decision)) return "ai_more_lenient";
  if (human && APPROVING.has(human)) return "ai_more_strict";
  return "other";
}

export const agreementLabels: Record<string, string> = {
  agree: "与 AI 建议一致",
  amount_differs: "金额 / 数量与 AI 不同",
  ai_more_lenient: "AI 更宽松（人工收紧）",
  ai_more_strict: "AI 更严格（人工放宽）",
  other: "结论不同",
  no_suggestion: "AI 无建议",
};
