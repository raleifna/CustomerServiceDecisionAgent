import { Bot, CheckCircle2, Headset, Loader, MessagesSquare, Send, UserRound, XCircle, Zap } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, ProductCard } from "../shop/api";
import { ProductImage } from "../shop/ProductImage";
import { ageText, formatDateTime, staffFetch, StaffIdentity } from "./api";

export type Handoff = {
  handoff_id: string;
  customer_id: string;
  order_id: string | null;
  request_id: string | null;
  reason: string;
  workflow_status: "Waiting" | "Active" | "Ended";
  staff_id: string | null;
  accepted_at: string | null;
  ended_at: string | null;
  end_reason: string | null;
  last_activity_at: string | null;
  created_at: string;
  last_message?: string | null;
  last_sender?: string | null;
  product?: ProductCard | null;
};

type LiveMessage = { message_id: number; handoff_id: string; sender: "customer" | "staff" | "system"; sender_id: string | null; content: string; created_at: string };

type Detail = {
  handoff: Handoff;
  messages: LiveMessage[];
  ai_transcript: { role: string; content: string }[];
  service_request: { request_id: string; request_reason: string; workflow_status: string } | null;
};

const QUICK_REPLIES = [
  "您好，我是人工客服，已经看过您和智能助手的对话，正在为您核实。",
  "麻烦您补充一张商品问题部位的清晰照片，我这边帮您加急处理。",
  "已为您核实完毕，处理结果会同步到「退款/售后」页面，请留意查看。",
  "请问还有其他可以帮您的吗？如果没有，我先结束本次会话。",
];

const statusLabels = { Waiting: "排队中", Active: "服务中", Ended: "已结束" } as const;

