import {
  CheckCircle2,
  BarChart3,
  ClipboardList,
  FileImage,
  History,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MessagesSquare,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
  ShoppingBag,
  Store,
  Wrench,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { API_BASE } from "./shop/api";
import { navigate, useLocation } from "./shop/router";
import { StaffApiError, staffFetch, StaffIdentity, StaffQueue, StaffReturnOrder } from "./staff/api";
import { DashboardPage } from "./staff/Dashboard";
import { Handoff, LiveChatPage } from "./staff/LiveChat";
import { ActionsPage, EvidencePage, HistoryPage, OverviewPage, PageProps, QueuePage, ReturnsPage } from "./staff/pages";
import "./staff/staff.css";

// Holds the short-lived session token from /staff/auth/login, never the
// long-lived access key itself.
const TOKEN_KEY = "staff_session_token";
const RETURN_KEY = "staff:return-customer";
const REFRESH_MS = 30_000;

const SECTIONS = [
  { key: "", label: "工作台", icon: LayoutDashboard, title: "工作台概览" },
  { key: "queue", label: "人工工单", icon: ClipboardList, title: "待人工裁决的售后申请" },
  { key: "live", label: "在线会话", icon: MessagesSquare, title: "与顾客的人工在线会话" },
  { key: "evidence", label: "证据审核", icon: FileImage, title: "待审核的证据材料" },
  { key: "returns", label: "退货验收", icon: PackageCheck, title: "退货收货与验收" },
  { key: "actions", label: "履约异常", icon: Wrench, title: "执行失败的履约任务" },
  { key: "history", label: "全部工单", icon: History, title: "全部售后工单" },
  { key: "insights", label: "数据看板", icon: BarChart3, title: "服务数据看板" },
] as const;

function readToken() {
  try { return window.localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function writeToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch { /* best-effort */ }
}

// The shop links here as /staff?returnCustomer=…; remember that we came from
// the shop so "返回商城" goes back to its service center.
function returnHref() {
  try {
    if (new URLSearchParams(window.location.search).get("returnCustomer")) window.sessionStorage.setItem(RETURN_KEY, "1");
    return window.sessionStorage.getItem(RETURN_KEY) ? "/?resumeCustomer=1" : "/";
  } catch {
    return "/";
  }
}

async function exchangeAccessKey(accessKey: string): Promise<string> {
  const response = await fetch(`${API_BASE}/staff/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_key: accessKey }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(response.status === 401 ? "访问令牌无效，请检查后重试。" : body?.detail || "登录失败，请稍后重试。");
  return body.access_token as string;
}

function StaffConsole() {
  const location = useLocation();
  const [token, setToken] = useState(readToken);
  const [identity, setIdentity] = useState<StaffIdentity | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [queue, setQueue] = useState<StaffQueue | null>(null);
  const [returns, setReturns] = useState<StaffReturnOrder[] | null>(null);
  const [waitingChats, setWaitingChats] = useState(0);
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [backHref] = useState(returnHref);
  // Pending-work count seen on the previous refresh, to announce new arrivals.
  const pendingCountRef = useRef<number | null>(null);

  const section = location.segments[1] ?? "";
  const meta = SECTIONS.find((item) => item.key === section) ?? SECTIONS[0];
  const selectedId = location.query.get("id");

  const logout = useCallback((message?: string) => {
    writeToken(null);
    setToken("");
    setIdentity(null);
    setQueue(null);
    setReturns(null);
    setAuthError(message ?? null);
  }, []);

  useEffect(() => {
    if (!token) return;
    staffFetch<StaffIdentity>(token, "/me")
      .then(setIdentity)
      .catch((error) => logout(error instanceof StaffApiError && error.status === 401 ? "登录已过期，请重新登录。" : "无法连接服务，请稍后重试。"));
  }, [token, logout]);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [nextQueue, nextReturns, sessions] = await Promise.all([
        staffFetch<StaffQueue>(token, "/queue"),
        staffFetch<StaffReturnOrder[]>(token, "/return-orders").catch(() => []),
        staffFetch<Handoff[]>(token, "/live").catch(() => []),
      ]);
      setWaitingChats(sessions.filter((session) => session.workflow_status === "Waiting").length);
      const pending = nextQueue.service_requests.length + nextQueue.evidence.length;
      if (pendingCountRef.current !== null && pending > pendingCountRef.current) {
        setToast(`有 ${pending - pendingCountRef.current} 项新的待处理工作进入队列`);
      }
      pendingCountRef.current = pending;
      setQueue(nextQueue);
      setReturns(nextReturns);
      setUpdatedAt(new Date());
    } catch (error) {
      if (error instanceof StaffApiError && (error.status === 401 || error.status === 403)) logout("访问令牌无效或权限不足，请重新登录。");
    } finally {
      setLoading(false);
    }
  }, [token, logout]);

  useEffect(() => {
    if (!identity) return;
    void reload();
    const timer = window.setInterval(() => void reload(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [identity, reload]);

  // Push updates: the server announces queue changes and new chat sessions,
  // so the 30s interval above is only a fallback.
  useEffect(() => {
    if (!identity || typeof EventSource === "undefined") return;
    const source = new EventSource(`${API_BASE}/staff/live/stream?access_token=${encodeURIComponent(token)}`);
    const refresh = () => void reload();
    source.addEventListener("queue", refresh);
    source.addEventListener("handoff", refresh);
    return () => source.close();
  }, [identity, token, reload]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  if (!token || !identity) {
    return (
      <StaffLogin
        pending={Boolean(token) && !authError}
        error={authError}
        backHref={backHref}
        onLogin={async (accessKey) => {
          setAuthError(null);
          try {
            const session = await exchangeAccessKey(accessKey);
            writeToken(session);
            setToken(session);
          } catch (error) {
            setAuthError(error instanceof Error ? error.message : "登录失败");
          }
        }}
      />
    );
  }

  const counts: Record<string, number> = {
    queue: queue?.service_requests.length ?? 0,
    evidence: queue?.evidence.length ?? 0,
    returns: (returns ?? []).filter((item) => item.workflow_status !== "AwaitingShipment").length,
    actions: queue?.failed_actions.length ?? 0,
    live: waitingChats,
  };

  const go = (next: string, id?: string) => navigate(`/staff${next ? `/${next}` : ""}${id ? `?id=${encodeURIComponent(id)}` : ""}`);
  const props: PageProps = {
    token,
    identity,
    queue,
    returns,
    selectedId,
    select: (id) => navigate(`/staff${section ? `/${section}` : ""}${id ? `?id=${encodeURIComponent(id)}` : ""}`, { replace: true }),
    go,
    done: (message) => { setToast(message); void reload(); },
  };

  return (
    <div className="staff-app">
      <aside className="staff-sidebar">
        <a className="staff-brand" href="/staff" onClick={(event) => { event.preventDefault(); go(""); }}>
          <span className="logo__mark"><ShoppingBag size={18} /></span>
          <span><b>ShopCare</b><small>客服工作台</small></span>
        </a>
        <nav>
          {SECTIONS.map(({ key, label, icon: Icon }) => (
            <a key={key} href={`/staff${key ? `/${key}` : ""}`} className={section === key ? "active" : ""} onClick={(event) => { event.preventDefault(); go(key); }}>
              <Icon size={17} />{label}
              {counts[key] > 0 && <em>{counts[key]}</em>}
            </a>
          ))}
        </nav>
        <div className="staff-sidebar__foot">
          <div className="staff-user">
            <span className="avatar"><ShieldCheck size={16} /></span>
            <span><b>{identity.staff_id}</b><small>{identity.roles.map((role) => ({ admin: "管理员", reviewer: "审核员", viewer: "只读" }[role] ?? role)).join(" · ")}</small></span>
          </div>
          <a className="staff-sidebar__link" href={backHref}><Store size={15} />返回商城</a>
          <button className="staff-sidebar__link" onClick={() => logout()}><LogOut size={15} />退出登录</button>
        </div>
      </aside>

      <div className="staff-main">
        <header className="staff-topbar">
          <h1>{meta.title}</h1>
          <span className="staff-topbar__updated">{updatedAt ? `${updatedAt.toLocaleTimeString("zh-CN", { hour12: false })} 更新 · 每 30 秒自动刷新` : "加载中…"}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => void reload()} disabled={loading}><RefreshCw size={14} className={loading ? "spin" : ""} />刷新</button>
        </header>
        <main className="staff-content">
          {section === "" && <OverviewPage {...props} />}
          {section === "queue" && <QueuePage {...props} />}
          {section === "live" && <LiveChatPage token={token} identity={identity} selectedId={selectedId} select={props.select} />}
          {section === "evidence" && <EvidencePage {...props} />}
          {section === "returns" && <ReturnsPage {...props} />}
          {section === "actions" && <ActionsPage {...props} />}
          {section === "history" && <HistoryPage {...props} />}
          {section === "insights" && <DashboardPage token={token} canExport={identity.roles.some((role) => role === "reviewer" || role === "admin")} />}
        </main>
      </div>

      {toast && <div className="toast"><CheckCircle2 size={16} />{toast}</div>}
    </div>
  );
}

function StaffLogin({ pending, error, backHref, onLogin }: { pending: boolean; error: string | null; backHref: string; onLogin: (accessKey: string) => Promise<void> }) {
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setSubmitting(true);
    await onLogin(value.trim());
    setSubmitting(false);
  }
  return (
    <div className="staff-login">
      <form className="staff-login__card" onSubmit={submit}>
        <span className="logo__mark staff-login__mark"><ShoppingBag size={22} /></span>
        <h1>ShopCare 客服工作台</h1>
        <p>处理智能客服转交的售后申请、证据审核与退货验收。</p>
        <label>
          <span>访问令牌</span>
          <span className="staff-login__input"><KeyRound size={16} /><input type="password" value={value} onChange={(event) => setValue(event.target.value)} placeholder="输入 Staff API Token" autoFocus /></span>
        </label>
        {error && <div className="staff-warning">{error}</div>}
        <button className="btn btn--primary btn--lg btn--block" type="submit" disabled={!value.trim() || pending || submitting}>{pending || submitting ? "验证中…" : "登录"}</button>
        <small className="muted">令牌在 <code>.env</code> 的 <code>STAFF_API_KEYS</code> 中配置，角色分为 reviewer / admin / viewer。登录后浏览器只保存 8 小时有效的会话凭证。</small>
        <a className="staff-login__back" href={backHref}><Store size={14} />返回商城</a>
      </form>
    </div>
  );
}

export default StaffConsole;
