import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FileImage,
  Loader,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

type StaffServiceRequest = {
  request_id: string;
  order_id: string;
  customer_id: string;
  request_type: string;
  request_reason: string;
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
};

type StaffEvidence = {
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

type StaffQueue = {
  service_requests: StaffServiceRequest[];
  evidence: StaffEvidence[];
  failed_actions: StaffAction[];
};

type StaffAction = {
  action_id: string;
  request_id: string;
  action_type: string;
  workflow_status: string;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
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

type RequestItem = {
  request_item_id: string;
  request_id: string;
  order_item_id: string;
  requested_quantity: number;
  approved_quantity: number | null;
  approved_amount: number | null;
};

type StaffServiceRequestDetail = {
  service_request: StaffServiceRequest;
  order: Record<string, unknown> | null;
  customer: Record<string, unknown> | null;
  product: Record<string, unknown> | null;
  logistics: Record<string, unknown>[];
  evidence: StaffEvidence[];
  order_items: OrderItem[];
  request_items: RequestItem[];
};

type Selection =
  | { kind: "service_request"; id: string }
  | { kind: "evidence"; id: string }
  | { kind: "action"; id: string }
  | null;

const workflowStatusLabels: Record<string, string> = {
  EscalatedToHuman: "强制人工升级",
  NeedsReflection: "复核 / 待更多证据",
};

function fieldValue(record: Record<string, unknown> | null, key: string): string {
  if (!record) return "—";
  const value = record[key];
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function StaffConsole() {
  const returnCustomerId = new URLSearchParams(window.location.search).get("returnCustomer");
  const customerReturnHref = returnCustomerId
    ? `/?resumeCustomer=${encodeURIComponent(returnCustomerId)}`
    : "/";
  const [queue, setQueue] = useState<StaffQueue | null>(null);
  const [isLoadingQueue, setIsLoadingQueue] = useState(true);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [detail, setDetail] = useState<StaffServiceRequestDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [staffName, setStaffName] = useState("");
  const [staffToken, setStaffToken] = useState(() => localStorage.getItem("staff_api_token") || "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [evidenceReviewNotes, setEvidenceReviewNotes] = useState("");

  const authHeaders = useCallback(() => ({ Authorization: `Bearer ${staffToken}` }), [staffToken]);

  const loadQueue = useCallback(() => {
    if (!staffToken) {
      setQueueError("请输入 Staff API Token。")
      setIsLoadingQueue(false);
      return;
    }
    setIsLoadingQueue(true);
    setQueueError(null);
    fetch(`${API_BASE}/staff/queue`, { headers: authHeaders() })
      .then((response) => {
        if (!response.ok) throw new Error(`加载队列失败 (${response.status})`);
        return response.json() as Promise<StaffQueue>;
      })
      .then(setQueue)
      .catch((error) => setQueueError(error instanceof Error ? error.message : "加载队列失败"))
      .finally(() => setIsLoadingQueue(false));
  }, [authHeaders, staffToken]);

  useEffect(() => {
    if (!staffToken) {
      setStaffName("");
      return;
    }
    localStorage.setItem("staff_api_token", staffToken);
    fetch(`${API_BASE}/staff/me`, { headers: authHeaders() })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("认证失败")))
      .then((identity) => setStaffName(identity.staff_id || ""))
      .catch(() => setStaffName(""));
  }, [authHeaders, staffToken]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(() => {
    if (selection?.kind !== "service_request") {
      setDetail(null);
      return;
    }
    setIsLoadingDetail(true);
    const controller = new AbortController();
    fetch(`${API_BASE}/staff/service-requests/${selection.id}`, {
      signal: controller.signal,
      headers: authHeaders(),
    })
      .then((response) => {
        if (!response.ok) throw new Error("加载详情失败");
        return response.json() as Promise<StaffServiceRequestDetail>;
      })
      .then(setDetail)
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setDetail(null);
      })
      .finally(() => setIsLoadingDetail(false));
    return () => controller.abort();
  }, [authHeaders, selection]);

  async function claimCurrentRequest() {
    if (!detail || isSubmitting) return;
    setIsSubmitting(true);
    setActionMessage(null);
    try {
      const response = await fetch(
        `${API_BASE}/staff/service-requests/${detail.service_request.request_id}/claim`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ lease_minutes: 15 }),
        },
      );
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail || `领取失败 (${response.status})`);
      setDetail((current) => current ? { ...current, service_request: body } : current);
      setActionMessage("工单已领取，15 分钟内可提交处理结果。");
      loadQueue();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "领取失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  function selectItem(next: Selection) {
    setSelection(next);
    setActionMessage(null);
    setEvidenceReviewNotes("");
  }

  async function resolveServiceRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || isSubmitting) return;
    const form = new FormData(event.currentTarget);
    const decision = String(form.get("decision"));
    const approvedQuantityRaw = form.get("approved_quantity");
    const approvedAmountRaw = form.get("approved_amount");
    const notes = String(form.get("resolution_notes") || "");

    setIsSubmitting(true);
    setActionMessage(null);
    try {
      const response = await fetch(
        `${API_BASE}/staff/service-requests/${detail.service_request.request_id}/resolve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({
            decision,
            approved_quantity: approvedQuantityRaw ? Number(approvedQuantityRaw) : null,
            approved_amount: approvedAmountRaw ? Number(approvedAmountRaw) : null,
            expected_version: detail.service_request.row_version,
            resolution_notes: notes || null,
          }),
        },
      );
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail || `处理失败 (${response.status})`);
      setActionMessage("已提交人工处理结果。");
      selectItem(null);
      loadQueue();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "处理失败，请重试。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function reviewEvidence(evidenceId: string, verificationStatus: "Verified" | "Rejected") {
    if (isSubmitting) return;
    const notes = evidenceReviewNotes.trim();
    if (verificationStatus === "Rejected" && !notes) {
      setActionMessage("驳回证据前请填写驳回原因，该原因会同步给客户。");
      return;
    }

    setIsSubmitting(true);
    setActionMessage(null);
    try {
      const response = await fetch(`${API_BASE}/staff/evidence/${evidenceId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          verification_status: verificationStatus,
          review_notes: notes || null,
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail || `审核失败 (${response.status})`);
      const successMessage =
        body?.requeued_decision
          ? "证据审核已提交，系统已自动重新尝试完成该申请的裁决。"
          : "证据审核已提交，但自动重新裁决未能完成，请检查该申请。";
      selectItem(null);
      setActionMessage(successMessage);
      loadQueue();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "审核失败，请重试。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function retryAction(actionId: string) {
    if (isSubmitting) return;
    setIsSubmitting(true);
    setActionMessage(null);
    try {
      const response = await fetch(`${API_BASE}/staff/actions/${actionId}/retry`, {
        method: "POST",
        headers: authHeaders(),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail || `重试失败 (${response.status})`);
      setActionMessage("履约任务已重新进入执行队列。");
      selectItem(null);
      loadQueue();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "重试失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  const requestCount = queue?.service_requests.length ?? 0;
  const evidenceCount = queue?.evidence.length ?? 0;
  const failedActionCount = queue?.failed_actions.length ?? 0;

  return (
    <div className="staff-shell">
      <header className="staff-topbar">
        <a className="staff-back" href={customerReturnHref}>
          <ArrowLeft size={15} /> {returnCustomerId ? `返回客户 ${returnCustomerId} 会话` : "返回客户选择页"}
        </a>
        <div className="staff-title">
          <ShieldAlert size={17} />
          <span>人工客服工作台</span>
        </div>
        <div className="staff-identity">
          <label>
            Staff Token
            <input
              type="password"
              value={staffToken}
              onChange={(event) => setStaffToken(event.target.value)}
              placeholder="输入访问令牌"
            />
          </label>
          <span>{staffName || "未认证"}</span>
          <button className="staff-refresh" onClick={loadQueue} disabled={isLoadingQueue}>
            <RefreshCw size={14} className={isLoadingQueue ? "spin" : ""} /> 刷新队列
          </button>
        </div>
      </header>

      <main className="staff-body">
        <aside className="staff-queue">
          <section>
            <h2>待人工处理的售后申请 <span className="staff-count">{requestCount}</span></h2>
            {queueError && <div className="staff-error"><AlertTriangle size={14} /> {queueError}</div>}
            {!queueError && requestCount === 0 && !isLoadingQueue && (
              <div className="staff-empty">目前没有需要人工处理的申请。</div>
            )}
            <ul>
              {queue?.service_requests.map((item) => (
                <li key={item.request_id}>
                  <button
                    className={
                      selection?.kind === "service_request" && selection.id === item.request_id
                        ? "staff-row staff-row--active"
                        : "staff-row"
                    }
                    onClick={() => selectItem({ kind: "service_request", id: item.request_id })}
                  >
                    <span className="staff-row__badge">
                      {workflowStatusLabels[item.workflow_status] || item.workflow_status}
                    </span>
                    <span className="staff-row__main">
                      <b>{item.request_id}</b>
                      <small>{item.order_id} · {item.customer_id} · {item.request_reason}</small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2>履约失败任务 <span className="staff-count">{failedActionCount}</span></h2>
            {failedActionCount === 0 && !isLoadingQueue && (
              <div className="staff-empty">目前没有需要恢复的履约任务。</div>
            )}
            <ul>
              {queue?.failed_actions.map((item) => (
                <li key={item.action_id}>
                  <button
                    className={
                      selection?.kind === "action" && selection.id === item.action_id
                        ? "staff-row staff-row--active"
                        : "staff-row"
                    }
                    onClick={() => selectItem({ kind: "action", id: item.action_id })}
                  >
                    <span className="staff-row__badge">执行失败</span>
                    <span className="staff-row__main">
                      <b>{item.action_id}</b>
                      <small>{item.request_id} · {item.action_type}</small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2>待审核证据 <span className="staff-count">{evidenceCount}</span></h2>
            {!queueError && evidenceCount === 0 && !isLoadingQueue && (
              <div className="staff-empty">目前没有待审核的证据。</div>
            )}
            <ul>
              {queue?.evidence.map((item) => (
                <li key={item.evidence_id}>
                  <button
                    className={
                      selection?.kind === "evidence" && selection.id === item.evidence_id
                        ? "staff-row staff-row--active"
                        : "staff-row"
                    }
                    onClick={() => selectItem({ kind: "evidence", id: item.evidence_id })}
                  >
                    <span className="staff-row__badge staff-row__badge--pending"><FileImage size={12} /> 待审核</span>
                    <span className="staff-row__main">
                      <b>{item.evidence_id}</b>
                      <small>{item.request_id} · {item.original_filename}</small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </aside>

        <section className="staff-detail">
          {actionMessage && <div className="staff-toast">{actionMessage}</div>}

          {!selection && <div className="staff-empty staff-empty--center">选择左侧一项开始处理。</div>}

          {selection?.kind === "evidence" && queue && (() => {
            const evidence = queue.evidence.find((item) => item.evidence_id === selection.id);
            if (!evidence) return <div className="staff-empty staff-empty--center">该证据已被处理。</div>;
            return (
              <div className="staff-card">
                <h3>{evidence.evidence_id}</h3>
                <p className="staff-meta">申请 {evidence.request_id} · 提交于 {evidence.submitted_date}</p>
                <div className="staff-customer-description">
                  <span>客户问题描述</span>
                  <p>{evidence.customer_description || "客户未填写描述"}</p>
                </div>
                {evidence.media_type.startsWith("image/") ? (
                  <img className="staff-evidence-image" src={`${API_BASE}${evidence.file_url}`} alt={evidence.original_filename} />
                ) : (
                  <div className="staff-empty">该证据类型暂不支持预览：{evidence.media_type}</div>
                )}
                <label className="staff-evidence-review-field">
                  <span>审核说明 / 驳回原因</span>
                  <textarea
                    rows={3}
                    value={evidenceReviewNotes}
                    onChange={(event) => setEvidenceReviewNotes(event.target.value)}
                    placeholder="驳回时必填：请说明材料哪里不清晰、不相关或无法证明问题，以及客户需要补充什么"
                    maxLength={2000}
                  />
                  <small>选择“驳回”后，这段原因会作为一条新的审核通知同步给客户。</small>
                </label>
                <div className="staff-actions">
                  <button
                    className="staff-btn staff-btn--approve"
                    disabled={isSubmitting}
                    onClick={() => reviewEvidence(evidence.evidence_id, "Verified")}
                  >
                    <CheckCircle2 size={14} /> 审核通过
                  </button>
                  <button
                    className="staff-btn staff-btn--reject"
                    disabled={isSubmitting}
                    onClick={() => reviewEvidence(evidence.evidence_id, "Rejected")}
                  >
                    <XCircle size={14} /> 驳回
                  </button>
                </div>
                <p className="staff-hint">这里审核的是证据，不是直接驳回整个售后申请。证据驳回后，申请会等待客户补充新材料。</p>
              </div>
            );
          })()}

          {selection?.kind === "action" && queue && (() => {
            const action = queue.failed_actions.find((item) => item.action_id === selection.id);
            if (!action) return <div className="staff-empty staff-empty--center">该任务已被处理。</div>;
            return (
              <div className="staff-card">
                <h3>{action.action_id}</h3>
                <p className="staff-meta">申请 {action.request_id} · {action.action_type}</p>
                <div className="staff-reasons">
                  <span className="staff-eyebrow">失败信息</span>
                  <p>{action.last_error || "未记录错误详情"}</p>
                  <p>已尝试 {action.attempts} / {action.max_attempts} 次</p>
                </div>
                <button
                  className="staff-btn staff-btn--approve"
                  disabled={isSubmitting}
                  onClick={() => retryAction(action.action_id)}
                >
                  重新加入执行队列
                </button>
              </div>
            );
          })()}

          {selection?.kind === "service_request" && (
            <>
              {isLoadingDetail && <div className="staff-empty staff-empty--center"><Loader size={16} className="spin" /> 加载详情中…</div>}
              {!isLoadingDetail && detail && (
                <div className="staff-card">
                  <h3>{detail.service_request.request_id}</h3>
                  <p className="staff-meta">
                    {detail.service_request.order_id} · {detail.service_request.customer_id} ·
                    {" "}{detail.service_request.request_type} / {detail.service_request.request_reason} ·
                    {" "}申请数量 {detail.service_request.requested_quantity}
                  </p>

                  <div className="staff-grid">
                    <div>
                      <span className="staff-eyebrow">订单事实</span>
                      <dl>
                        <dt>金额</dt><dd>¥{fieldValue(detail.order, "amount")}</dd>
                        <dt>支付状态</dt><dd>{fieldValue(detail.order, "payment_status")}</dd>
                        <dt>发货日期</dt><dd>{fieldValue(detail.order, "shipped_date")}</dd>
                        <dt>签收日期</dt><dd>{fieldValue(detail.order, "delivered_date")}</dd>
                      </dl>
                    </div>
                    <div>
                      <span className="staff-eyebrow">客户</span>
                      <dl>
                        <dt>会员等级</dt><dd>{fieldValue(detail.customer, "vip_level")}</dd>
                        <dt>历史退款</dt><dd>{fieldValue(detail.customer, "refund_history_count")} 次</dd>
                        <dt>注册日期</dt><dd>{fieldValue(detail.customer, "register_date")}</dd>
                      </dl>
                    </div>
                  </div>

                  <div className="staff-reasons">
                    <span className="staff-eyebrow">订单项 / 本次售后目标</span>
                    <div className="staff-order-items">
                      {detail.order_items.map((item) => {
                        const requestItem = detail.request_items.find(
                          (relation) => relation.order_item_id === item.order_item_id,
                        );
                        return (
                          <div
                            className={requestItem ? "staff-order-item staff-order-item--target" : "staff-order-item"}
                            key={item.order_item_id}
                          >
                            <div>
                              <b>{item.sku_name}</b>
                              <small>{item.product_id} · {item.sku_id}</small>
                            </div>
                            <span>成交价 ¥{item.unit_price.toFixed(2)} · 购买 {item.quantity} 件</span>
                            <span>{requestItem ? `本次申请 ${requestItem.requested_quantity} 件` : "未申请售后"}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {detail.service_request.reasons.length > 0 && (
                    <div className="staff-reasons">
                      <span className="staff-eyebrow">转人工 / 复核原因</span>
                      <ul>{detail.service_request.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                    </div>
                  )}
                  {detail.service_request.required_actions.length > 0 && (
                    <div className="staff-reasons">
                      <span className="staff-eyebrow">建议核查步骤</span>
                      <ul>{detail.service_request.required_actions.map((action) => <li key={action}>{action}</li>)}</ul>
                    </div>
                  )}

                  {detail.logistics.length > 0 && (
                    <div className="staff-reasons">
                      <span className="staff-eyebrow">物流异常</span>
                      <ul>
                        {detail.logistics.map((event, index) => (
                          <li key={index}>
                            {String(event.carrier)} · {String(event.exception_type)} ·
                            {" "}{event.resolved ? "已处理" : "尚未处理"}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {detail.evidence.length > 0 && (
                    <div className="staff-reasons">
                      <span className="staff-eyebrow">证据审核全记录</span>
                      <div className="staff-evidence-history">
                        {detail.evidence.map((item, index) => (
                          <div key={item.evidence_id}>
                            <b>第 {index + 1} 轮 · {item.evidence_id}</b>
                            <span>{item.original_filename} · {item.verification_status}</span>
                            <small>提交：{item.submitted_date} · 审核人：{item.reviewed_by || "待审核"}{item.reviewed_at ? ` · 审核时间：${new Date(item.reviewed_at).toLocaleString("zh-CN")}` : ""}</small>
                            {item.review_notes && <p>审核依据：{item.review_notes}</p>}
                          </div>
                        ))}
                      </div>
                      {detail.evidence.filter((item) => item.verification_status === "Rejected").length >= 2 && <div className="staff-limit-warning"><AlertTriangle size={14} /> 已达到两轮补证上限，应进行最终人工复核；不要仅凭次数自动关闭。</div>}
                    </div>
                  )}

                  {detail.service_request.assigned_to !== staffName && (
                    <button
                      className="staff-btn staff-btn--approve"
                      type="button"
                      disabled={isSubmitting || !staffName}
                      onClick={claimCurrentRequest}
                    >
                      领取工单
                    </button>
                  )}
                  <form className="staff-form" onSubmit={resolveServiceRequest}>
                    <span className="staff-eyebrow">人工处理结果</span>
                    <label>
                      结论
                      <select name="decision" defaultValue="">
                        <option value="" disabled>请选择处理结论</option>
                        <option value="Approve">批准</option>
                        <option value="PartialApprove">部分批准</option>
                        <option value="Reject">证据不足，关闭申请</option>
                        <option value="NoActionRequired">不符合政策，关闭申请</option>
                      </select>
                    </label>
                    <div className="staff-form-row">
                      <label>
                        批准数量
                        <input type="number" name="approved_quantity" min={0} placeholder="可留空" />
                      </label>
                      <label>
                        批准金额
                        <input type="number" name="approved_amount" min={0} step="0.01" placeholder="可留空" />
                      </label>
                    </div>
                    <label>
                      处理依据（关闭申请时必填）
                      <textarea name="resolution_notes" rows={3} placeholder="记录最终判断依据；该内容会进入完整操作记录并反馈给客户" />
                    </label>
                    <button
                      className="staff-btn staff-btn--approve"
                      type="submit"
                      disabled={isSubmitting || detail.service_request.assigned_to !== staffName}
                    >
                      {isSubmitting ? <Loader size={14} className="spin" /> : <CheckCircle2 size={14} />} 提交处理结果
                    </button>
                  </form>
                </div>
              )}
            </>
          )}
        </section>
      </main>
    </div>
  );
}

export default StaffConsole;
