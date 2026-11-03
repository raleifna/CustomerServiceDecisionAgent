import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  FileImage,
  Inbox,
  Loader,
  PackageCheck,
  RotateCcw,
  Search,
  Truck,
  Wrench,
  XCircle,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";

import { API_BASE, yuan } from "../shop/api";
import { ProductImage } from "../shop/ProductImage";
import {
  actionTypeLabels,
  ageHours,
  ageText,
  formatDateTime,
  requestStatusLabels,
  returnStatusLabels,
  StaffDetail,
  staffFetch,
  StaffIdentity,
  StaffQueue,
  StaffRequest,
  StaffReturnOrder,
} from "./api";
import { RequestDetail } from "./RequestDetail";
import { ImageLightbox, StatusPill } from "./ui";

export type PageProps = {
  token: string;
  identity: StaffIdentity | null;
  queue: StaffQueue | null;
  returns: StaffReturnOrder[] | null;
  selectedId: string | null;
  select: (id: string | null) => void;
  go: (section: string, id?: string) => void;
  done: (message: string) => void;
};

const canWrite = (identity: StaffIdentity | null) =>
  Boolean(identity?.roles.some((role) => role === "reviewer" || role === "admin"));

const DEFAULT_SLA_HOURS = 24;

// Escalations and anything waiting longest float to the top.
function priority(request: StaffRequest) {
  return (request.workflow_status === "EscalatedToHuman" ? 1000 : 0) + ageHours(request.created_at ?? request.request_date);
}

