import {
  Activity,
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
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

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

type OrderDetail = CustomerOrder & {
  logistics: LogisticsEvent[];
};

type EvidenceRecord = {
  evidence_id: string;
  request_id: string;
  original_filename: string;
  media_type: string;
  file_size_bytes: number;
  verification_status: string;
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

function App() {
  const [conversationId, setConversationId] = useState(() => createConversationId());
  const [messages, setMessages] = useState<Message[]>(() => initialMessages());
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [lastResult, setLastResult] = useState<ChatResponse | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [orders, setOrders] = useState<CustomerOrder[]>([]);
  const [activeCustomerId, setActiveCustomerId] = useState("CUS0021");
  const [activeOrderId, setActiveOrderId] = useState<string | null>(null);
  const [orderDetail, setOrderDetail] = useState<OrderDetail | null>(null);
  const [isUploadingEvidence, setIsUploadingEvidence] = useState(false);
  const [uploadedEvidence, setUploadedEvidence] = useState<EvidenceRecord | null>(null);
  const [connectionState, setConnectionState] = useState<"checking" | "online" | "offline">("checking");
  const bottomRef = useRef<HTMLDivElement>(null);

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
    const controller = new AbortController();
    fetch(`${API_BASE}/customers`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("客户列表加载失败");
        return response.json() as Promise<Customer[]>;
      })
      .then((data) => {
        setCustomers(data);
        if (!data.some((customer) => customer.customer_id === activeCustomerId) && data[0]) {
          setActiveCustomerId(data[0].customer_id);
        }
      })
      .catch(() => setConnectionState("offline"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/customers/${activeCustomerId}/orders`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("订单列表加载失败");
        return response.json() as Promise<CustomerOrder[]>;
      })
      .then((data) => {
        setOrders(data);
        setActiveOrderId(null);
      })
      .catch(() => {
        setOrders([]);
        setActiveOrderId(null);
        setConnectionState("offline");
      });
    return () => controller.abort();
  }, [activeCustomerId]);

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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

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
      if (result.request_id !== lastResult?.request_id) {
        setUploadedEvidence(null);
      }
      setLastResult(result);
      setConnectionState("online");
      setMessages((current) => [
        ...current,
        {
          id: `agent-${Date.now()}`,
          role: "agent",
          content: result.message,
          time: now(),
        },
      ]);
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

  function resetConversation() {
    setConversationId(createConversationId());
    setMessages(initialMessages());
    setLastResult(null);
    setInput("");
    setUploadedEvidence(null);
  }

  function selectCustomer(customerId: string) {
    setActiveCustomerId(customerId);
    setOrders([]);
    setActiveOrderId(null);
    resetConversation();
  }

  function selectOrder(order: CustomerOrder) {
    setActiveOrderId(order.order_id);
    setLastResult(null);
    if (order.order_status === "PendingShipment") {
      setInput(`订单${order.order_id}什么时候发货？`);
    } else if (order.order_status === "Shipped") {
      setInput(`订单${order.order_id}的物流到哪里了？`);
    } else {
      setInput(`订单${order.order_id}现在是什么状态？`);
    }
  }

  async function uploadEvidence(file: File) {
    const requestId = lastResult?.request_id;
    if (!requestId || isUploadingEvidence) return;
    if (file.size > 10 * 1024 * 1024) {
      setMessages((current) => [...current, { id: `upload-${Date.now()}`, role: "system", content: "图片不能超过 10MB。", time: now() }]);
      return;
    }

    const form = new FormData();
    form.append("customer_id", activeCustomerId);
    form.append("file", file);
    setIsUploadingEvidence(true);
    try {
      const response = await fetch(`${API_BASE}/service-requests/${requestId}/evidence`, {
        method: "POST",
        body: form,
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail || `上传失败 (${response.status})`);
      const evidence = body as EvidenceRecord;
      setUploadedEvidence(evidence);
      setMessages((current) => [...current, {
        id: `evidence-${Date.now()}`,
        role: "system",
        content: `证据 ${evidence.evidence_id} 已安全提交，当前状态为待审核。`,
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
      setIsUploadingEvidence(false);
    }
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
          <span className={`connection connection--${connectionState}`}>
            <i />
            {connectionState === "online" ? "服务在线" : connectionState === "checking" ? "连接中" : "连接异常"}
          </span>
          <button className="icon-button" aria-label="设置"><Settings size={16} /></button>
          <div className="operator">
            <span className="operator-avatar">AI</span>
            <span><b>Decision Agent</b><small>多智能体客服</small></span>
          </div>
        </div>
      </header>

      <main className="workspace">
        <nav className="rail" aria-label="主导航">
          <button className="rail-button rail-button--active" aria-label="会话"><MessageSquare size={19} /><em>1</em></button>
          <button className="rail-button" aria-label="订单"><Package size={19} /></button>
          <button className="rail-button" aria-label="工单"><FileText size={19} /></button>
          <button className="rail-button" aria-label="知识库"><HelpCircle size={19} /></button>
          <span className="rail-spacer" />
          <button className="rail-button" aria-label="系统状态"><Activity size={19} /></button>
        </nav>

        <aside className="scenario-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">MOCK CUSTOMER</span><h2>客户与订单</h2></div>
            <button className="mini-button" onClick={resetConversation} title="新建会话"><Plus size={14} /></button>
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
              onClick={() => {
                setActiveOrderId(null);
                setLastResult(null);
                setInput("我的订单现在是什么状态？");
              }}
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

          <div className="messages" aria-live="polite">
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
              <div key={message.id} className={`message-row message-row--${message.role}`}>
                <span className="message-avatar">
                  {message.role === "customer" ? <UserRound size={14} /> : message.role === "system" ? <Zap size={14} /> : <Bot size={14} />}
                </span>
                <div className="message-stack">
                  <div className="message-bubble">{message.content}</div>
                  <time>{message.time}</time>
                </div>
              </div>
            ))}
            {lastResult?.action === "CreateServiceRequest" && lastResult.request_id && (
              <div className="evidence-upload-card">
                <span className="evidence-upload-card__icon"><ImageUp size={19} /></span>
                <div>
                  <b>{uploadedEvidence ? "证据已提交" : "补充问题证据"}</b>
                  <p>{uploadedEvidence ? `${uploadedEvidence.evidence_id} · 等待审核` : "上传清晰的商品图片，支持 JPG、PNG、WebP，最大 10MB。"}</p>
                  <small>上传只代表材料已收到，不代表问题已确认或申请已批准。</small>
                </div>
                {!uploadedEvidence && (
                  <label className={isUploadingEvidence ? "disabled" : ""}>
                    {isUploadingEvidence ? <LoaderCircle size={14} className="spin" /> : <ImageUp size={14} />}
                    {isUploadingEvidence ? "上传中" : "选择图片"}
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      disabled={isUploadingEvidence}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void uploadEvidence(file);
                        event.target.value = "";
                      }}
                    />
                  </label>
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
      </main>
    </div>
  );
}

export default App;
