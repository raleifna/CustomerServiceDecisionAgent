import {
  Activity,
  ArrowLeft,
  Bot,
  Box,
  CheckCircle2,
  CircleDot,
  FileText,
  HelpCircle,
  ImageUp,
  LoaderCircle,
  MessageSquare,
  Package,
  Plus,
  RotateCcw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Truck,
  UserRound,
  Zap,
} from "lucide-react";
import { Fragment, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

type MessageRole = "agent" | "customer" | "system";

type Message = {
  id: string;
  role: MessageRole;
  content: string;
  time: string;
};

type ChatResponse = {
  conversation_id: string;
  message: string;
  intent: string | null;
  order_id: string | null;
  request_id: string | null;
  request_reason: string | null;
  handling_status: string | null;
  decision: string | null;
  action: string | null;
  action_id: string | null;
};

type Customer = {
  customer_id: string;
  vip_level: string;
  register_date: string;
  refund_history_count: number;
};

type CustomerOrder = {
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
  order_status: "PendingShipment" | "Shipped" | "Delivered";
};

type LogisticsEvent = {
  carrier: string;
  exception_type: string;
  exception_recorded_date: string;
  resolved: boolean;
};

type OrderItem = {
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

type OrderDetail = CustomerOrder & {
  logistics: LogisticsEvent[];
  order_items: OrderItem[];
  refunded_amount: number;
};

type EvidenceRecord = {
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

type ServiceRequestStatus = {
  request_id: string;
  workflow_status: string;
  decision: string | null;
  approved_amount: number | null;
  resolution_notes: string | null;
  resolved_at: string | null;
  return_order: ReturnOrder | null;
  evidence_reviews: CustomerEvidenceReview[];
  actions: CustomerRequestAction[];
};

type CustomerEvidenceReview = {
  evidence_id: string;
  verification_status: "Verified" | "Rejected";
  review_notes: string | null;
  reviewed_at: string | null;
};

type CustomerRequestAction = {
  action_id: string; action_type: string; workflow_status: string;
  external_transaction_id: string | null; created_at: string | null; updated_at: string | null;
};

type CustomerServiceRequestProgress = {
  request_id: string; order_id: string; request_type: string; request_reason: string;
  requested_quantity: number; request_date: string; workflow_status: string;
  decision: string | null; approved_quantity: number | null; approved_amount: number | null;
  resolution_notes: string | null; resolved_at: string | null;
  evidence_reviews: CustomerEvidenceReview[]; actions: CustomerRequestAction[];
  evidence: EvidenceRecord[];
};

type ReturnOrder = {
  return_order_id: string;
  workflow_status: string;
  return_carrier: string | null;
  return_tracking_number: string | null;
  inspection_notes: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

const quickPrompts = [
  "那这个订单的物流呢？",
  "售后进度怎么样？",
  "需要",
  "我想申请退款",
];

const intentLabels: Record<string, string> = {
  ShippingTimeQuery: "发货时间查询",
  OrderStatusQuery: "订单状态查询",
  LogisticsQuery: "物流查询",
  AfterSalesStatusQuery: "售后进度查询",
  AfterSalesRequest: "售后申请",
  ExpediteShipmentConfirmation: "确认催发货",
  Unknown: "待澄清",
};

const decisionLabels: Record<string, string> = {
  Approve: "批准",
  PartialApprove: "部分批准",
  Reject: "拒绝",
};

const requestWorkflowStatusLabels: Record<string, string> = {
  Pending: "待处理",
  InReview: "审核中",
  NeedsReflection: "复核中",
  EscalatedToHuman: "已转人工",
  Closed: "已关闭",
};

const evidenceStatusLabels: Record<string, string> = {
  Pending: "待审核",
  MachineReviewed: "机审通过",
  NeedsHumanReview: "待人工复核",
  Verified: "已通过",
  Rejected: "未通过",
};

function now() {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function createConversationId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `web-${crypto.randomUUID()}`;
  }
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function initialMessages(): Message[] {
  return [
    {
      id: "welcome",
      role: "agent",
      content:
        "您好，我是 ShopCare 智能售后助手。您可以提供订单号，查询发货、物流和售后进度，也可以提出退款或换货需求。",
      time: now(),
    },
  ];
}

// Each evidence-upload card in the chat is anchored to the id of the agent
// message that prompted it (the initial request-created message, or a later
// rejection notice) so re-submitting after a rejection opens a fresh card
// instead of overwriting the one that already shows "证据已提交".
type EvidenceCardState = {
  requestId: string;
  uploaded: EvidenceRecord | null;
};

type ThreadState = {
  conversationId: string;
  messages: Message[];
  lastResult: ChatResponse | null;
  evidenceCards: Record<string, EvidenceCardState>;
};

const UNASSIGNED_THREAD_KEY = "__unassigned__";

function createThreadState(): ThreadState {
  return {
    conversationId: createConversationId(),
    messages: initialMessages(),
    lastResult: null,
    evidenceCards: {},
  };
}

// Threads live in localStorage (not just the in-memory threadsRef Map) so
// navigating away to /staff and back — a full page load, since that link
// is a plain <a href> — doesn't wipe out everything the customer already
// submitted. Best-effort only: private browsing / quota errors degrade to
// "just don't persist," not a crash.
// Bump this namespace whenever the demo runtime is intentionally reset. The
// startup cleanup below removes every older ShopCare thread from the browser.
const THREAD_STORAGE_VERSION = "v4";
const CHAT_RESET_MARKER = "shopcare:chat-reset:20260821-full-reset-5";
const CUS0001_RESET_MARKER = "shopcare:customer-reset:CUS0001:20260820-2305";

function removeStaleThreadStorage() {
  try {
    const requiresFullReset = window.localStorage.getItem(CHAT_RESET_MARKER) !== "done";
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (
        key?.startsWith("shopcare:threads:")
        && (requiresFullReset || !key.startsWith(`shopcare:threads:${THREAD_STORAGE_VERSION}:`))
      ) {
        window.localStorage.removeItem(key);
      }
    }
    if (requiresFullReset) window.localStorage.setItem(CHAT_RESET_MARKER, "done");
    if (window.localStorage.getItem(CUS0001_RESET_MARKER) !== "done") {
      window.localStorage.removeItem(threadStorageKey("CUS0001"));
      window.localStorage.setItem(CUS0001_RESET_MARKER, "done");
    }
  } catch {
    // Storage cleanup is best-effort; a blocked localStorage must not break chat.
  }
}

removeStaleThreadStorage();

function threadStorageKey(customerId: string): string {
  return `shopcare:threads:${THREAD_STORAGE_VERSION}:${customerId}`;
}

function loadPersistedThreads(
  customerId: string,
): { activeKey: string; threads: Map<string, ThreadState> } | null {
  try {
    const raw = window.localStorage.getItem(threadStorageKey(customerId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as {
      activeKey: string;
      threads: Record<string, ThreadState>;
    };
    const threads = new Map<string, ThreadState>(Object.entries(parsed.threads));
    if (threads.size === 0) return null;
    return { activeKey: parsed.activeKey, threads };
  } catch {
    return null;
  }
}

function savePersistedThreads(
  customerId: string,
  threads: Map<string, ThreadState>,
  activeKey: string,
) {
  try {
    window.localStorage.setItem(
      threadStorageKey(customerId),
      JSON.stringify({ activeKey, threads: Object.fromEntries(threads) }),
    );
  } catch {
    // best-effort persistence only
  }
}

function App() {
  const [conversationId, setConversationId] = useState(() => createConversationId());
  const [messages, setMessages] = useState<Message[]>(() => initialMessages());
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [lastResult, setLastResult] = useState<ChatResponse | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [orders, setOrders] = useState<CustomerOrder[]>([]);
  const [activeCustomerId, setActiveCustomerId] = useState("CUS0021");
  const [hasSelectedCustomer, setHasSelectedCustomer] = useState(false);
  const [activeOrderId, setActiveOrderId] = useState<string | null>(null);
  const [orderDetail, setOrderDetail] = useState<OrderDetail | null>(null);
  const [evidenceCards, setEvidenceCards] = useState<Record<string, EvidenceCardState>>({});
  const [evidenceCardDescriptions, setEvidenceCardDescriptions] = useState<Record<string, string>>({});
  const [uploadingCardId, setUploadingCardId] = useState<string | null>(null);
  const [returnOrder, setReturnOrder] = useState<ReturnOrder | null>(null);
  const [activeWorkspace, setActiveWorkspace] = useState<"chat" | "requests">("chat");
  const [customerRequests, setCustomerRequests] = useState<CustomerServiceRequestProgress[]>([]);
  const [isLoadingRequests, setIsLoadingRequests] = useState(false);
  const [withdrawingRequestId, setWithdrawingRequestId] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<"checking" | "online" | "offline">("checking");
  const [requestPageUploadingId, setRequestPageUploadingId] = useState<string | null>(null);
  const [requestPageDescriptions, setRequestPageDescriptions] = useState<Record<string, string>>({});
  const [requestPageUploadedEvidence, setRequestPageUploadedEvidence] = useState<Record<string, EvidenceRecord>>({});
  const [requestPageUploadErrors, setRequestPageUploadErrors] = useState<Record<string, string>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const threadsRef = useRef<Map<string, ThreadState>>(new Map());
  const requestPollingAbortRef = useRef<AbortController | null>(null);
  const resumeAttemptedRef = useRef(false);
  // The status-polling effect below only re-runs when a handful of coarse
  // deps change, so its closure over `messages` goes stale between runs.
  // This ref always reflects the latest committed messages so the polling
  // loop can correctly tell "already showed this" from "just arrived".
  const messagesRef = useRef<Message[]>(messages);
  // The persist-effect below must not run before hydration has had a chance
  // to apply on mount, or it stomps the freshly-loaded thread with the
  // pre-hydration default state (see comment on the effect itself).
  const skipNextPersistRef = useRef(true);

  useEffect(() => {
    if (!hasSelectedCustomer) return;
    if (skipNextPersistRef.current) {
      skipNextPersistRef.current = false;
      return;
    }
    const key = activeOrderId ?? UNASSIGNED_THREAD_KEY;
    threadsRef.current.set(key, {
      conversationId,
      messages,
      lastResult,
      evidenceCards,
    });
    savePersistedThreads(activeCustomerId, threadsRef.current, key);
  }, [conversationId, messages, lastResult, evidenceCards, activeOrderId, activeCustomerId, hasSelectedCustomer]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health check failed");
        setConnectionState("online");
      })
      .catch(() => setConnectionState("offline"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!hasSelectedCustomer) return;
    const requestIdOrNull = lastResult?.request_id;
    if (!requestIdOrNull) return;
    const requestId: string = requestIdOrNull;
    if (lastResult?.action === "WithdrawServiceRequest") return;
    const needsPolling = !lastResult?.decision;

    const controller = new AbortController();
    requestPollingAbortRef.current?.abort();
    requestPollingAbortRef.current = controller;
    let stopped = false;

    async function refreshRequestStatus() {
      try {
        const response = await fetch(
          `${API_BASE}/service-requests/${requestId}/status?customer_id=${encodeURIComponent(activeCustomerId)}`,
          { signal: controller.signal },
        );
        if (response.status === 404) {
          if (stopped) return;
          const fresh = createThreadState();
          threadsRef.current.set(activeOrderId ?? UNASSIGNED_THREAD_KEY, fresh);
          setConversationId(fresh.conversationId);
          setMessages(fresh.messages);
          setLastResult(null);
          setEvidenceCards({});
          return;
        }
        if (!response.ok) return;
        const status = (await response.json()) as ServiceRequestStatus;
        if (stopped) return;
        setReturnOrder(status.return_order);

        const existingMessageIds = new Set(messagesRef.current.map((message) => message.id));
        const evidenceReviewMessages = (status.evidence_reviews || [])
          .map((review) => {
            const messageId = `evidence-review-${review.evidence_id}-${review.verification_status}`;
            if (review.verification_status === "Rejected") {
              const reason = review.review_notes || "当前材料无法有效支持您描述的问题";
              return {
                id: messageId,
                role: "agent" as const,
                content: `您为售后申请 ${requestId} 提交的材料未通过证据审核。驳回原因：${reason}。这不代表售后申请已被拒绝，请根据原因重新上传与商品问题相关、清晰可辨的材料，我们会在新材料审核后继续处理。`,
                time: now(),
              };
            }
            return {
              id: messageId,
              role: "agent" as const,
              content: `您为售后申请 ${requestId} 提交的材料已通过证据审核，申请正在继续处理，请留意后续审核结果。`,
              time: now(),
            };
          })
          .filter((message) => !existingMessageIds.has(message.id));
        if (evidenceReviewMessages.length > 0) {
          setMessages((current) => [...current, ...evidenceReviewMessages]);
        }
        // A rejection means the customer needs to submit new material — open
        // a fresh upload card anchored to the rejection notice, leaving the
        // earlier card (and its "已提交" state) exactly where it was.
        const newRejection = evidenceReviewMessages.find((message) => message.id.endsWith("-Rejected"));
        if (newRejection) {
          setEvidenceCards((current) => ({
            ...current,
            [newRejection.id]: { requestId, uploaded: null },
          }));
        }

        const refundAction = (status.actions || []).find(
          (action) => action.action_type === "RefundPayment",
        );
        if (refundAction?.workflow_status === "Succeeded") {
          const refundMessageId = `refund-succeeded-${requestId}-${refundAction.action_id}`;
          setMessages((current) => {
            if (current.some((message) => message.id === refundMessageId)) return current;
            const amountText = status.approved_amount != null
              ? `¥${status.approved_amount.toFixed(2)}`
              : "";
            return [...current, {
              id: refundMessageId,
              role: "agent",
              content: `您的售后申请 ${requestId} 退款已成功，金额 ${amountText}，`
                + `退款流水号 ${refundAction.external_transaction_id ?? "—"}，`
                + `将按原支付路径退回，请注意查收。`,
              time: now(),
            }];
          });
        }

        if (!status.decision) return;

        const wasCustomerWithdrawal = (
          status.decision === "NoActionRequired"
          && status.workflow_status === "Closed"
          && status.resolution_notes?.includes("客户主动撤销")
        );
        if (wasCustomerWithdrawal) {
          if (status.decision !== lastResult?.decision) {
            setLastResult((current) => {
              if (!current || current.request_id !== requestId) return current;
              return { ...current, request_id: null, decision: status.decision };
            });
          }
          return;
        }

        const decisionLabels: Record<string, string> = {
          Approve: "审核通过",
          PartialApprove: "部分审核通过",
          Reject: "审核未通过",
          NoActionRequired: "无需进一步处理",
        };
        const conclusion = decisionLabels[status.decision] || status.decision;
        const note = status.resolution_notes ? ` 原因：${status.resolution_notes}` : "";
        setMessages((current) => {
          const messageId = `request-resolution-${requestId}-${status.decision}`;
          if (current.some((message) => message.id === messageId)) return current;
          return [...current, {
            id: messageId,
            role: "agent",
            content: `您的售后申请 ${requestId} 已完成人工审核：${conclusion}。${note}`,
            time: now(),
          }];
        });
        if (status.decision !== lastResult?.decision) {
          setLastResult((current) => {
            if (!current || current.request_id !== requestId) return current;
            return {
              ...current,
              decision: status.decision,
              handling_status: "DecisionCompleted",
            };
          });
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          // A transient polling failure should not interrupt the active chat.
        }
      }
    }

    void refreshRequestStatus();
    const intervalId = needsPolling
      ? window.setInterval(refreshRequestStatus, 3000)
      : null;
    return () => {
      stopped = true;
      controller.abort();
      if (requestPollingAbortRef.current === controller) {
        requestPollingAbortRef.current = null;
      }
      if (intervalId !== null) window.clearInterval(intervalId);
    };
  }, [activeCustomerId, activeOrderId, hasSelectedCustomer, lastResult?.decision, lastResult?.request_id]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/customers`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("客户列表加载失败");
        return response.json() as Promise<Customer[]>;
      })
      .then(setCustomers)
      .catch(() => setConnectionState("offline"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (resumeAttemptedRef.current || customers.length === 0) return;
    resumeAttemptedRef.current = true;
    const resumeCustomerId = new URLSearchParams(window.location.search).get("resumeCustomer");
    if (resumeCustomerId && customers.some((customer) => customer.customer_id === resumeCustomerId)) {
      selectCustomer(resumeCustomerId);
      window.history.replaceState({}, "", "/");
    }
  }, [customers]);

  useEffect(() => {
    if (!hasSelectedCustomer) return;
    const controller = new AbortController();
    fetch(`${API_BASE}/customers/${activeCustomerId}/orders`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("订单列表加载失败");
        return response.json() as Promise<CustomerOrder[]>;
      })
      .then((data) => {
        setOrders(data);
      })
      .catch(() => {
        setOrders([]);
        setActiveOrderId(null);
        setConnectionState("offline");
      });
    return () => controller.abort();
  }, [activeCustomerId, hasSelectedCustomer]);

  useEffect(() => {
    if (!hasSelectedCustomer || activeWorkspace !== "requests") return;
    const controller = new AbortController();
    setIsLoadingRequests(true);
    fetch(`${API_BASE}/customers/${activeCustomerId}/service-requests`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("售后进度加载失败");
        return response.json() as Promise<CustomerServiceRequestProgress[]>;
      })
      .then(setCustomerRequests)
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setCustomerRequests([]);
      })
      .finally(() => setIsLoadingRequests(false));
    return () => controller.abort();
  }, [activeCustomerId, activeWorkspace, hasSelectedCustomer]);

  useEffect(() => {
    setOrderDetail(null);
    if (!activeOrderId) return;

    const controller = new AbortController();
    fetch(`${API_BASE}/customers/${activeCustomerId}/orders/${activeOrderId}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("订单详情加载失败");
        return response.json() as Promise<OrderDetail>;
      })
      .then(setOrderDetail)
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setConnectionState("offline");
      });
    return () => controller.abort();
  }, [activeCustomerId, activeOrderId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  // Runs after the bottom-scroll effect above, so switching context (which also
  // swaps in a different thread's messages) ends up scrolled to top instead of
  // bottom, keeping the newly shown order card visible instead of hidden above the fold.
  useEffect(() => {
    messagesContainerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [activeOrderId]);

  const activeOrder = lastResult?.order_id || activeOrderId;
  const activeCustomer = customers.find((item) => item.customer_id === activeCustomerId);
  const metadata = useMemo(
    () => [
      ["意图", lastResult?.intent ? intentLabels[lastResult.intent] || lastResult.intent : "等待消息"],
      ["订单", lastResult?.order_id || "—"],
      ["售后申请", lastResult?.request_id || "—"],
      ["处理状态", lastResult?.handling_status || "—"],
      ["决策", lastResult?.decision ? decisionLabels[lastResult.decision] || lastResult.decision : "—"],
      ["业务动作", lastResult?.action || "—"],
    ],
    [lastResult],
  );

  async function sendMessage(rawMessage?: string) {
    const content = (rawMessage ?? input).trim();
    if (!content || isSending) return;

    // A new customer turn supersedes any in-flight status refresh. Without
    // aborting here, a closed request can report back after the new workflow
    // has already started and append a stale resolution message.
    requestPollingAbortRef.current?.abort();
    requestPollingAbortRef.current = null;
    if (
      lastResult?.request_id
      && lastResult.decision === "NoActionRequired"
    ) {
      setLastResult((current) => current ? { ...current, request_id: null } : current);
    }

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "customer",
      content,
      time: now(),
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setIsSending(true);

    try {
      const response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId,
          customer_id: activeCustomerId,
          order_id: activeOrder,
          message: content,
        }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || `请求失败 (${response.status})`);
      }

      const result = (await response.json()) as ChatResponse;
      const isNewRequest = result.request_id !== lastResult?.request_id;
      setLastResult(result);
      setConnectionState("online");
      const agentMessageId = `agent-${Date.now()}`;
      setMessages((current) => [
        ...current,
        {
          id: agentMessageId,
          role: "agent",
          content: result.message,
          time: now(),
        },
      ]);
      // Anchor a fresh evidence-upload card to the message that created the
      // service request, so it stays put in the conversation instead of
      // drifting to the bottom as later turns are appended.
      if (
        isNewRequest
        && result.action === "CreateServiceRequest"
        && result.request_id
        && (result.request_reason === "质量问题" || result.request_reason === "商品损坏")
      ) {
        const requestId = result.request_id;
        setEvidenceCards((current) => ({
          ...current,
          [agentMessageId]: { requestId, uploaded: null },
        }));
      }
    } catch (error) {
      setConnectionState("offline");
      setMessages((current) => [
        ...current,
        {
          id: `system-${Date.now()}`,
          role: "system",
          content: error instanceof Error ? error.message : "服务暂时不可用，请稍后重试。",
          time: now(),
        },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void sendMessage();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  // Each order (and "unassigned") keeps its own thread so switching between them
  // doesn't leak one order's conversation into another's.
  function currentThreadKey() {
    return activeOrderId ?? UNASSIGNED_THREAD_KEY;
  }

  function saveCurrentThread() {
    threadsRef.current.set(currentThreadKey(), {
      conversationId,
      messages,
      lastResult,
      evidenceCards,
    });
  }

  function loadThread(threadKey: string) {
    const thread = threadsRef.current.get(threadKey) ?? createThreadState();
    threadsRef.current.set(threadKey, thread);
    setConversationId(thread.conversationId);
    setMessages(thread.messages);
    setLastResult(thread.lastResult);
    setEvidenceCards(thread.evidenceCards);
  }

  function resetConversation() {
    const fresh = createThreadState();
    threadsRef.current.set(currentThreadKey(), fresh);
    setConversationId(fresh.conversationId);
    setMessages(fresh.messages);
    setLastResult(fresh.lastResult);
    setEvidenceCards(fresh.evidenceCards);
    setInput("");
  }

  function returnToCustomerSelection() {
    saveCurrentThread();
    savePersistedThreads(activeCustomerId, threadsRef.current, currentThreadKey());
    setHasSelectedCustomer(false);
    setOrders([]);
    setOrderDetail(null);
    setReturnOrder(null);
    setActiveWorkspace("chat");
    setInput("");
  }

  function preserveThreadBeforeStaffNavigation() {
    saveCurrentThread();
    savePersistedThreads(activeCustomerId, threadsRef.current, currentThreadKey());
  }

  function selectCustomer(customerId: string) {
    setHasSelectedCustomer(true);
    setActiveCustomerId(customerId);
    setOrders([]);
    setInput("");

    const persisted = loadPersistedThreads(customerId);
    if (persisted) {
      threadsRef.current = persisted.threads;
      const thread = persisted.threads.get(persisted.activeKey);
      if (thread) {
        setConversationId(thread.conversationId);
        setMessages(thread.messages);
        setLastResult(thread.lastResult);
        setEvidenceCards(thread.evidenceCards);
        setActiveOrderId(
          persisted.activeKey === UNASSIGNED_THREAD_KEY ? null : persisted.activeKey,
        );
        return;
      }
    }

    setActiveOrderId(null);
    threadsRef.current = new Map();
    loadThread(UNASSIGNED_THREAD_KEY);
  }

  function selectUnassigned() {
    saveCurrentThread();
    setActiveOrderId(null);
    loadThread(UNASSIGNED_THREAD_KEY);
    setInput("我的订单现在是什么状态？");
  }

  function selectOrder(order: CustomerOrder) {
    saveCurrentThread();
    setActiveOrderId(order.order_id);
    const existing = threadsRef.current.get(order.order_id);
    if (existing) {
      loadThread(order.order_id);
    } else {
      const fresh = createThreadState();
      threadsRef.current.set(order.order_id, fresh);
      setConversationId(fresh.conversationId);
      setMessages(fresh.messages);
      setLastResult(null);
      setEvidenceCards(fresh.evidenceCards);
    }
    setInput("");
  }

  async function submitEvidenceUpload(requestId: string, description: string, file: File): Promise<EvidenceRecord> {
    const form = new FormData();
    form.append("customer_id", activeCustomerId);
    form.append("description", description.trim());
    form.append("file", file);
    const response = await fetch(`${API_BASE}/service-requests/${requestId}/evidence`, {
      method: "POST",
      body: form,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.detail || `上传失败 (${response.status})`);
    return body as EvidenceRecord;
  }

  async function uploadEvidenceForCard(cardId: string, file: File) {
    const card = evidenceCards[cardId];
    if (!card || uploadingCardId) return;
    if (file.size > 10 * 1024 * 1024) {
      setMessages((current) => [...current, { id: `upload-${Date.now()}`, role: "system", content: "图片不能超过 10MB。", time: now() }]);
      return;
    }

    setUploadingCardId(cardId);
    try {
      const evidence = await submitEvidenceUpload(card.requestId, evidenceCardDescriptions[cardId] || "", file);
      setEvidenceCards((current) => ({
        ...current,
        [cardId]: { requestId: card.requestId, uploaded: evidence },
      }));
      setEvidenceCardDescriptions((current) => ({ ...current, [cardId]: "" }));
      setMessages((current) => [...current, {
        id: `evidence-${Date.now()}`,
        role: "system",
        content: `证据 ${evidence.evidence_id} 已安全提交，问题描述和图片已一并记录，当前状态为待审核。`,
        time: now(),
      }]);
    } catch (error) {
      setMessages((current) => [...current, {
        id: `upload-error-${Date.now()}`,
        role: "system",
        content: error instanceof Error ? error.message : "证据上传失败，请稍后重试。",
        time: now(),
      }]);
    } finally {
      setUploadingCardId(null);
    }
  }

  async function uploadRequestPageEvidence(requestId: string, file: File) {
    if (requestPageUploadingId) return;
    if (file.size > 10 * 1024 * 1024) {
      setRequestPageUploadErrors((current) => ({ ...current, [requestId]: "图片不能超过 10MB。" }));
      return;
    }
    const description = (requestPageDescriptions[requestId] || "").trim();
    setRequestPageUploadingId(requestId);
    setRequestPageUploadErrors((current) => {
      if (!(requestId in current)) return current;
      const next = { ...current };
      delete next[requestId];
      return next;
    });
    try {
      const evidence = await submitEvidenceUpload(requestId, description, file);
      setRequestPageUploadedEvidence((current) => ({ ...current, [requestId]: evidence }));
      setRequestPageDescriptions((current) => ({ ...current, [requestId]: "" }));
    } catch (error) {
      setRequestPageUploadErrors((current) => ({
        ...current,
        [requestId]: error instanceof Error ? error.message : "证据上传失败，请稍后重试。",
      }));
    } finally {
      setRequestPageUploadingId(null);
    }
  }

  async function submitReturnShipment() {
    const requestId = lastResult?.request_id;
    if (!requestId || !returnOrder) return;
    const carrier = window.prompt("请输入寄回承运商：", "顺丰速运")?.trim();
    if (!carrier) return;
    const trackingNumber = window.prompt("请输入寄回运单号：")?.trim();
    if (!trackingNumber) return;
    const response = await fetch(`${API_BASE}/service-requests/${requestId}/return-shipment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customer_id: activeCustomerId, carrier, tracking_number: trackingNumber }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      setMessages((current) => [...current, { id: `return-error-${Date.now()}`, role: "system", content: body?.detail || "寄回信息提交失败。", time: now() }]);
      return;
    }
    setReturnOrder(body as ReturnOrder);
    setMessages((current) => [...current, { id: `return-shipped-${Date.now()}`, role: "system", content: `退货运单 ${trackingNumber} 已提交，仓库收货后将进行验收。`, time: now() }]);
  }

  async function withdrawCustomerRequest(request: CustomerServiceRequestProgress) {
    if (withdrawingRequestId) return;
    if (!window.confirm(`确定撤销售后申请 ${request.request_id} 吗？撤销后本次申请将停止审核。`)) return;
    setWithdrawingRequestId(request.request_id);
    try {
      const response = await fetch(`${API_BASE}/service-requests/${request.request_id}/withdraw`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_id: activeCustomerId }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail || "撤销申请失败");
      setCustomerRequests((current) => current.map((item) => item.request_id === request.request_id
        ? { ...item, workflow_status: body.workflow_status, decision: body.decision, resolution_notes: body.resolution_notes, resolved_at: body.resolved_at }
        : item));
      setMessages((current) => [...current, { id: `withdraw-${request.request_id}-${Date.now()}`, role: "agent", content: `售后申请 ${request.request_id} 已由您主动撤销并关闭。`, time: now() }]);
      if (lastResult?.request_id === request.request_id) {
        setLastResult((current) => current ? {
          ...current,
          request_id: null,
          decision: "NoActionRequired",
          handling_status: "DecisionCompleted",
          action: "WithdrawServiceRequest",
        } : current);
        setReturnOrder(null);
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "撤销申请失败");
    } finally {
      setWithdrawingRequestId(null);
    }
  }

  if (!hasSelectedCustomer) {
    return (
      <div className="customer-entry">
        <div className="customer-entry__brand">
          <span className="brand-mark"><ShoppingBag size={18} /></span>
          <span><b>SHOPCARE</b><small>智能电商客服演示</small></span>
        </div>
        <section className="customer-entry__panel">
          <span className="eyebrow">SELECT CUSTOMER</span>
          <h1>请选择要进入的客户</h1>
          <p>选择后会加载该客户的订单、历史会话和售后申请。每次刷新都会回到这里。</p>
          <div className="customer-entry__grid">
            {customers.map((customer) => (
              <button key={customer.customer_id} onClick={() => selectCustomer(customer.customer_id)}>
                <span className="customer-entry__avatar"><UserRound size={18} /></span>
                <span>
                  <b>{customer.customer_id}</b>
                  <small>{customer.vip_level} 会员 · 历史退款 {customer.refund_history_count} 次</small>
                </span>
                <CircleDot size={13} />
              </button>
            ))}
          </div>
          {customers.length === 0 && (
            <div className="customer-entry__loading">
              <LoaderCircle size={16} className="spin" /> 正在加载客户列表…
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><ShoppingBag size={15} /></span>
          <span className="brand-name">SHOPCARE</span>
          <span className="brand-subtitle">AGENT CONSOLE</span>
        </div>

        <div className="global-search">
          <Search size={14} />
          <span>搜索订单、申请或会话…</span>
          <kbd>⌘ K</kbd>
        </div>

        <div className="topbar-actions">
          <button
            className="topbar-customer-return"
            onClick={returnToCustomerSelection}
            title="返回客户选择页"
          >
            <ArrowLeft size={13} /><span>选择客户</span>
          </button>
          <span className={`connection connection--${connectionState}`}>
            <i />
            {connectionState === "online" ? "服务在线" : connectionState === "checking" ? "连接中" : "连接异常"}
          </span>
          <a className="icon-button" aria-label="人工客服工作台" href={`/staff?returnCustomer=${encodeURIComponent(activeCustomerId)}`} onClick={preserveThreadBeforeStaffNavigation} title="人工客服工作台"><ShieldCheck size={16} /></a>
          <button className="icon-button" aria-label="设置"><Settings size={16} /></button>
          <div className="operator">
            <span className="operator-avatar">AI</span>
            <span><b>Decision Agent</b><small>多智能体客服</small></span>
          </div>
        </div>
      </header>

      <main className="workspace">
        <nav className="rail" aria-label="主导航">
          <button className={`rail-button ${activeWorkspace === "chat" ? "rail-button--active" : ""}`} aria-label="会话" onClick={() => setActiveWorkspace("chat")}><MessageSquare size={19} /><em>1</em></button>
          <button className="rail-button" aria-label="订单"><Package size={19} /></button>
          <button className={`rail-button ${activeWorkspace === "requests" ? "rail-button--active" : ""}`} aria-label="我的售后" title="我的售后" onClick={() => setActiveWorkspace("requests")}><FileText size={19} /></button>
          <button className="rail-button" aria-label="知识库"><HelpCircle size={19} /></button>
          <span className="rail-spacer" />
          <button className="rail-button" aria-label="系统状态"><Activity size={19} /></button>
        </nav>

        {activeWorkspace === "requests" ? (
          <section className="customer-requests-page">
            <header className="customer-requests-page__header">
              <button onClick={() => setActiveWorkspace("chat")}><ArrowLeft size={15} /> 返回客服会话</button>
              <div><span className="eyebrow">AFTER-SALES PROGRESS</span><h1>我的售后</h1><p>共 {customerRequests.length} 条申请，覆盖该客户的全部订单；可查看证据审核、人工结论和退款结果。</p></div>
              <span className="customer-requests-page__customer">{activeCustomerId}</span>
            </header>
            <div className="customer-requests-list">
              {isLoadingRequests && <div className="customer-requests-empty"><LoaderCircle className="spin" size={17} /> 正在加载申请进度…</div>}
              {!isLoadingRequests && customerRequests.length === 0 && <div className="customer-requests-empty">当前客户还没有售后申请。</div>}
              {customerRequests.map((request) => {
                const refund = request.actions.find((action) => action.action_type === "RefundPayment");
                const rejectedCount = request.evidence_reviews.filter((review) => review.verification_status === "Rejected").length;
                const isOpenRequest = !request.decision
                  && ["Pending", "NeedsReflection", "EscalatedToHuman", "InReview"].includes(request.workflow_status);
                // workflow_status alone can't tell "needs a new upload" apart
                // from "already uploaded, sitting in Pending review" — both
                // collapse into the same NeedsReflection value server-side.
                // Reconstruct the distinction from the evidence list itself
                // instead of inviting a redundant second upload.
                const hasEvidenceAwaitingReview = request.evidence.some(
                  (item) => item.verification_status === "Pending",
                );
                const canUploadMoreEvidence = isOpenRequest
                  && rejectedCount < 2
                  && !hasEvidenceAwaitingReview
                  && (request.request_reason === "质量问题" || request.request_reason === "商品损坏");
                const requestEvidence = requestPageUploadedEvidence[request.request_id];
                const requestDescription = requestPageDescriptions[request.request_id] || "";
                const requestUploadError = requestPageUploadErrors[request.request_id];
                const isUploadingThisRequest = requestPageUploadingId === request.request_id;
                return (
                  <article className="customer-request-card" key={request.request_id}>
                    <div className="customer-request-card__title"><div><b>{request.request_id}</b><span>订单 {request.order_id} · {request.request_reason} · {request.requested_quantity} 件</span></div><em>{requestWorkflowStatusLabels[request.workflow_status] || request.workflow_status}</em></div>
                    <div className="request-timeline">
                      <div><i /><span><b>申请已提交</b><small>{request.request_date}</small></span></div>
                      {request.evidence_reviews.map((review, index) => (
                        <div key={`${review.evidence_id}-${review.verification_status}`} className={review.verification_status === "Rejected" ? "is-rejected" : "is-complete"}><i /><span><b>第 {index + 1} 轮证据{review.verification_status === "Rejected" ? "未通过" : "已通过"}</b><small>{review.review_notes || "审核完成"}{review.reviewed_at ? ` · ${new Date(review.reviewed_at).toLocaleString("zh-CN")}` : ""}</small></span></div>
                      ))}
                      {request.decision && <div className={request.decision === "Reject" ? "is-rejected" : "is-complete"}><i /><span><b>{request.decision === "Reject" ? "申请已关闭" : "申请已批准"}</b><small>{request.resolution_notes || "审核已经完成"}</small></span></div>}
                      {!request.decision && rejectedCount >= 2 && <div><i /><span><b>最终人工复核</b><small>已达到两轮补证上限，等待人工给出最终结论。</small></span></div>}
                    </div>
                    {request.evidence.length > 0 && (
                      <div className="evidence-gallery">
                        {request.evidence.map((item) => (
                          <a
                            key={item.evidence_id}
                            className={`evidence-gallery__item evidence-gallery__item--${item.verification_status.toLowerCase()}`}
                            href={item.file_url ? `${API_BASE}${item.file_url}` : undefined}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {item.file_url ? (
                              <img src={`${API_BASE}${item.file_url}`} alt={item.original_filename} />
                            ) : (
                              <span className="evidence-gallery__placeholder"><ImageUp size={16} /></span>
                            )}
                            <div>
                              <b>{item.evidence_id}</b>
                              <em>{evidenceStatusLabels[item.verification_status] || item.verification_status}</em>
                              {item.customer_description && <p>{item.customer_description}</p>}
                              <small>{item.submitted_date}</small>
                            </div>
                          </a>
                        ))}
                      </div>
                    )}
                    {canUploadMoreEvidence && (
                      <div className="evidence-upload-card evidence-upload-card--inline">
                        <span className="evidence-upload-card__icon"><ImageUp size={19} /></span>
                        <div>
                          <b>{requestEvidence ? "证据已提交" : rejectedCount > 0 ? "重新提交证据" : "补充问题证据"}</b>
                          <p>{requestEvidence ? `${requestEvidence.evidence_id} · 等待审核` : "上传清晰的商品图片，支持 JPG、PNG、WebP，最大 10MB。"}</p>
                          {!requestEvidence && (
                            <>
                              <textarea
                                className="evidence-description"
                                value={requestDescription}
                                onChange={(event) => setRequestPageDescriptions((current) => ({ ...current, [request.request_id]: event.target.value }))}
                                placeholder="请描述图片中的问题，例如：开机后屏幕右侧持续闪烁，重启后仍存在（至少5个字）"
                                maxLength={1000}
                                rows={3}
                              />
                              <span className="evidence-description__counter">
                                {requestDescription.trim().length}/5
                              </span>
                            </>
                          )}
                          {requestUploadError && <small className="evidence-upload-card__error">{requestUploadError}</small>}
                          <small>上传只代表材料已收到，不代表问题已确认或申请已批准。</small>
                        </div>
                        {!requestEvidence && (
                          <label className={isUploadingThisRequest || requestDescription.trim().length < 5 ? "disabled" : ""}>
                            {isUploadingThisRequest ? <LoaderCircle size={14} className="spin" /> : <ImageUp size={14} />}
                            {isUploadingThisRequest ? "上传中" : "选择图片"}
                            <input
                              type="file"
                              accept="image/jpeg,image/png,image/webp"
                              disabled={isUploadingThisRequest || requestDescription.trim().length < 5}
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) void uploadRequestPageEvidence(request.request_id, file);
                                event.target.value = "";
                              }}
                            />
                          </label>
                        )}
                      </div>
                    )}
                    {refund?.workflow_status === "Succeeded" && <div className="refund-receipt"><CheckCircle2 size={18} /><div><b>退款成功凭证</b><span>退款金额 ¥{request.approved_amount?.toFixed(2) || "0.00"}</span><code>退款流水号 {refund.external_transaction_id}</code><small>完成时间 {refund.updated_at ? new Date(refund.updated_at).toLocaleString("zh-CN") : "—"}</small></div></div>}
                    {refund && refund.workflow_status !== "Succeeded" && <div className="refund-pending"><LoaderCircle size={14} /> 退款任务状态：{refund.workflow_status}</div>}
                    {isOpenRequest && (
                      <div className="request-card-actions"><button disabled={withdrawingRequestId === request.request_id} onClick={() => void withdrawCustomerRequest(request)}>{withdrawingRequestId === request.request_id ? "正在撤销…" : "撤销申请"}</button></div>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        ) : (<>

        <aside className="scenario-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">MOCK CUSTOMER</span><h2>客户与订单</h2></div>
            <div className="panel-heading__actions">
              <button className="mini-button" onClick={resetConversation} title="新建会话"><Plus size={14} /></button>
            </div>
          </div>
          <div className="customer-picker">
            <UserRound size={14} />
            <select
              aria-label="切换模拟客户"
              value={activeCustomerId}
              onChange={(event) => selectCustomer(event.target.value)}
            >
              {customers.map((customer) => (
                <option key={customer.customer_id} value={customer.customer_id}>
                  {customer.customer_id} · {customer.vip_level}
                </option>
              ))}
            </select>
          </div>
          <div className="customer-summary">
            <span>{activeCustomer?.vip_level || "—"} 会员</span>
            <span>历史退款 {activeCustomer?.refund_history_count ?? "—"} 次</span>
          </div>
          <div className="scenario-filter"><Search size={13} /><span>该客户的订单</span></div>
          <div className="scenario-list">
            <button
              className={`scenario-card scenario-card--smart ${activeOrderId === null ? "scenario-card--active" : ""}`}
              onClick={selectUnassigned}
            >
              <span className="scenario-icon scenario-icon--purple"><Sparkles size={15} /></span>
              <span className="scenario-copy"><b>不指定订单</b><small>让 Agent 自动关联或请您选择</small><code>{activeCustomerId}</code></span>
              <CircleDot size={11} className="scenario-dot" />
            </button>
            {orders.map((order) => (
              <button
                key={order.order_id}
                className={`scenario-card ${activeOrderId === order.order_id ? "scenario-card--active" : ""}`}
                onClick={() => selectOrder(order)}
              >
                <span className={`scenario-icon scenario-icon--${order.order_status === "PendingShipment" ? "orange" : order.order_status === "Shipped" ? "blue" : "green"}`}>
                  {order.order_status === "Shipped" ? <Truck size={15} /> : <Package size={15} />}
                </span>
                <span className="scenario-copy">
                  <b>{order.product_category || order.product_id} · ¥{order.amount.toFixed(2)}</b>
                  <small>{order.order_status === "PendingShipment" ? "待发货" : order.order_status === "Shipped" ? "运输中" : "已签收"} · {order.payment_status}</small>
                  <code>{order.order_id}</code>
                </span>
                <CircleDot size={11} className="scenario-dot" />
              </button>
            ))}
            {!orders.length && <div className="empty-orders">该客户暂无订单</div>}
          </div>
          <div className="session-card">
            <span className="eyebrow">THREAD ID</span>
            <code>{conversationId}</code>
            <button onClick={resetConversation}><RotateCcw size={12} /> 新建独立会话</button>
          </div>
        </aside>

        <section className="chat-panel">
          <div className="chat-header">
            <div className="agent-avatar"><Bot size={19} /></div>
            <div><h1>智能售后助手</h1><p><code>{activeOrder || "等待订单号"}</code><span>·</span> Evidence-first workflow</p></div>
            <div className="chat-header-status"><ShieldCheck size={14} /><span>安全决策模式</span></div>
          </div>

          <div className="messages" aria-live="polite" ref={messagesContainerRef}>
            {orderDetail && (
              <section className="order-overview" aria-label="订单与物流详情">
                <div className="order-overview__header">
                  <div>
                    <span className="eyebrow">MY ORDER</span>
                    <h2>{orderDetail.product_category || "商品订单"}<code>{orderDetail.order_id}</code></h2>
                  </div>
                  <span className={`order-status order-status--${orderDetail.order_status.toLowerCase()}`}>
                    {orderDetail.order_status === "PendingShipment" ? "待发货" : orderDetail.order_status === "Shipped" ? "运输中" : "已签收"}
                  </span>
                </div>

                <div className="order-progress">
                  <div className="complete"><i><CheckCircle2 size={12} /></i><span>已下单<small>{orderDetail.order_date}</small></span></div>
                  <b className={orderDetail.shipped_date ? "complete" : ""} />
                  <div className={orderDetail.shipped_date ? "complete" : "pending"}><i>{orderDetail.shipped_date ? <CheckCircle2 size={12} /> : <Package size={12} />}</i><span>{orderDetail.shipped_date ? "已发货" : "待发货"}<small>{orderDetail.shipped_date || orderDetail.promised_ship_date || "时间待确认"}</small></span></div>
                  <b className={orderDetail.delivered_date ? "complete" : ""} />
                  <div className={orderDetail.delivered_date ? "complete" : "pending"}><i>{orderDetail.delivered_date ? <CheckCircle2 size={12} /> : <Truck size={12} />}</i><span>{orderDetail.delivered_date ? "已签收" : "预计送达"}<small>{orderDetail.delivered_date || orderDetail.estimated_delivery_date || "时间待确认"}</small></span></div>
                </div>

                <div className="order-facts">
                  <span><small>订单金额</small><b>¥{orderDetail.amount.toFixed(2)}</b></span>
                  <span><small>商品数量</small><b>{orderDetail.quantity} 件</b></span>
                  <span><small>支付状态</small><b>{orderDetail.payment_status === "Paid" ? "已支付" : orderDetail.payment_status}</b></span>
                  {orderDetail.refunded_amount > 0 && (
                    <span><small>退款状态</small><b className="order-facts__refunded">已退款 ¥{orderDetail.refunded_amount.toFixed(2)}</b></span>
                  )}
                </div>

                <div className="order-items-panel">
                  <span className="eyebrow">ORDER ITEMS</span>
                  {orderDetail.order_items.map((item) => (
                    <div className="order-item-row" key={item.order_item_id}>
                      <div>
                        <b>{item.sku_name}</b>
                        <small>{item.product_id} · {item.sku_id}</small>
                      </div>
                      <span>¥{item.unit_price.toFixed(2)} × {item.quantity}</span>
                      <span>实付 ¥{item.paid_amount.toFixed(2)}</span>
                      <span>{item.shipped_quantity === null ? "未出库" : `已出库 ${item.shipped_quantity} 件`}</span>
                    </div>
                  ))}
                </div>

                <div className={`logistics-notice ${orderDetail.logistics.length ? "logistics-notice--event" : ""}`}>
                  <Truck size={15} />
                  {orderDetail.logistics.length ? (
                    <div><b>{orderDetail.logistics[0].carrier} · {orderDetail.logistics[0].exception_type}</b><small>{orderDetail.logistics[0].exception_recorded_date} · {orderDetail.logistics[0].resolved ? "已处理" : "处理中"}</small></div>
                  ) : (
                    <div><b>{orderDetail.shipped_date ? "暂未记录物流异常" : "商品尚未发货"}</b><small>{orderDetail.shipped_date ? "如状态长时间未更新，可咨询智能客服" : `承诺发货：${orderDetail.promised_ship_date || "待确认"}`}</small></div>
                  )}
                  <button onClick={() => setInput(`请帮我查一下订单${orderDetail.order_id}`)}>咨询客服</button>
                </div>
              </section>
            )}
            <div className="day-divider"><span>当前会话</span></div>
            {messages.map((message) => (
              <Fragment key={message.id}>
                <div className={`message-row message-row--${message.role}`}>
                  <span className="message-avatar">
                    {message.role === "customer" ? <UserRound size={14} /> : message.role === "system" ? <Zap size={14} /> : <Bot size={14} />}
                  </span>
                  <div className="message-stack">
                    <div className="message-bubble">{message.content}</div>
                    <time>{message.time}</time>
                  </div>
                </div>
                {evidenceCards[message.id] && (() => {
                  const card = evidenceCards[message.id];
                  const cardDescription = evidenceCardDescriptions[message.id] || "";
                  const isUploadingThisCard = uploadingCardId === message.id;
                  return (
                    <div className="evidence-upload-card">
                      <span className="evidence-upload-card__icon"><ImageUp size={19} /></span>
                      <div>
                        <b>{card.uploaded ? "证据已提交" : "补充问题证据"}</b>
                        <p>{card.uploaded ? `${card.uploaded.evidence_id} · 等待审核` : "上传清晰的商品图片，支持 JPG、PNG、WebP，最大 10MB。"}</p>
                        {!card.uploaded && (
                          <>
                            <textarea
                              className="evidence-description"
                              value={cardDescription}
                              onChange={(event) => setEvidenceCardDescriptions((current) => ({ ...current, [message.id]: event.target.value }))}
                              placeholder="请描述图片中的问题，例如：开机后屏幕右侧持续闪烁，重启后仍存在（至少5个字）"
                              maxLength={1000}
                              rows={3}
                            />
                            <span className="evidence-description__counter">
                              {cardDescription.trim().length}/5
                            </span>
                          </>
                        )}
                        <small>上传只代表材料已收到，不代表问题已确认或申请已批准。</small>
                      </div>
                      {!card.uploaded && (
                        <label className={isUploadingThisCard || cardDescription.trim().length < 5 ? "disabled" : ""}>
                          {isUploadingThisCard ? <LoaderCircle size={14} className="spin" /> : <ImageUp size={14} />}
                          {isUploadingThisCard ? "上传中" : "选择图片"}
                          <input
                            type="file"
                            accept="image/jpeg,image/png,image/webp"
                            disabled={isUploadingThisCard || cardDescription.trim().length < 5}
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              if (file) void uploadEvidenceForCard(message.id, file);
                              event.target.value = "";
                            }}
                          />
                        </label>
                      )}
                    </div>
                  );
                })()}
              </Fragment>
            ))}
            {lastResult?.request_id && (
              <div className="request-progress-card">
                <div><FileText size={17} /><span><b>售后申请 {lastResult.request_id}</b><small>申请进度、证据审核和退款结果会持续更新。</small></span></div>
                <button onClick={() => setActiveWorkspace("requests")}>查看申请进度 <ArrowLeft size={12} /></button>
              </div>
            )}
            {returnOrder && (
              <div className="evidence-upload-card">
                <span className="evidence-upload-card__icon"><Package size={19} /></span>
                <div>
                  <b>退货单 {returnOrder.return_order_id}</b>
                  <p>当前状态：{returnOrder.workflow_status}</p>
                  <small>商品验收入库通过后才会发起原路退款。</small>
                </div>
                {returnOrder.workflow_status === "AwaitingShipment" && (
                  <button className="staff-btn staff-btn--approve" onClick={() => void submitReturnShipment()}>填写寄回物流</button>
                )}
              </div>
            )}
            {isSending && (
              <div className="message-row message-row--agent">
                <span className="message-avatar"><Bot size={14} /></span>
                <div className="typing"><i /><i /><i /><span>Agent 正在编排工作流</span></div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="composer-area">
            <div className="quick-prompts">
              {quickPrompts.map((prompt) => <button key={prompt} onClick={() => void sendMessage(prompt)}>{prompt}</button>)}
            </div>
            <form className="composer" onSubmit={handleSubmit}>
              <Sparkles size={16} className="composer-sparkle" />
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入订单问题，Enter 发送，Shift + Enter 换行…"
                rows={2}
                disabled={isSending}
              />
              <button type="submit" disabled={!input.trim() || isSending} aria-label="发送">
                {isSending ? <LoaderCircle size={17} className="spin" /> : <Send size={17} />}
              </button>
            </form>
            <p className="composer-hint"><ShieldCheck size={11} /> Agent 不会在业务记录创建前声称动作已执行</p>
          </div>
        </section>

        <aside className="context-panel">
          <div className="context-section context-hero">
            <span className="eyebrow">LIVE TRACE</span>
            <div className="context-title"><span className="context-agent-icon"><Bot size={18} /></span><div><h3>Agent 运行状态</h3><p>LangGraph · Multi-agent</p></div></div>
            <div className="pipeline">
              {["Conversation Router", "Evidence Agents", "Policy & Gate", "Decision / Action"].map((step, index) => (
                <div className="pipeline-step" key={step}>
                  <span className={lastResult && index <= 3 ? "done" : index === 0 ? "active" : ""}>
                    {lastResult && index <= 3 ? <CheckCircle2 size={11} /> : index + 1}
                  </span>
                  <b>{step}</b>
                </div>
              ))}
            </div>
          </div>

          <div className="context-section">
            <span className="eyebrow">STRUCTURED RESULT</span>
            <div className="metadata-list">
              {metadata.map(([label, value]) => (
                <div className="metadata-row" key={label}><span>{label}</span><code className={value !== "—" && value !== "等待消息" ? "has-value" : ""}>{value}</code></div>
              ))}
            </div>
          </div>

          <div className="context-section">
            <span className="eyebrow">CAPABILITIES</span>
            <div className="capability-grid">
              <div><Package size={14} /><span>订单事实</span></div>
              <div><Truck size={14} /><span>物流核查</span></div>
              <div><ShieldCheck size={14} /><span>证据门控</span></div>
              <div><Box size={14} /><span>履约动作</span></div>
            </div>
          </div>

          {lastResult?.action_id && (
            <div className="action-receipt"><CheckCircle2 size={18} /><div><span>业务动作已记录</span><code>{lastResult.action_id}</code></div></div>
          )}
        </aside>
        </>)}
      </main>
    </div>
  );
}

export default App;
