import { useState, useRef, useEffect } from "react";
import {
  MessageSquare, Package, FileText, HelpCircle,
  Send, Search, Bell, Settings, CheckCircle,
  Truck, MapPin, Star, ChevronDown, Filter,
  MoreHorizontal, Paperclip, RefreshCw, AlertTriangle,
  Plus, Phone, Mail, ShoppingBag, ChevronRight,
  Zap, Tag,
} from "lucide-react";

// ── Static data ───────────────────────────────────────────────────────
const conversations = [
  { id: 1, name: "李 薇",  avatar: "LW", avatarBg: "bg-blue-500",    issue: "包裹延迟未到",   order: "ORD-28471", time: "2分钟前",  status: "urgent",    unread: 3, last: "追踪信息已经5天没更新了" },
  { id: 2, name: "张 明",  avatar: "ZM", avatarBg: "bg-emerald-500", issue: "退款申请",       order: "ORD-28305", time: "15分钟前", status: "pending",   unread: 1, last: "我想申请退款，商品与描述不符" },
  { id: 3, name: "王 芳",  avatar: "WF", avatarBg: "bg-purple-500",  issue: "修改收货地址",   order: "ORD-28198", time: "32分钟前", status: "active",    unread: 0, last: "好的，谢谢您的帮助！" },
  { id: 4, name: "陈 浩",  avatar: "CH", avatarBg: "bg-amber-500",   issue: "发票开具问题",   order: "ORD-27956", time: "1小时前",  status: "pending",   unread: 2, last: "请问增值税发票可以开吗" },
  { id: 5, name: "刘 娜",  avatar: "LN", avatarBg: "bg-rose-500",    issue: "商品质量投诉",   order: "ORD-27834", time: "2小时前",  status: "escalated", unread: 0, last: "已提交给质检部门处理" },
];

const initMessages = [
  { id: 1, role: "customer", content: "你好，我的订单 #ORD-28471 本来应该3天前送到，但一直没收到。", time: "14:23" },
  { id: 2, role: "agent",    content: "您好，李薇女士！非常抱歉给您带来不便。我马上为您查询订单状态。", time: "14:24" },
  { id: 3, role: "agent",    content: "我看到您的订单目前显示「运输中」，最后更新是5天前在上海转运中心。让我联系物流公司了解具体情况。", time: "14:25" },
  { id: 4, role: "customer", content: "是的，追踪信息已经5天没更新了，我很担心包裹丢失了。", time: "14:26" },
  { id: 5, role: "customer", content: "我买的是给孩子的生日礼物，明天就是生日了…😢", time: "14:26" },
  { id: 6, role: "agent",    content: "我完全理解您的心情。已将此单标记为紧急优先处理，并联系了顺丰客服。预计2小时内会有回复。如确认延误，我们会为您安排补发或全额退款，您可自行选择。", time: "14:28" },
];

const tracking = [
  { label: "已下单",  time: "01-08 10:32", done: true,  note: "买家已付款" },
  { label: "商家发货", time: "01-08 16:45", done: true,  note: "上海仓库" },
  { label: "运输中",  time: "01-09 08:20", done: true,  note: "上海转运中心" },
  { label: "派送延迟", time: "—",           done: false, note: "区域分拣中心异常" },
  { label: "派送中",  time: "—",           done: false, note: "" },
  { label: "已签收",  time: "—",           done: false, note: "" },
];

const faqs = [
  {
    cat: "退换货",
    items: [
      { q: "如何申请退款？", a: "在订单页面点击「申请退款」，选择退款原因并提交。1-3个工作日内处理。" },
      { q: "退货地址在哪里？", a: "退货地址根据商家不同而不同。请在退货申请通过后查看系统提供的退货标签。" },
      { q: "退款多久到账？", a: "退款将在我们收到退货并验收后3-5个工作日内退回原支付账户。" },
    ],
  },
  {
    cat: "物流配送",
    items: [
      { q: "如何查询物流信息？", a: "在「我的订单」页面点击订单，可查看实时物流轨迹，也可直接联系客服查询。" },
      { q: "可以修改收货地址吗？", a: "订单未发货前可修改。已发货订单需联系客服，部分情况可协调修改，但无法保证成功。" },
    ],
  },
  {
    cat: "支付与发票",
    items: [
      { q: "支持哪些支付方式？", a: "支持支付宝、微信支付、银联卡、花呗分期等主流支付方式。" },
      { q: "如何开具发票？", a: "在订单详情页点击「申请发票」，填写抬头信息，电子发票将发至您的邮箱。" },
    ],
  },
];

