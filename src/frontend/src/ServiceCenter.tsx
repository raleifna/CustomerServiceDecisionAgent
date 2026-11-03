import {
  Bot,
  Box,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  FileText,
  Headset,
  ImageUp,
  LoaderCircle,
  Package,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Truck,
  UserRound,
  Zap,
} from "lucide-react";
import { Fragment, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  API_BASE,
  apiFetch,
  CustomerOrder,
  CustomerServiceRequestProgress,
  EvidenceRecord,
  evidenceStatusLabels,
  OrderDetail,
  orderStatusLabels,
  ProductDetail,
  requestWorkflowStatusLabels,
  ReturnOrder,
  vipLabels,
  yuan,
} from "./shop/api";
import { Breadcrumbs, Link, UserCenterLayout } from "./shop/components";
import { useFetch, useMyProfile } from "./shop/hooks";
import { ProductImage } from "./shop/ProductImage";
import { ReturnShipmentCard } from "./shop/ReturnShipment";
import { navigate } from "./shop/router";

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

type ServiceRequestStatus = {
  request_id: string;
  workflow_status: string;
  decision: string | null;
  approved_amount: number | null;
  resolution_notes: string | null;
  resolved_at: string | null;
  return_order: ReturnOrder | null;
  evidence_reviews: CustomerServiceRequestProgress["evidence_reviews"];
  actions: CustomerServiceRequestProgress["actions"];
};

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
  NoActionRequired: "无需处理",
};

const handlingStatusLabels: Record<string, string> = {
  DecisionCompleted: "已完成",
  AwaitingEvidenceReview: "等待证据审核",
  AwaitingExternalVerification: "等待外部核实",
  NeedsMoreEvidence: "需要补充证据",
  NeedsReflection: "复核中",
  HumanEscalation: "已转人工",
  SystemError: "系统繁忙",
};