export function LiveChatPage({ token, identity, selectedId, select }: {
  token: string;
  identity: StaffIdentity | null;
  selectedId: string | null;
  select: (id: string | null) => void;
}) {
  const [sessions, setSessions] = useState<Handoff[] | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [showTranscript, setShowTranscript] = useState(true);
  const selectedRef = useRef(selectedId);
  const threadRef = useRef<HTMLDivElement>(null);
  selectedRef.current = selectedId;
  const writable = Boolean(identity?.roles.some((role) => role === "reviewer" || role === "admin"));

  const loadSessions = useCallback(() => {
    staffFetch<Handoff[]>(token, "/live").then(setSessions).catch((err) => setError(err instanceof Error ? err.message : "加载失败"));
  }, [token]);

  const loadDetail = useCallback((id: string) => {
    staffFetch<Detail>(token, `/live/${encodeURIComponent(id)}`).then(setDetail).catch((err) => setError(err instanceof Error ? err.message : "加载失败"));
  }, [token]);

  useEffect(() => { loadSessions(); }, [loadSessions]);
  useEffect(() => {
    setDetail(null);
    setError(null);
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  // Real-time: new messages land in the open thread; any session change
  // refreshes the list (and the open session's header).
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource(`${API_BASE}/staff/live/stream?access_token=${encodeURIComponent(token)}`);
    source.addEventListener("message", (event) => {
      const message = JSON.parse((event as MessageEvent).data) as LiveMessage;
      setDetail((current) => current && current.handoff.handoff_id === message.handoff_id && !current.messages.some((m) => m.message_id === message.message_id)
        ? { ...current, messages: [...current.messages, message] }
        : current);
      loadSessions();
    });
    source.addEventListener("handoff", (event) => {
      const handoff = JSON.parse((event as MessageEvent).data) as Handoff;
      setDetail((current) => current && current.handoff.handoff_id === handoff.handoff_id ? { ...current, handoff: { ...current.handoff, ...handoff } } : current);
      loadSessions();
    });
    return () => source.close();
  }, [token, loadSessions]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [detail?.messages.length]);

  async function act(path: string, body?: object) {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      await staffFetch(token, `/live/${detail.handoff.handoff_id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      loadDetail(detail.handoff.handoff_id);
      loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || !detail) return;
    setBusy(true);
    try {
      const message = await staffFetch<LiveMessage>(token, `/live/${detail.handoff.handoff_id}/messages`, { method: "POST", body: JSON.stringify({ content }) });
      setDraft("");
      setDetail((current) => current && !current.messages.some((m) => m.message_id === message.message_id) ? { ...current, messages: [...current.messages, message] } : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setBusy(false);
    }
  }

  const handoff = detail?.handoff;
  const mine = handoff?.workflow_status === "Active" && handoff.staff_id === identity?.staff_id;

  return (
    <div className="master-detail live-chat">
      <div className="master-detail__list">
        {sessions && sessions.length === 0 && (
          <div className="staff-empty-state"><MessagesSquare size={22} /><b>暂无人工会话</b><small>顾客在聊天中要求转人工，或你从工单「联系顾客」后会出现在这里</small></div>
        )}
        {(sessions ?? []).map((session) => (
          <button key={session.handoff_id} className={`queue-card ${selectedId === session.handoff_id ? "queue-card--active" : ""}`} onClick={() => select(session.handoff_id)}>
            {session.product ? <ProductImage product={session.product} size="xs" /> : <span className="queue-card__thumb"><UserRound size={16} /></span>}
            <span className="queue-card__body">
              <span className="queue-card__top">
                <b>{session.customer_id}</b>
                <span className={`pill pill--sm ${session.workflow_status === "Waiting" ? "pill--orange" : session.workflow_status === "Active" ? "pill--green" : "pill--gray"}`}>
                  {statusLabels[session.workflow_status]}{session.workflow_status === "Active" && session.staff_id ? ` · ${session.staff_id}` : ""}
                </span>
              </span>
              <span className="queue-card__title">{session.last_message ?? session.reason}</span>
              <span className="queue-card__foot">
                <small>{session.order_id ? `订单 ${session.order_id}` : "未关联订单"}</small>
                {session.workflow_status !== "Ended" && <span className={`staff-age ${session.workflow_status === "Waiting" ? "staff-age--late" : ""}`}>{session.workflow_status === "Waiting" ? "已等待" : "进行"} {ageText(session.created_at)}</span>}
              </span>
            </span>
          </button>
        ))}
      </div>

      <div className="master-detail__detail live-chat__detail">
        {!selectedId && <div className="staff-empty-state"><Headset size={22} /><b>选择左侧会话</b><small>排队中的会话按等待时间排序</small></div>}
        {selectedId && !detail && !error && <div className="staff-empty-state"><Loader size={18} className="spin" />加载会话…</div>}
        {error && <div className="staff-warning">{error}</div>}
        {handoff && detail && (
          <>
            <header className="live-chat__head">
              {handoff.product ? <ProductImage product={handoff.product} size="sm" /> : <span className="avatar avatar--lg"><UserRound size={20} /></span>}
              <div>
                <h2>{handoff.customer_id}{handoff.product ? ` · ${handoff.product.title}` : ""}</h2>
                <small>转人工原因：{handoff.reason}</small>
                <small>
                  {handoff.order_id && <>订单 {handoff.order_id} · </>}
                  {detail.service_request && <>售后单 {detail.service_request.request_id}（{detail.service_request.request_reason}） · </>}
                  发起于 {formatDateTime(handoff.created_at)}
                </small>
              </div>
              <div className="live-chat__actions">
                {handoff.workflow_status === "Waiting" && writable && <button className="btn btn--primary btn--sm" disabled={busy} onClick={() => void act("accept")}><CheckCircle2 size={14} />接入会话</button>}
                {mine && <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void act("end")}><XCircle size={14} />结束会话</button>}
                {handoff.workflow_status === "Active" && !mine && <span className="pill pill--green">{handoff.staff_id} 服务中</span>}
                {handoff.workflow_status === "Ended" && <span className="pill pill--gray">已结束 {handoff.end_reason ? `· ${handoff.end_reason}` : ""}</span>}
              </div>
            </header>

            {detail.ai_transcript.length > 0 && (
              <section className="live-transcript">
                <button className="link-btn" onClick={() => setShowTranscript((value) => !value)}><Bot size={13} />{showTranscript ? "收起" : "展开"}转人工前与智能助手的对话（{detail.ai_transcript.length} 条）</button>
                {showTranscript && (
                  <ol>
                    {detail.ai_transcript.map((item, index) => (
                      <li key={index} className={item.role === "user" ? "is-customer" : ""}><b>{item.role === "user" ? "顾客" : item.role === "summary" ? "摘要" : "智能助手"}</b>{item.content}</li>
                    ))}
                  </ol>
                )}
              </section>
            )}

            <div className="live-thread" ref={threadRef}>
              {detail.messages.map((message) => (
                <div key={message.message_id} className={`live-message live-message--${message.sender}`}>
                  {message.sender === "system" ? (
                    <span className="live-message__system"><Zap size={11} />{message.content}</span>
                  ) : (
                    <>
                      <span className="live-message__meta">{message.sender === "staff" ? message.sender_id : handoff.customer_id} · {formatDateTime(message.created_at)}</span>
                      <div className="live-message__bubble">{message.content}</div>
                    </>
                  )}
                </div>
              ))}
            </div>

            {mine ? (
              <form className="live-composer" onSubmit={(event) => void send(event)}>
                <div className="note-templates">
                  {QUICK_REPLIES.map((reply) => <button type="button" key={reply} onClick={() => setDraft(reply)}>{reply}</button>)}
                </div>
                <div className="live-composer__row">
                  <textarea
                    className="staff-textarea"
                    rows={2}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }}
                    placeholder="回复顾客，Enter 发送，Shift + Enter 换行"
                    maxLength={2000}
                  />
                  <button className="btn btn--primary" type="submit" disabled={busy || !draft.trim()}><Send size={15} />发送</button>
                </div>
              </form>
            ) : handoff.workflow_status === "Waiting" ? (
              <p className="muted live-chat__hint">接入后才能回复顾客；接入时会自动通知顾客。</p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