const tickets = [
  { id: "TKT-5521", customer: "刘 娜", issue: "商品质量投诉",   priority: "high",   status: "escalated",   sla: "2小时后到期",  slaAlert: true },
  { id: "TKT-5520", customer: "陈 浩", issue: "发票开具问题",   priority: "medium", status: "open",        sla: "8小时后到期",  slaAlert: false },
  { id: "TKT-5519", customer: "赵 磊", issue: "账号被盗申诉",   priority: "high",   status: "in_progress", sla: "已逾期",       slaAlert: true },
  { id: "TKT-5518", customer: "孙 悦", issue: "积分未到账",     priority: "low",    status: "resolved",    sla: "已解决",       slaAlert: false },
  { id: "TKT-5517", customer: "周 鹏", issue: "促销价格异议",   priority: "medium", status: "open",        sla: "12小时后到期", slaAlert: false },
];

// ── Badge configs ─────────────────────────────────────────────────────
const convStatus: Record<string, { label: string; cls: string }> = {
  urgent:    { label: "紧急",  cls: "bg-red-500/20 text-red-400 border-red-500/30" },
  pending:   { label: "待处理", cls: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
  active:    { label: "处理中", cls: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" },
  escalated: { label: "已升级", cls: "bg-purple-500/20 text-purple-400 border-purple-500/30" },
};
const tktStatus: Record<string, { label: string; cls: string }> = {
  open:        { label: "待处理", cls: "bg-amber-500/15 text-amber-400" },
  in_progress: { label: "处理中", cls: "bg-blue-500/15 text-blue-400" },
  escalated:   { label: "已升级", cls: "bg-purple-500/15 text-purple-400" },
  resolved:    { label: "已解决", cls: "bg-emerald-500/15 text-emerald-400" },
};
const priority: Record<string, { label: string; cls: string }> = {
  high:   { label: "高", cls: "text-red-400" },
  medium: { label: "中", cls: "text-amber-400" },
  low:    { label: "低", cls: "text-emerald-400" },
};

// ── Component ─────────────────────────────────────────────────────────
type NavId = "conversations" | "orders" | "tickets" | "faq";

export default function App() {
  const [nav, setNav] = useState<NavId>("conversations");
  const [selConv, setSelConv] = useState(0);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState(initMessages);
  const [faqCat, setFaqCat] = useState<Record<number, boolean>>({ 0: true });
  const [faqItem, setFaqItem] = useState<Record<string, boolean>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = () => {
    if (!input.trim()) return;
    setMessages(p => [
      ...p,
      {
        id: p.length + 1,
        role: "agent",
        content: input,
        time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
      },
    ]);
    setInput("");
  };

  const conv = conversations[selConv];

  const navItems = [
    { id: "conversations" as NavId, Icon: MessageSquare, label: "会话", badge: 6 },
    { id: "orders"        as NavId, Icon: Package,       label: "订单", badge: null },
    { id: "tickets"       as NavId, Icon: FileText,      label: "工单", badge: 3 },
    { id: "faq"           as NavId, Icon: HelpCircle,    label: "知识库", badge: null },
  ];

  const quickReplies = ["感谢您的耐心等待", "已为您紧急处理", "请提供订单截图", "申请补发/退款"];

  return (
    <div
      className="h-screen flex flex-col bg-background text-foreground overflow-hidden"
      style={{ fontFamily: "'Outfit', sans-serif" }}
    >
      {/* ── Header ─────────────────────────────────────────────────── */}
      <header className="h-13 border-b border-border flex items-center px-4 gap-4 flex-shrink-0 bg-card z-10" style={{ height: "52px" }}>
        <div className="flex items-center gap-2.5 w-56 flex-shrink-0">
          <div className="w-7 h-7 bg-primary rounded flex items-center justify-center flex-shrink-0">
            <ShoppingBag size={13} className="text-white" />
          </div>
          <span
            className="font-bold tracking-wider text-foreground"
            style={{ fontFamily: "'Barlow Condensed', sans-serif", fontSize: "17px", letterSpacing: "0.08em" }}
          >
            SHOPCARE
          </span>
          <span className="text-[10px] text-muted-foreground font-mono ml-0.5">客服台</span>
        </div>

        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              className="w-full bg-secondary border border-border rounded-lg pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:border-primary/40 placeholder:text-muted-foreground transition-colors"
              placeholder="搜索订单、客户、工单..."
            />
          </div>
        </div>

        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />
            在线
          </div>
          <button className="relative p-1.5 hover:bg-secondary rounded-md transition-colors">
            <Bell size={15} className="text-muted-foreground" />
            <span className="absolute top-1 right-1 w-1.5 h-1.5 bg-primary rounded-full" />
          </button>
          <button className="p-1.5 hover:bg-secondary rounded-md transition-colors">
            <Settings size={15} className="text-muted-foreground" />
          </button>
          <div className="flex items-center gap-2 pl-3 border-l border-border">
            <div className="w-7 h-7 rounded-full bg-primary/25 flex items-center justify-center text-primary text-xs font-bold">
              王
            </div>
            <div className="text-xs leading-tight">
              <div className="font-medium">王 静</div>
              <div className="text-muted-foreground text-[10px]">高级客服</div>
            </div>
          </div>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* ── Icon nav ───────────────────────────────────────────────── */}
        <nav className="w-14 border-r border-border flex flex-col items-center py-3 gap-1 bg-card flex-shrink-0">
          {navItems.map(({ id, Icon, badge }) => (
            <button
              key={id}
              onClick={() => setNav(id)}
              title={id}
              className={`relative w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${
                nav === id
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary"
              }`}
            >
              <Icon size={18} />
              {badge !== null && (
                <span className="absolute -top-0.5 -right-0.5 w-4 h-4 bg-primary rounded-full text-white text-[10px] flex items-center justify-center font-mono leading-none">
                  {badge}
                </span>
              )}
            </button>
          ))}
        </nav>

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  CONVERSATIONS                                              */}
        {/* ═══════════════════════════════════════════════════════════ */}
        {nav === "conversations" && (
          <>
            {/* Conversation list */}
            <aside className="w-72 border-r border-border flex flex-col flex-shrink-0 bg-card">
              <div className="p-3 border-b border-border space-y-2">
                <div className="flex items-center justify-between">
                  <span
                    className="font-semibold"
                    style={{ fontFamily: "'Barlow Condensed', sans-serif", fontSize: "16px", letterSpacing: "0.06em" }}
                  >
                    活跃会话
                  </span>
                  <div className="flex gap-1">
                    <button className="p-1 hover:bg-secondary rounded text-muted-foreground hover:text-foreground transition-colors">
                      <Filter size={12} />
                    </button>
                    <button className="p-1 hover:bg-secondary rounded text-muted-foreground hover:text-foreground transition-colors">
                      <Plus size={12} />
                    </button>
                  </div>
                </div>
                <div className="flex gap-1 text-xs">
                  {["全部 (6)", "待处理 (3)", "紧急 (1)"].map((t, i) => (
                    <button
                      key={t}
                      className={`px-2 py-0.5 rounded transition-colors ${
                        i === 0 ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-secondary"
                      }`}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex-1 overflow-y-auto">
                {conversations.map((c, i) => (
                  <button
                    key={c.id}
                    onClick={() => setSelConv(i)}
                    className={`w-full px-3 py-3 flex gap-2.5 text-left border-b border-border/40 transition-colors ${
                      selConv === i ? "bg-secondary/70" : "hover:bg-secondary/40"
                    }`}
                  >
                    <div className="relative flex-shrink-0">
                      <div className={`w-9 h-9 rounded-full ${c.avatarBg} flex items-center justify-center text-white text-xs font-bold`}>
                        {c.avatar}
                      </div>
                      {c.unread > 0 && (
                        <span className="absolute -top-0.5 -right-0.5 w-4 h-4 bg-primary rounded-full text-white text-[10px] flex items-center justify-center font-mono">
                          {c.unread}
                        </span>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="text-sm font-medium">{c.name}</span>
                        <span className="text-[10px] text-muted-foreground">{c.time}</span>
                      </div>
                      <div className="flex items-center gap-1.5 mb-1">
                        <span className="font-mono text-[10px] text-muted-foreground">{c.order}</span>
                        <span className={`text-[10px] px-1.5 py-px rounded border ${convStatus[c.status].cls}`}>
                          {convStatus[c.status].label}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground truncate">{c.last}</p>
                    </div>
                  </button>
                ))}
              </div>
            </aside>

            {/* Chat main */}
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Chat header */}
              <div className="border-b border-border px-4 flex items-center gap-3 flex-shrink-0 bg-card/40" style={{ height: "52px" }}>
                <div className={`w-8 h-8 rounded-full ${conv.avatarBg} flex items-center justify-center text-white text-xs font-bold`}>
                  {conv.avatar}
                </div>
                <div className="leading-tight">
                  <div className="text-sm font-semibold">{conv.name}</div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="font-mono">{conv.order}</span>
                    <span>·</span>
                    <span>{conv.issue}</span>
                  </div>
                </div>
                <div className="ml-auto flex items-center gap-2">
                  <button className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-secondary hover:bg-secondary/60 rounded-md transition-colors text-muted-foreground">
                    <RefreshCw size={11} />
                    转接
                  </button>
                  <button className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 rounded-md transition-colors text-emerald-400 border border-emerald-500/20">
                    <CheckCircle size={11} />
                    结单
                  </button>
                  <button className="p-1.5 hover:bg-secondary rounded-md transition-colors text-muted-foreground">
                    <MoreHorizontal size={15} />
                  </button>
                </div>
              </div>

              {/* Messages */}
              <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
                <div className="text-center py-1">
                  <span className="text-[10px] text-muted-foreground bg-secondary/60 px-3 py-1 rounded-full font-mono">
                    今天 · 会话开始于 14:22
                  </span>
                </div>

                {messages.map(msg => (
                  <div key={msg.id} className={`flex gap-2.5 ${msg.role === "agent" ? "flex-row-reverse" : ""}`}>
                    <div
                      className={`w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-white text-[10px] font-bold mt-0.5 ${
                        msg.role === "customer" ? conv.avatarBg : "bg-primary/30"
                      }`}
                    >
                      {msg.role === "customer" ? conv.avatar : "王"}
                    </div>
                    <div className={`max-w-[65%] flex flex-col gap-0.5 ${msg.role === "agent" ? "items-end" : "items-start"}`}>
                      <div
                        className={`px-3.5 py-2 text-sm leading-relaxed ${
                          msg.role === "agent"
                            ? "bg-primary text-white rounded-2xl rounded-tr-sm"
                            : "bg-secondary text-foreground rounded-2xl rounded-tl-sm"
                        }`}
                      >
                        {msg.content}
                      </div>
                      <span className="text-[10px] text-muted-foreground px-1 font-mono">{msg.time}</span>
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>

              {/* Quick replies */}
              <div className="px-4 pb-2">
                <div className="flex gap-2 overflow-x-auto scrollbar-none">
                  {quickReplies.map(r => (
                    <button
                      key={r}
                      onClick={() => setInput(r)}
                      className="flex-shrink-0 text-xs px-2.5 py-1 bg-secondary hover:bg-secondary/70 rounded-full text-muted-foreground hover:text-foreground transition-colors border border-border/60"
                    >
                      {r}
                    </button>
                  ))}
                </div>
              </div>

              {/* Input */}
              <div className="border-t border-border p-3 bg-card/20">
                <div className="flex items-end gap-2">
                  <div className="flex-1 bg-secondary border border-border rounded-xl px-3 py-2 focus-within:border-primary/40 transition-colors">
                    <textarea
                      value={input}
                      onChange={e => setInput(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          send();
                        }
                      }}
                      placeholder="输入回复内容… (Enter 发送，Shift+Enter 换行)"
                      className="w-full bg-transparent text-sm resize-none focus:outline-none placeholder:text-muted-foreground max-h-24"
                      rows={2}
                    />
                    <div className="flex items-center gap-1 mt-1">
                      <button className="p-0.5 text-muted-foreground hover:text-foreground transition-colors">
                        <Paperclip size={12} />
                      </button>
                      <button className="p-0.5 text-muted-foreground hover:text-foreground transition-colors">
                        <Tag size={12} />
                      </button>
                      <button className="p-0.5 text-muted-foreground hover:text-foreground transition-colors">
                        <Zap size={12} />
                      </button>
                    </div>
                  </div>
                  <button
                    onClick={send}
                    disabled={!input.trim()}
                    className="p-2.5 bg-primary text-white rounded-xl hover:bg-primary/85 transition-colors disabled:opacity-35 disabled:cursor-not-allowed flex-shrink-0"
                  >
                    <Send size={15} />
                  </button>
                </div>
              </div>
            </div>

            {/* Right info panel */}
            <aside className="w-68 border-l border-border overflow-y-auto flex-shrink-0 bg-card/20" style={{ width: "272px" }}>
              {/* Customer */}
              <div className="p-4 border-b border-border">
                <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest mb-3">客户信息</p>
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-10 h-10 rounded-full ${conv.avatarBg} flex items-center justify-center text-white font-bold text-sm`}>
                    {conv.avatar}
                  </div>
                  <div>
                    <div className="font-semibold text-sm">{conv.name}</div>
                    <div className="flex items-center gap-0.5 mt-0.5">
                      {[1, 2, 3, 4].map(n => (
                        <Star key={n} size={10} className="text-amber-400" fill="currentColor" />
                      ))}
                      <Star size={10} className="text-muted-foreground" />
                      <span className="text-[10px] text-muted-foreground ml-1">钻石会员</span>
                    </div>
                  </div>
                </div>
                <div className="space-y-1.5 text-xs text-muted-foreground">
                  <div className="flex items-center gap-2"><Mail size={10} />liwei@example.com</div>
                  <div className="flex items-center gap-2"><Phone size={10} />138 **** 7829</div>
                  <div className="flex items-center gap-2"><MapPin size={10} />上海市浦东新区</div>
                </div>
                <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-border text-center">
                  {[["历史订单", "24"], ["投诉次数", "1"], ["满意度", "92%"]].map(([l, v]) => (
                    <div key={l}>
                      <div className="text-sm font-semibold font-mono">{v}</div>
                      <div className="text-[10px] text-muted-foreground">{l}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Order tracking */}
              <div className="p-4 border-b border-border">
                <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest mb-3">订单追踪</p>
                <div className="bg-secondary/50 rounded-lg p-2.5 mb-4 border border-border/60">
                  <div className="flex items-start justify-between mb-1">
                    <div>
                      <div className="font-mono text-sm font-semibold">{conv.order}</div>
                      <div className="text-[10px] text-muted-foreground mt-0.5">顺丰 · SF2847193021</div>
                    </div>
                    <span className="text-[10px] px-1.5 py-0.5 bg-amber-500/20 text-amber-400 rounded font-mono">延迟</span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    <span className="text-foreground font-medium">¥ 328.00</span> · 运动耳机 × 1
                  </div>
                </div>

                <div>
                  {tracking.map((step, i) => (
                    <div key={i} className="flex gap-2.5">
                      <div className="flex flex-col items-center">
                        <div
                          className={`w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 border ${
                            step.done ? "border-primary bg-primary/20" : "border-border bg-secondary"
                          }`}
                        >
                          <div className={`w-1.5 h-1.5 rounded-full ${step.done ? "bg-primary" : "bg-muted-foreground/25"}`} />
                        </div>
                        {i < tracking.length - 1 && (
                          <div className={`w-px my-1 ${step.done ? "bg-primary/30" : "bg-border"}`} style={{ minHeight: "14px" }} />
                        )}
                      </div>
                      <div className="pb-3 min-w-0">
                        <div className={`text-xs font-medium ${step.done ? "text-foreground" : "text-muted-foreground"}`}>
                          {step.label}
                        </div>
                        <div className="text-[10px] text-muted-foreground font-mono">
                          {step.time}{step.note ? ` · ${step.note}` : ""}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Quick actions */}
              <div className="p-4">
                <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest mb-3">快速操作</p>
                <div className="space-y-1.5">
                  {[
                    { label: "申请紧急补发",  Icon: Package,       cls: "text-blue-400" },
                    { label: "发起全额退款",  Icon: RefreshCw,     cls: "text-emerald-400" },
                    { label: "升级工单",     Icon: AlertTriangle,  cls: "text-amber-400" },
                    { label: "联系物流公司",  Icon: Truck,          cls: "text-purple-400" },
                  ].map(({ label, Icon, cls }) => (
                    <button
                      key={label}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg bg-secondary/40 hover:bg-secondary border border-border/40 transition-colors text-left"
                    >
                      <Icon size={12} className={cls} />
                      <span className="text-xs">{label}</span>
                      <ChevronRight size={10} className="ml-auto text-muted-foreground" />
                    </button>
                  ))}
                </div>
              </div>
            </aside>
          </>
        )}

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  TICKETS                                                    */}
        {/* ═══════════════════════════════════════════════════════════ */}
        {nav === "tickets" && (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-4xl mx-auto">
              <div className="flex items-center justify-between mb-6">
                <h1
                  className="font-bold text-2xl"
                  style={{ fontFamily: "'Barlow Condensed', sans-serif", letterSpacing: "0.06em" }}
                >
                  工单管理
                </h1>
                <button className="flex items-center gap-2 px-3 py-1.5 bg-primary text-white rounded-lg text-sm hover:bg-primary/85 transition-colors">
                  <Plus size={13} />
                  新建工单
                </button>
              </div>

              <div className="bg-card border border-border rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      {["工单号", "客户", "问题", "优先级", "状态", "SLA"].map(h => (
                        <th
                          key={h}
                          className="text-left px-4 py-3 text-[10px] text-muted-foreground font-mono uppercase tracking-widest font-normal"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tickets.map(t => (
                      <tr key={t.id} className="border-b border-border/40 hover:bg-secondary/30 transition-colors cursor-pointer">
                        <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{t.id}</td>
                        <td className="px-4 py-3 font-medium">{t.customer}</td>
                        <td className="px-4 py-3 text-muted-foreground">{t.issue}</td>
                        <td className={`px-4 py-3 text-xs font-semibold ${priority[t.priority].cls}`}>
                          {priority[t.priority].label}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`text-xs px-2 py-0.5 rounded-full ${tktStatus[t.status].cls}`}>
                            {tktStatus[t.status].label}
                          </span>
                        </td>
                        <td className={`px-4 py-3 text-xs font-mono ${
                          t.sla === "已逾期" ? "text-red-400" : t.sla === "已解决" ? "text-emerald-400" : "text-muted-foreground"
                        }`}>
                          {t.sla}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  FAQ / Knowledge base                                       */}
        {/* ═══════════════════════════════════════════════════════════ */}
        {nav === "faq" && (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-2xl mx-auto">
              <div className="flex items-center justify-between mb-6">
                <h1
                  className="font-bold text-2xl"
                  style={{ fontFamily: "'Barlow Condensed', sans-serif", letterSpacing: "0.06em" }}
                >
                  知识库 / FAQ
                </h1>
                <div className="relative">
                  <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input
                    className="bg-secondary border border-border rounded-lg pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:border-primary/40 placeholder:text-muted-foreground"
                    placeholder="搜索问题…"
                  />
                </div>
              </div>

              <div className="space-y-3">
                {faqs.map((cat, ci) => (
                  <div key={ci} className="bg-card border border-border rounded-xl overflow-hidden">
                    <button
                      onClick={() => setFaqCat(p => ({ ...p, [ci]: !p[ci] }))}
                      className="w-full flex items-center justify-between px-4 py-3 hover:bg-secondary/30 transition-colors"
                    >
                      <span
                        className="font-semibold"
                        style={{ fontFamily: "'Barlow Condensed', sans-serif", fontSize: "16px", letterSpacing: "0.05em" }}
                      >
                        {cat.cat}
                      </span>
                      <ChevronDown
                        size={13}
                        className={`text-muted-foreground transition-transform duration-200 ${faqCat[ci] ? "rotate-180" : ""}`}
                      />
                    </button>

                    {faqCat[ci] && (
                      <div className="border-t border-border divide-y divide-border/40">
                        {cat.items.map((item, qi) => {
                          const key = `${ci}-${qi}`;
                          return (
                            <div key={qi}>
                              <button
                                onClick={() => setFaqItem(p => ({ ...p, [key]: !p[key] }))}
                                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-secondary/20 transition-colors"
                              >
                                <span className="text-sm">{item.q}</span>
                                <ChevronDown
                                  size={11}
                                  className={`flex-shrink-0 ml-3 text-muted-foreground transition-transform duration-200 ${
                                    faqItem[key] ? "rotate-180" : ""
                                  }`}
                                />
                              </button>
                              {faqItem[key] && (
                                <div className="px-4 pb-3 text-sm text-muted-foreground leading-relaxed">{item.a}</div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════ */}
        {/*  ORDERS (placeholder)                                       */}
        {/* ═══════════════════════════════════════════════════════════ */}
        {nav === "orders" && (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Package size={40} className="mx-auto mb-3 opacity-25" />
              <div className="text-sm font-medium">订单查询</div>
              <div className="text-xs mt-1 opacity-60">在会话中点击订单号可快速查看</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