const actionLabels: Record<string, string> = {
  CreateServiceRequest: "已创建售后申请",
  CreateHumanHandoff: "已转接人工客服",
  ExpediteShipment: "已催促发货",
  LogisticsChase: "已发起物流催查",
  WithdrawServiceRequest: "已撤销售后申请",
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

type ServiceCenterProps = {
  customerId: string;
  workspace: "chat" | "requests";
  // Deep links from the shop: /service?order=…&product=…&ask=…
  routeOrderId: string | null;
  routeProductId: string | null;
  routePrefill: string | null;
  // /after-sales?focus=<request_id>: scroll to and highlight that request.
  routeFocus?: string | null;
};

function ServiceCenter({ customerId, workspace, routeOrderId, routeProductId, routePrefill, routeFocus }: ServiceCenterProps) {
  const activeCustomerId = customerId;
  const activeWorkspace = workspace;
  const activeCustomer = useMyProfile(customerId);
  const productContext = useFetch<ProductDetail>(routeProductId ? `/products/${encodeURIComponent(routeProductId)}` : null);
  const [conversationId, setConversationId] = useState(() => createConversationId());
  const [messages, setMessages] = useState<Message[]>(() => initialMessages());
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [lastResult, setLastResult] = useState<ChatResponse | null>(null);
  const [orders, setOrders] = useState<CustomerOrder[]>([]);
  const [hasSelectedCustomer, setHasSelectedCustomer] = useState(false);
  const [activeOrderId, setActiveOrderId] = useState<string | null>(null);
  const [orderDetail, setOrderDetail] = useState<OrderDetail | null>(null);
  const [evidenceCards, setEvidenceCards] = useState<Record<string, EvidenceCardState>>({});
  const [evidenceCardDescriptions, setEvidenceCardDescriptions] = useState<Record<string, string>>({});
  const [uploadingCardId, setUploadingCardId] = useState<string | null>(null);
  const [returnOrder, setReturnOrder] = useState<ReturnOrder | null>(null);
  const [customerRequests, setCustomerRequests] = useState<CustomerServiceRequestProgress[]>([]);
  const [isLoadingRequests, setIsLoadingRequests] = useState(false);
  const [withdrawingRequestId, setWithdrawingRequestId] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<"checking" | "online" | "offline">("checking");
  const [requestPageUploadingId, setRequestPageUploadingId] = useState<string | null>(null);
  const [requestPageDescriptions, setRequestPageDescriptions] = useState<Record<string, string>>({});
  const [requestPageUploadedEvidence, setRequestPageUploadedEvidence] = useState<Record<string, EvidenceRecord>>({});
  const [requestPageUploadErrors, setRequestPageUploadErrors] = useState<Record<string, string>>({});
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const threadsRef = useRef<Map<string, ThreadState>>(new Map());
  const requestPollingAbortRef = useRef<AbortController | null>(null);
  const routeKeyRef = useRef<string | null>(null);
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
    apiFetch(`/health`, { signal: controller.signal })
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
        const response = await apiFetch(
          `/service-requests/${requestId}/status`,
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

  // Enter the signed-in customer's service center once on mount, then
  // follow later deep links (e.g. "联系客服" on another order) in place.
  const routeKey = `${routeOrderId ?? ""}|${routePrefill ?? ""}`;
  useEffect(() => {
    if (routeKeyRef.current === null) {
      routeKeyRef.current = routeKey;
      selectCustomer(customerId, routeOrderId ?? undefined);
      if (routePrefill) setInput(routePrefill);
      return;
    }
    if (routeKeyRef.current === routeKey) return;
    routeKeyRef.current = routeKey;
    if (routeOrderId && routeOrderId !== activeOrderId) selectOrder(routeOrderId);
    if (routePrefill) setInput(routePrefill);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey]);

  useEffect(() => {
    if (!hasSelectedCustomer) return;
    const controller = new AbortController();
    apiFetch("/me/orders", { signal: controller.signal })
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
    apiFetch("/me/service-requests", { signal: controller.signal })
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
    apiFetch(`/me/orders/${activeOrderId}`, {
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
    if (activeWorkspace !== "requests" || !routeFocus || isLoadingRequests) return;
    document.getElementById(`request-${routeFocus}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [activeWorkspace, routeFocus, isLoadingRequests, customerRequests.length]);

  useEffect(() => {
    // Scroll only the message list: scrollIntoView would also scroll the
    // whole shop page now that the service center sits inside it.
    const container = messagesContainerRef.current;
    container?.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  // Runs after the bottom-scroll effect above, so switching context (which also
  // swaps in a different thread's messages) ends up scrolled to top instead of
  // bottom, keeping the newly shown order card visible instead of hidden above the fold.
  useEffect(() => {
    messagesContainerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [activeOrderId]);

  const activeOrder = lastResult?.order_id || activeOrderId;
  const metadata = useMemo(
    () => [
      ["问题类型", lastResult?.intent ? intentLabels[lastResult.intent] || lastResult.intent : "等待提问"],
      ["关联订单", lastResult?.order_id || "—"],
      ["售后申请", lastResult?.request_id || "—"],
      ["处理状态", lastResult?.handling_status ? handlingStatusLabels[lastResult.handling_status] || "处理中" : "—"],
      ["处理结果", lastResult?.decision ? decisionLabels[lastResult.decision] || lastResult.decision : "—"],
      ["执行动作", lastResult?.action ? actionLabels[lastResult.action] || lastResult.action : "—"],
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
      const response = await apiFetch(`/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId,
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

  // `orderId` undefined -> resume whichever thread was active last time;
  // a string/null -> open that order's thread (or the unassigned one).
  function selectCustomer(customerId: string, orderId?: string | null) {
    setHasSelectedCustomer(true);
    setOrders([]);
    setInput("");

    const persisted = loadPersistedThreads(customerId);
    threadsRef.current = persisted?.threads ?? new Map();
    const key = orderId !== undefined && orderId !== null
      ? orderId
      : persisted?.activeKey ?? UNASSIGNED_THREAD_KEY;
    setActiveOrderId(key === UNASSIGNED_THREAD_KEY ? null : key);
    loadThread(key);
  }

  function selectUnassigned() {
    saveCurrentThread();
    setActiveOrderId(null);
    loadThread(UNASSIGNED_THREAD_KEY);
    setInput("我的订单现在是什么状态？");
  }

  function selectOrder(orderId: string) {
    saveCurrentThread();
    setActiveOrderId(orderId);
    loadThread(orderId);
    setInput("");
  }

  async function submitEvidenceUpload(requestId: string, description: string, file: File): Promise<EvidenceRecord> {
    const form = new FormData();
    form.append("description", description.trim());
    form.append("file", file);
    const response = await apiFetch(`/service-requests/${requestId}/evidence`, {
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

  function handleChatReturnSubmitted(next: ReturnOrder) {
    setReturnOrder(next);
    setMessages((current) => [...current, {
      id: `return-shipped-${Date.now()}`,
      role: "system",
      content: `退货运单 ${next.return_carrier} ${next.return_tracking_number} 已提交，仓库签收并验收通过后将原路退款。`,
      time: now(),
    }]);
  }

  function handleRequestPageReturnSubmitted(requestId: string, next: ReturnOrder) {
    setCustomerRequests((current) => current.map((item) => item.request_id === requestId
      ? { ...item, workflow_status: "ReturnInTransit", return_order: next }
      : item));
  }

  async function withdrawCustomerRequest(request: CustomerServiceRequestProgress) {
    if (withdrawingRequestId) return;
    if (!window.confirm(`确定撤销售后申请 ${request.request_id} 吗？撤销后本次申请将停止审核。`)) return;
    setWithdrawingRequestId(request.request_id);
    try {
      const response = await apiFetch(`/service-requests/${request.request_id}/withdraw`, { method: "POST" });
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

  const orderById = new Map(orders.map((order) => [order.order_id, order]));
  const productPurchases = routeProductId ? orders.filter((order) => order.product_id === routeProductId) : [];

  if (activeWorkspace === "requests") {
    return (
      <UserCenterLayout customer={activeCustomer}>
        <section className="panel customer-requests-page">
          <header className="customer-requests-page__header">
            <div><h1>退款/售后</h1><p>共 {customerRequests.length} 条申请，覆盖您的全部订单；可查看证据审核、人工结论和退款结果。</p></div>
            <Link to="/service" className="btn btn--outline btn--sm"><Headset size={14} />联系客服</Link>
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
                  <article
                    className={`customer-request-card ${routeFocus === request.request_id ? "customer-request-card--focus" : ""}`}
                    key={request.request_id}
                    id={`request-${request.request_id}`}
                  >
                    <div className="customer-request-card__title">
                      <ProductImage product={orderById.get(request.order_id)?.product} size="sm" />
                      <div>
                        <b>{orderById.get(request.order_id)?.product?.title ?? `订单 ${request.order_id}`}</b>
                        <span>售后单 {request.request_id} · <Link to={`/orders/${request.order_id}`}>订单 {request.order_id}</Link> · {request.request_reason} · {request.requested_quantity} 件</span>
                      </div>
                      <em className={request.decision === "Reject" || request.workflow_status === "Closed" ? "is-closed" : request.decision ? "is-done" : ""}>{requestWorkflowStatusLabels[request.workflow_status] || request.workflow_status}</em>
                    </div>
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
                    {request.return_order && (
                      <ReturnShipmentCard
                        returnOrder={request.return_order}
                        refundSucceeded={refund?.workflow_status === "Succeeded"}
                        onSubmitted={(next) => handleRequestPageReturnSubmitted(request.request_id, next)}
                      />
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
      </UserCenterLayout>
    );
  }

  const currentOrder = activeOrderId ? orderById.get(activeOrderId) : undefined;

  return (
    <div className="page-container service-page">
      <div className="service-page__head">
        <Breadcrumbs items={[{ label: "首页", to: "/" }, { label: "客服中心" }]} />
        <span className="service-page__hours"><Headset size={14} />智能客服 7×24 小时在线 · 复杂问题自动转人工</span>
      </div>
    <div className="service-center">
        <aside className="scenario-panel">
          <div className="panel-heading">
            <div><h2>选择咨询的订单</h2><small>{activeCustomerId} · {vipLabels[activeCustomer?.vip_level ?? ""] ?? activeCustomer?.vip_level ?? "—"} · 历史退款 {activeCustomer?.refund_history_count ?? "—"} 次</small></div>
            <button className="mini-button" onClick={resetConversation} title="新建会话"><Plus size={14} /></button>
          </div>
          <div className="scenario-list">
            <button
              className={`scenario-card scenario-card--smart ${activeOrderId === null ? "scenario-card--active" : ""}`}
              onClick={selectUnassigned}
            >
              <span className="scenario-icon"><Sparkles size={18} /></span>
              <span className="scenario-copy"><b>不指定订单</b><small>让客服自动关联或请您选择</small></span>
              <CircleDot size={11} className="scenario-dot" />
            </button>
            {orders.map((order) => (
              <button
                key={order.order_id}
                className={`scenario-card ${activeOrderId === order.order_id ? "scenario-card--active" : ""}`}
                onClick={() => selectOrder(order.order_id)}
              >
                <ProductImage product={order.product} size="xs" />
                <span className="scenario-copy">
                  <b>{order.product?.title ?? order.product_category ?? order.product_id}</b>
                  <small><em className={`status-text status-text--${order.order_status.toLowerCase()}`}>{orderStatusLabels[order.order_status]}</em> · {yuan(order.amount)}</small>
                  <code>{order.order_id}</code>
                </span>
                <CircleDot size={11} className="scenario-dot" />
              </button>
            ))}
            {!orders.length && <div className="empty-orders">暂无订单</div>}
          </div>
          <div className="session-card">
            <span className="eyebrow">当前会话</span>
            <code>{conversationId}</code>
            <button onClick={resetConversation}><RotateCcw size={12} /> 新建独立会话</button>
          </div>
        </aside>

        <section className="chat-panel">
          <div className="chat-header">
            <div className="agent-avatar"><Bot size={19} /></div>
            <div>
              <h1>ShopCare 智能售后助手</h1>
              <p>
                <span className={`connection connection--${connectionState}`}><i />{connectionState === "online" ? "在线" : connectionState === "checking" ? "连接中" : "连接异常"}</span>
                <span>·</span>
                {activeOrder ? <>正在咨询订单 <code>{activeOrder}</code></> : "未指定订单"}
              </p>
            </div>
            <div className="chat-header-status"><ShieldCheck size={14} /><span>证据优先 · 安全决策</span></div>
          </div>

          <div className="messages" aria-live="polite" ref={messagesContainerRef}>
            {orderDetail && (
              <section className="order-overview" aria-label="订单与物流详情">
                <div className="order-overview__header">
                  <ProductImage product={orderDetail.product} size="sm" />
                  <div>
                    <span className="eyebrow">正在咨询的订单</span>
                    <h2>{orderDetail.product?.title || orderDetail.product_category || "商品订单"}</h2>
                    <p><code>{orderDetail.order_id}</code><Link to={`/orders/${orderDetail.order_id}`}>订单详情 <ChevronRight size={11} /></Link></p>
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
                  <span className="eyebrow">订单商品</span>
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
                  <button onClick={() => setInput(orderDetail.shipped_date ? "我的快递到哪了？" : "这个订单什么时候发货？")}>{orderDetail.shipped_date ? "问物流" : "问发货"}</button>
                </div>
              </section>
            )}
            {routeProductId && productContext.data && activeOrderId === null && (
              <section className="product-context">
                <ProductImage product={productContext.data} size="sm" />
                <div>
                  <span className="eyebrow">正在咨询的商品</span>
                  <Link to={`/product/${productContext.data.product_id}`}><b>{productContext.data.title}</b></Link>
                  <small>{yuan(productContext.data.price)} · {productContext.data.service_promises.map((promise) => promise.title).join(" · ")}</small>
                  {productPurchases.length > 0 ? (
                    <div className="product-context__orders">
                      <span>您购买过此商品，选择订单可直接办理售后：</span>
                      {productPurchases.map((order) => (
                        <button key={order.order_id} onClick={() => selectOrder(order.order_id)}>{order.order_id} · {orderStatusLabels[order.order_status]}</button>
                      ))}
                    </div>
                  ) : (
                    <span className="product-context__hint">售前问题可直接提问；智能客服主要处理订单、物流与售后，复杂问题会为您转接人工。</span>
                  )}
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
                <button onClick={() => navigate("/after-sales")}>查看申请进度 <ChevronRight size={12} /></button>
              </div>
            )}
            {returnOrder && (
              <div className="chat-return-card">
                <ReturnShipmentCard returnOrder={returnOrder} compact onSubmitted={handleChatReturnSubmitted} />
              </div>
            )}
            {isSending && (
              <div className="message-row message-row--agent">
                <span className="message-avatar"><Bot size={14} /></span>
                <div className="typing"><i /><i /><i /><span>Agent 正在编排工作流</span></div>
              </div>
            )}
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
          {activeCustomer?.test_scenario && (
            <section className="context-section context-guide">
              <h3>测试指引</h3>
              <b>{activeCustomer.test_scenario}</b>
              <ul>{(activeCustomer.test_hints ?? []).map((hint) => <li key={hint}>{hint}</li>)}</ul>
            </section>
          )}
          <section className="context-section">
            <h3>当前咨询</h3>
            {currentOrder ? (
              <div className="context-order">
                <ProductImage product={currentOrder.product} size="sm" />
                <div>
                  <b>{currentOrder.product?.title ?? currentOrder.order_id}</b>
                  <small><em className={`status-text status-text--${currentOrder.order_status.toLowerCase()}`}>{orderStatusLabels[currentOrder.order_status]}</em> · {yuan(currentOrder.amount)}</small>
                  <span className="context-order__links">
                    <Link to={`/orders/${currentOrder.order_id}`}>订单详情</Link>
                    {currentOrder.order_status !== "PendingShipment" && <Link to={`/orders/${currentOrder.order_id}#tracking`}>查看物流</Link>}
                    <Link to={`/product/${currentOrder.product_id}`}>商品页</Link>
                  </span>
                </div>
              </div>
            ) : (
              <p className="context-empty">未指定订单。您可以在左侧选择订单，或直接描述问题，客服会帮您关联。</p>
            )}
          </section>

          <section className="context-section">
            <h3>本次处理进度</h3>
            <div className="metadata-list">
              {metadata.map(([label, value]) => (
                <div className="metadata-row" key={label}><span>{label}</span><code className={value !== "—" && value !== "等待提问" ? "has-value" : ""}>{value}</code></div>
              ))}
            </div>
            {lastResult?.request_id && (
              <button className="btn btn--outline btn--sm btn--block context-section__action" onClick={() => navigate("/after-sales")}>查看售后进度</button>
            )}
          </section>

          <section className="context-section">
            <h3>智能客服如何处理</h3>
            <div className="pipeline">
              {[
                ["理解问题", "识别您的诉求并关联订单"],
                ["核查事实", "订单、物流与证据交叉核验"],
                ["政策校验", "按平台规则与风险策略审核"],
                ["给出结论", "自动处理或转人工复核"],
              ].map(([step, hint], index) => (
                <div className="pipeline-step" key={step}>
                  <span className={lastResult ? "done" : index === 0 ? "active" : ""}>
                    {lastResult ? <CheckCircle2 size={11} /> : index + 1}
                  </span>
                  <div><b>{step}</b><small>{hint}</small></div>
                </div>
              ))}
            </div>
          </section>

          <section className="context-section">
            <h3>服务保障</h3>
            <div className="capability-grid">
              <div><Package size={14} /><span>订单核查</span></div>
              <div><Truck size={14} /><span>物流追踪</span></div>
              <div><ShieldCheck size={14} /><span>证据审核</span></div>
              <div><Box size={14} /><span>极速退款</span></div>
            </div>
          </section>

          {lastResult?.action_id && (
            <div className="action-receipt"><CheckCircle2 size={18} /><div><span>处理动作已记录</span><code>{lastResult.action_id}</code></div></div>
          )}
        </aside>
    </div>
    </div>
  );
}

export default ServiceCenter;