function Empty({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return <div className="staff-empty-state">{icon}<b>{title}</b>{hint && <small>{hint}</small>}</div>;
}

function MasterDetail({ list, detail }: { list: ReactNode; detail: ReactNode }) {
  return (
    <div className="master-detail">
      <div className="master-detail__list">{list}</div>
      <div className="master-detail__detail">{detail}</div>
    </div>
  );
}

/* ---------- overview ---------- */

export function OverviewPage({ queue, returns, go, identity }: PageProps) {
  const requests = queue?.service_requests ?? [];
  const pendingReturns = (returns ?? []).filter((item) => item.workflow_status !== "AwaitingShipment");
  const stats = [
    { section: "queue", label: "待人工裁决", value: requests.length, icon: ClipboardList, tone: "brand", hint: `${requests.filter((item) => ageHours(item.created_at ?? item.request_date) > (queue?.sla_hours ?? DEFAULT_SLA_HOURS)).length} 单超过 ${queue?.sla_hours ?? DEFAULT_SLA_HOURS} 小时处理时限` },
    { section: "evidence", label: "待审核证据", value: queue?.evidence.length ?? 0, icon: FileImage, tone: "violet", hint: "审核后系统自动重新裁决" },
    { section: "returns", label: "退货待处理", value: pendingReturns.length, icon: PackageCheck, tone: "blue", hint: `另有 ${(returns ?? []).length - pendingReturns.length} 单等待客户寄回` },
    { section: "actions", label: "履约异常", value: queue?.failed_actions.length ?? 0, icon: Wrench, tone: "red", hint: "退款 / 取消等执行失败" },
  ];
  const oldest = [...requests].sort((a, b) => priority(b) - priority(a)).slice(0, 6);

  return (
    <div className="staff-page">
      <div className="staff-welcome">
        <div><h1>你好，{identity?.staff_id ?? "客服"}</h1><p>智能客服处理不了的申请会进入这里，处理完成后结果会实时同步给顾客。</p></div>
      </div>
      <div className="stat-grid">
        {stats.map(({ section, label, value, icon: Icon, tone, hint }) => (
          <button key={section} className={`stat-card stat-card--${tone}`} onClick={() => go(section)}>
            <span className="stat-card__icon"><Icon size={20} /></span>
            <span className="stat-card__body"><small>{label}</small><b>{queue ? value : "—"}</b><em>{hint}</em></span>
            <ChevronRight size={16} />
          </button>
        ))}
      </div>
      <section className="panel">
        <h2><ClipboardList size={17} />优先处理</h2>
        {oldest.length === 0 && <Empty icon={<CheckCircle2 size={22} />} title="待人工裁决的工单都处理完了" />}
        <div className="staff-table">
          {oldest.map((request) => (
            <button key={request.request_id} className="staff-table__row" onClick={() => go("queue", request.request_id)}>
              <ProductImage product={request.product} size="xs" />
              <span className="staff-table__main"><b>{request.product?.title ?? request.order_id}</b><small>{request.request_id} · {request.customer_id} · {request.request_reason}</small></span>
              <StatusPill status={request.workflow_status} small />
              <span className="staff-table__amount">{request.order_amount != null ? yuan(request.order_amount) : "—"}</span>
              <span className={`staff-age ${ageHours(request.created_at ?? request.request_date) > (queue?.sla_hours ?? DEFAULT_SLA_HOURS) ? "staff-age--late" : ""}`}>等待 {ageText(request.created_at ?? request.request_date)}{ageHours(request.created_at ?? request.request_date) > (queue?.sla_hours ?? DEFAULT_SLA_HOURS) ? " · 已超时" : ""}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

/* ---------- human review queue ---------- */

export function QueuePage(props: PageProps) {
  const { queue, selectedId, select, token, identity, done, go } = props;
  const [filter, setFilter] = useState<"all" | "EscalatedToHuman" | "NeedsReflection">("all");
  const requests = useMemo(
    () => [...(queue?.service_requests ?? [])]
      .filter((item) => filter === "all" || item.workflow_status === filter)
      .sort((a, b) => priority(b) - priority(a)),
    [queue, filter],
  );
  return (
    <MasterDetail
      list={(
        <>
          <div className="segmented">
            {([["all", "全部"], ["EscalatedToHuman", "转人工"], ["NeedsReflection", "待复核"]] as const).map(([key, label]) => (
              <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>
            ))}
          </div>
          {queue && requests.length === 0 && <Empty icon={<Inbox size={22} />} title="暂无待人工处理的工单" />}
          {requests.map((request) => (
            <RequestCard key={request.request_id} request={request} active={selectedId === request.request_id} onClick={() => select(request.request_id)} staffId={identity?.staff_id} slaHours={queue?.sla_hours ?? DEFAULT_SLA_HOURS} />
          ))}
        </>
      )}
      detail={selectedId
        ? <RequestDetail key={selectedId} requestId={selectedId} token={token} identity={identity} onChanged={(message) => { select(null); done(message); }} onOpenRequest={select} onOpenLive={(handoffId) => go("live", handoffId)} />
        : <Empty icon={<ClipboardList size={22} />} title="选择左侧工单开始处理" hint="按优先级排序：强制转人工 > 等待时长" />}
    />
  );
}

function RequestCard({ request, active, onClick, staffId, slaHours }: { request: StaffRequest; active: boolean; onClick: () => void; staffId?: string; slaHours: number }) {
  const age = request.created_at ?? request.request_date;
  const leased = request.assigned_to && request.lease_expires_at && new Date(request.lease_expires_at).getTime() > Date.now();
  return (
    <button className={`queue-card ${active ? "queue-card--active" : ""}`} onClick={onClick}>
      <ProductImage product={request.product} size="xs" />
      <span className="queue-card__body">
        <span className="queue-card__top"><b>{request.request_reason}</b><StatusPill status={request.workflow_status} small /></span>
        <span className="queue-card__title">{request.product?.title ?? request.order_id}</span>
        <small>{request.request_id} · {request.customer_id}{request.order_amount != null ? ` · ${yuan(request.order_amount)}` : ""}</small>
        <span className="queue-card__foot">
          <span className={`staff-age ${ageHours(age) > slaHours ? "staff-age--late" : ""}`}>等待 {ageText(age)}{ageHours(age) > slaHours ? " · 超时" : ""}</span>
          {leased && <em className={request.assigned_to === staffId ? "queue-card__lease queue-card__lease--mine" : "queue-card__lease"}>{request.assigned_to === staffId ? "我处理中" : `${request.assigned_to} 处理中`}</em>}
        </span>
      </span>
    </button>
  );
}

/* ---------- evidence review ---------- */

const EVIDENCE_TEMPLATES = {
  Verified: ["图片清晰展示了商品问题，与客户描述一致。"],
  Rejected: [
    "图片模糊无法辨认，请在光线充足处重新拍摄清晰照片。",
    "图片未展示问题部位，请拍摄问题细节并附上商品整体照片。",
    "图片中的商品与订单不符，请上传本订单商品的照片。",
  ],
};

export function EvidencePage({ queue, selectedId, select, token, identity, done }: PageProps) {
  const evidence = queue?.evidence ?? [];
  const current = evidence.find((item) => item.evidence_id === selectedId) ?? null;
  return (
    <MasterDetail
      list={(
        <>
          {queue && evidence.length === 0 && <Empty icon={<FileImage size={22} />} title="暂无待审核证据" />}
          {evidence.map((item) => (
            <button key={item.evidence_id} className={`queue-card ${selectedId === item.evidence_id ? "queue-card--active" : ""}`} onClick={() => select(item.evidence_id)}>
              {item.media_type.startsWith("image/") && item.file_url
                ? <img className="queue-card__thumb" src={`${API_BASE}${item.file_url}`} alt="" />
                : <span className="queue-card__thumb"><FileImage size={16} /></span>}
              <span className="queue-card__body">
                <span className="queue-card__top"><b>{item.evidence_id}</b><span className="pill pill--violet pill--sm">待审核</span></span>
                <span className="queue-card__title">{item.customer_description || "客户未填写描述"}</span>
                <small>售后单 {item.request_id} · 提交于 {item.submitted_date}</small>
              </span>
            </button>
          ))}
        </>
      )}
      detail={current
        ? <EvidenceReview key={current.evidence_id} evidence={current} token={token} canWrite={canWrite(identity)} onDone={(message) => { select(null); done(message); }} />
        : <Empty icon={<FileImage size={22} />} title="选择左侧证据开始审核" hint="审核的是这份材料，不是直接裁决整个售后申请" />}
    />
  );
}

function EvidenceReview({ evidence, token, canWrite: writable, onDone }: {
  evidence: StaffQueue["evidence"][number];
  token: string;
  canWrite: boolean;
  onDone: (message: string) => void;
}) {
  const [context, setContext] = useState<StaffDetail | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);
  const imageUrl = evidence.file_url ? `${API_BASE}${evidence.file_url}` : "";

  useEffect(() => {
    staffFetch<StaffDetail>(token, `/service-requests/${encodeURIComponent(evidence.request_id)}`).then(setContext).catch(() => setContext(null));
  }, [evidence.request_id, token]);

  async function review(status: "Verified" | "Rejected") {
    if (status === "Rejected" && !notes.trim()) return setError("驳回时请填写原因，客户会根据它重新提交材料。");
    setBusy(true);
    setError(null);
    try {
      const body = await staffFetch<{ requeued_decision: boolean }>(token, `/evidence/${evidence.evidence_id}/review`, {
        method: "POST",
        body: JSON.stringify({ verification_status: status, review_notes: notes.trim() || null }),
      });
      onDone(status === "Verified"
        ? `证据 ${evidence.evidence_id} 已通过${body.requeued_decision ? "，系统已重新裁决该申请" : "，但自动重新裁决未完成，请到工单中检查"}`
        : `证据 ${evidence.evidence_id} 已驳回，客户会收到补充材料的通知`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "审核失败");
    } finally {
      setBusy(false);
    }
  }

  const request = context?.service_request;
  const previous = (context?.evidence ?? []).filter((item) => item.evidence_id !== evidence.evidence_id);
  return (
    <div className="evidence-review">
      <div className="evidence-review__media">
        {evidence.media_type.startsWith("image/") && imageUrl ? (
          <button className="evidence-review__image" onClick={() => setZoom(true)} title="点击放大"><img src={imageUrl} alt={evidence.original_filename} /></button>
        ) : <Empty icon={<FileImage size={22} />} title="该类型暂不支持预览" hint={evidence.media_type} />}
        <small className="muted">{evidence.original_filename} · 提交于 {evidence.submitted_date} · 点击图片放大</small>
      </div>
      <div className="evidence-review__side">
        <section className="staff-section">
          <h3>客户描述</h3>
          <p className="evidence-review__quote">{evidence.customer_description || "客户未填写描述"}</p>
        </section>
        <section className="staff-section">
          <h3>对应申请</h3>
          {request ? (
            <div className="evidence-review__request">
              <ProductImage product={request.product} size="sm" />
              <div>
                <b>{request.product?.title ?? request.order_id}</b>
                <small>{request.request_reason} · 申请 {request.requested_quantity} 件 · 订单 {request.order_id}</small>
                <small>客户 {request.customer_id} · 签收 {String(context?.order?.delivered_date ?? "未签收")}</small>
                <StatusPill status={request.workflow_status} small />
              </div>
            </div>
          ) : <p className="muted"><Loader size={14} className="spin" /> 加载申请信息…</p>}
          {previous.length > 0 && <p className="muted">此前已提交 {previous.length} 轮材料，{previous.filter((item) => item.verification_status === "Rejected").length} 轮被驳回。</p>}
        </section>
        {writable ? (
          <section className="staff-section">
            <h3>审核结论</h3>
            <textarea className="staff-textarea" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="驳回时必填：说明材料哪里不足、需要补充什么（客户可见）" maxLength={2000} />
            <div className="note-templates">
              {[...EVIDENCE_TEMPLATES.Rejected, ...EVIDENCE_TEMPLATES.Verified].map((template) => <button type="button" key={template} onClick={() => setNotes(template)}>{template}</button>)}
            </div>
            <div className="staff-actions">
              <button className="btn btn--primary" disabled={busy} onClick={() => void review("Verified")}><CheckCircle2 size={15} />审核通过</button>
              <button className="btn btn--danger" disabled={busy} onClick={() => void review("Rejected")}><XCircle size={15} />驳回，请客户补充</button>
            </div>
            {error && <div className="staff-warning"><AlertTriangle size={14} />{error}</div>}
          </section>
        ) : <p className="muted">当前账号为只读角色，不能审核证据。</p>}
      </div>
      {zoom && <ImageLightbox src={imageUrl} title={evidence.evidence_id} onClose={() => setZoom(false)} />}
    </div>
  );
}

/* ---------- return inspection ---------- */

const RETURN_STEPS = ["AwaitingShipment", "InTransit", "AwaitingInspection", "Done"];

export function ReturnsPage({ returns, token, identity, done, queue }: PageProps) {
  const transitAlertDays = queue?.return_transit_alert_days ?? 10;
  const [scope, setScope] = useState<"open" | "all">("open");
  const [all, setAll] = useState<StaffReturnOrder[] | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const writable = canWrite(identity);

  useEffect(() => {
    if (scope !== "all") return;
    staffFetch<StaffReturnOrder[]>(token, "/return-orders?scope=all").then(setAll).catch(() => setAll([]));
  }, [scope, token, returns]);

  const list = scope === "open" ? returns : all;

  async function act(item: StaffReturnOrder, path: string, body?: object, message?: string) {
    setBusyId(item.return_order_id);
    setError(null);
    try {
      await staffFetch(token, `/return-orders/${item.return_order_id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      done(message ?? "操作成功");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  function inspect(item: StaffReturnOrder, accepted: boolean) {
    const note = (notes[item.return_order_id] ?? "").trim();
    if (!accepted && !note) return setError("验收不通过时请填写原因，客户会看到这段说明。");
    void act(item, "inspect", { accepted, notes: note || null },
      accepted ? `退货单 ${item.return_order_id} 验收通过，已生成退款任务 ${yuan(item.refundable_amount)}` : `退货单 ${item.return_order_id} 验收不通过，申请已关闭`);
  }

  return (
    <div className="staff-page">
      <div className="segmented">
        <button className={scope === "open" ? "active" : ""} onClick={() => setScope("open")}>待处理</button>
        <button className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>全部退货单</button>
      </div>
      {error && <div className="staff-warning"><AlertTriangle size={14} />{error}</div>}
      {list && list.length === 0 && <Empty icon={<PackageCheck size={22} />} title="暂无退货单" hint="无理由退货等需要寄回的申请批准后，会在这里等待收货验收" />}
      <div className="return-list">
        {(list ?? []).map((item) => {
          const stepIndex = item.workflow_status.startsWith("Inspection") ? 3 : RETURN_STEPS.indexOf(item.workflow_status);
          return (
            <article key={item.return_order_id} className="panel return-card">
              <header>
                <ProductImage product={item.product} size="sm" />
                <div>
                  <b>{item.product?.title ?? item.order_id}</b>
                  <small>退货单 {item.return_order_id} · 售后单 {item.request_id} · 客户 {item.customer_id}</small>
                  <small>{item.request_reason ?? "售后"} · 退回 {item.quantity} 件 · 验收通过后退款 <b className="text-brand">{yuan(item.refundable_amount)}</b></small>
                </div>
                <span className={`pill ${item.workflow_status === "InspectionAccepted" ? "pill--green" : item.workflow_status === "InspectionRejected" || item.workflow_status === "Expired" ? "pill--gray" : "pill--orange"}`}>{returnStatusLabels[item.workflow_status] ?? item.workflow_status}</span>
                {item.workflow_status === "InTransit" && item.shipped_at && ageHours(item.shipped_at) > transitAlertDays * 24 && (
                  <span className="pill pill--red" title="寄出后长时间未签收，建议联系承运商核查">运输超时</span>
                )}
              </header>
              <ol className="return-steps">
                {["客户寄回", "运输中", "仓库收货", "验收完成"].map((label, index) => (
                  <li key={label} className={index < stepIndex || stepIndex === 3 ? "done" : index === stepIndex ? "current" : ""}>{label}</li>
                ))}
              </ol>
              <div className="return-card__meta">
                <span><Truck size={14} />{item.return_carrier ? `${item.return_carrier} · ${item.return_tracking_number}` : "客户尚未填写寄回物流"}</span>
                {item.shipped_at && <span>寄出 {formatDateTime(item.shipped_at)}</span>}
                {item.received_at && <span>收货 {formatDateTime(item.received_at)}</span>}
                {item.inspected_at && <span>验收 {formatDateTime(item.inspected_at)}{item.inspection_notes ? ` · ${item.inspection_notes}` : ""}</span>}
              </div>
              {writable && item.workflow_status === "InTransit" && (
                <div className="staff-actions">
                  <button className="btn btn--primary btn--sm" disabled={busyId === item.return_order_id} onClick={() => void act(item, "receive", undefined, `退货单 ${item.return_order_id} 已确认收货，等待验收`)}><PackageCheck size={14} />确认仓库已收货</button>
                </div>
              )}
              {writable && item.workflow_status === "AwaitingInspection" && (
                <div className="return-card__inspect">
                  <textarea className="staff-textarea" rows={2} placeholder="验收说明（不通过时必填，客户可见）" value={notes[item.return_order_id] ?? ""} onChange={(event) => setNotes((current) => ({ ...current, [item.return_order_id]: event.target.value }))} />
                  <div className="staff-actions">
                    <button className="btn btn--primary btn--sm" disabled={busyId === item.return_order_id} onClick={() => inspect(item, true)}><CheckCircle2 size={14} />验收通过并退款</button>
                    <button className="btn btn--danger btn--sm" disabled={busyId === item.return_order_id} onClick={() => inspect(item, false)}><XCircle size={14} />验收不通过</button>
                  </div>
                </div>
              )}
              {item.workflow_status === "AwaitingShipment" && <p className="muted return-card__hint">等待客户在「退款/售后」中填写寄回物流。</p>}
            </article>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- failed fulfillment actions ---------- */

export function ActionsPage({ queue, token, identity, done, go }: PageProps) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actions = queue?.failed_actions ?? [];

  async function retry(actionId: string) {
    setBusyId(actionId);
    setError(null);
    try {
      await staffFetch(token, `/actions/${actionId}/retry`, { method: "POST" });
      done(`任务 ${actionId} 已重新加入执行队列`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重试失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="staff-page">
      <p className="muted">裁决通过后，退款 / 取消订单等动作由履约 Worker 调用外部系统执行；多次失败的任务会停在这里，等待排查后手动重试。</p>
      {error && <div className="staff-warning"><AlertTriangle size={14} />{error}</div>}
      {queue && actions.length === 0 && <Empty icon={<CheckCircle2 size={22} />} title="没有失败的履约任务" />}
      <div className="return-list">
        {actions.map((action) => (
          <article key={action.action_id} className="panel action-card">
            <header>
              <span className="action-card__icon"><Wrench size={18} /></span>
              <div><b>{actionTypeLabels[action.action_type] ?? action.action_type}</b><small>{action.action_id} · 售后单 <button className="link-btn" onClick={() => go("history", action.request_id)}>{action.request_id}</button></small></div>
              <span className="pill pill--red">已尝试 {action.attempts}/{action.max_attempts} 次</span>
            </header>
            <pre className="action-card__error">{action.last_error || "未记录错误详情"}</pre>
            {canWrite(identity) && (
              <button className="btn btn--outline btn--sm" disabled={busyId === action.action_id} onClick={() => void retry(action.action_id)}><RotateCcw size={14} />重新加入执行队列</button>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

/* ---------- all cases ---------- */

export function HistoryPage({ token, identity, selectedId, select, done, go }: PageProps) {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<StaffRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams();
    if (status) params.set("workflow_status", status);
    if (query) params.set("q", query);
    setRows(null);
    staffFetch<StaffRequest[]>(token, `/service-requests?${params}`)
      .then(setRows)
      .catch((err) => { setRows([]); setError(err instanceof Error ? err.message : "加载失败"); });
  }, [status, query, token]);

  function submit(event: FormEvent) {
    event.preventDefault();
    setQuery(search.trim());
  }

  return (
    <div className={`history ${selectedId ? "history--with-drawer" : ""}`}>
      <div className="history__main">
        <form className="filter-toolbar" onSubmit={submit}>
          <label className="filter-toolbar__search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索售后单号、订单号或客户号" /></label>
          <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按状态筛选">
            <option value="">全部状态</option>
            {Object.entries(requestStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <button className="btn btn--primary btn--sm" type="submit">查询</button>
          <span className="muted">{rows ? `共 ${rows.length} 条` : ""}</span>
        </form>
        {error && <div className="staff-warning"><AlertTriangle size={14} />{error}</div>}
        <div className="panel history-table">
          <div className="history-table__row history-table__row--head"><span>商品 / 售后单</span><span>客户</span><span>原因</span><span>状态</span><span>批准金额</span><span>处理人</span><span>提交时间</span></div>
          {!rows && <div className="staff-empty-state"><Loader size={16} className="spin" />加载中…</div>}
          {rows?.length === 0 && <Empty icon={<Search size={22} />} title="没有符合条件的工单" />}
          {rows?.map((row) => (
            <button key={row.request_id} className={`history-table__row ${selectedId === row.request_id ? "active" : ""}`} onClick={() => select(row.request_id)}>
              <span className="history-table__product"><ProductImage product={row.product} size="xs" /><span><b>{row.product?.title ?? row.order_id}</b><small>{row.request_id}</small></span></span>
              <span>{row.customer_id}</span>
              <span>{row.request_reason}</span>
              <span><StatusPill status={row.workflow_status} decision={row.decision} small /></span>
              <span>{row.approved_amount != null ? yuan(row.approved_amount) : "—"}</span>
              <span>{row.resolved_by ?? (row.decision ? "系统" : "—")}</span>
              <span className="muted">{formatDateTime(row.created_at ?? row.request_date)}</span>
            </button>
          ))}
        </div>
      </div>
      {selectedId && (
        <aside className="history__drawer">
          <button className="icon-btn history__close" onClick={() => select(null)} aria-label="关闭详情"><XCircle size={18} /></button>
          <RequestDetail key={selectedId} requestId={selectedId} token={token} identity={identity} onChanged={(message) => { select(null); done(message); }} onOpenRequest={select} onOpenLive={(handoffId) => go("live", handoffId)} />
        </aside>
      )}
    </div>
  );
}
