import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock,
  FileImage,
  Loader,
  MessagesSquare,
  Package,
  ReceiptText,
  Truck,
  UserRound,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { API_BASE, yuan } from "../shop/api";
import { ProductImage } from "../shop/ProductImage";
import {
  agreementLabels,
  aiAgreement,
  AiRecommendation,
  actionStatusLabels,
  actionTypeLabels,
  decisionLabels,
  evidenceStatusLabels,
  formatDateTime,
  HUMAN_QUEUE_STATUSES,
  returnStatusLabels,
  StaffDetail,
  staffFetch,
  StaffIdentity,
  vipLabels,
} from "./api";
import { ImageLightbox, StatusPill } from "./ui";

const NOTE_TEMPLATES: Record<string, string[]> = {
  Approve: ["已核实订单、物流与证据，符合售后政策，同意退款。", "人工复核后风险可控，同意按实付金额退款。"],
  PartialApprove: ["经核实仅部分商品存在问题，按实际问题数量部分退款。"],
  Reject: ["提交的材料无法证明所述问题，申请关闭；客户可补充有效证据后重新申请。", "已超过售后时效且不属于质量问题，申请关闭。"],
  NoActionRequired: ["经核实订单状态正常，无需进一步处理。", "客户问题已通过其他方式解决，关闭申请。"],
};

type Props = {
  requestId: string;
  token: string;
  identity: StaffIdentity | null;
  onChanged: (message: string) => void;
  onOpenRequest?: (requestId: string) => void;
  onOpenLive?: (handoffId: string) => void;
};

export function RequestDetail({ requestId, token, identity, onChanged, onOpenRequest, onOpenLive }: Props) {
  const [detail, setDetail] = useState<StaffDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [lightbox, setLightbox] = useState<{ src: string; title: string } | null>(null);
  const [contactError, setContactError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    staffFetch<StaffDetail>(token, `/service-requests/${encodeURIComponent(requestId)}`)
      .then((data) => alive && setDetail(data))
      .catch((err) => alive && setError(err instanceof Error ? err.message : "加载失败"));
    return () => { alive = false; };
  }, [requestId, token, reloadKey]);

  if (error) return <div className="staff-empty-state"><AlertTriangle size={20} />{error}</div>;
  if (!detail || detail.service_request.request_id !== requestId) {
    return <div className="staff-empty-state"><Loader size={18} className="spin" />加载工单详情…</div>;
  }

  const request = detail.service_request;
  const targetIds = new Set(detail.request_items.map((item) => item.order_item_id));
  const canWrite = Boolean(identity?.roles.some((role) => role === "reviewer" || role === "admin"));
  const actionable = HUMAN_QUEUE_STATUSES.includes(request.workflow_status);
  const order = detail.order ?? {};

  async function contactCustomer() {
    setContactError(null);
    try {
      const handoff = await staffFetch<{ handoff_id: string }>(token, `/live/start-from-request/${encodeURIComponent(request.request_id)}`, {
        method: "POST",
        body: JSON.stringify({ message: `您好，我是人工客服，关于您的售后申请 ${request.request_id}（${request.request_reason}）想跟您确认一些情况。` }),
      });
      onOpenLive?.(handoff.handoff_id);
    } catch (err) {
      setContactError(err instanceof Error ? err.message : "无法发起会话");
    }
  }

  return (
    <div className="request-detail">
      <header className="request-detail__head">
        <ProductImage product={request.product} size="sm" />
        <div>
          <span className="request-detail__ids">售后单 {request.request_id} · 订单 {request.order_id} · 客户 {request.customer_id}</span>
          <h2 title={request.product?.title ?? undefined}>{request.product?.title ?? request.order_id}</h2>
          <div className="request-detail__tags">
            <StatusPill status={request.workflow_status} />
            <span className="pill pill--gray">{request.request_reason}</span>
            {request.product?.tags.includes("V2 导入") && <span className="pill pill--blue" title="Olist + Amazon 导入批次的真实订单数据">V2 导入</span>}
            <span className="pill pill--gray">申请 {request.requested_quantity} 件</span>
            <span className="muted">提交于 {formatDateTime(request.created_at ?? request.request_date)}</span>
            {canWrite && onOpenLive && request.workflow_status !== "Closed" && (
              <button className="btn btn--ghost btn--sm request-detail__contact" onClick={() => void contactCustomer()}><MessagesSquare size={13} />联系顾客</button>
            )}
          </div>
          {contactError && <div className="staff-warning">{contactError}</div>}
        </div>
      </header>

      {(request.reasons.length > 0 || request.rule_hits.length > 0 || request.required_actions.length > 0 || request.ai_recommendation) && (
        <section className="staff-section staff-section--ai">
          <h3><Bot size={16} />智能客服的判断</h3>
          {request.reasons.length > 0 && (
            <div className="ai-block"><b>{actionable ? "转人工 / 复核原因" : "判断依据"}</b><ul>{request.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>
          )}
          {request.required_actions.length > 0 && (
            <div className="ai-block"><b>{actionable ? "建议核查步骤" : "后续动作"}</b><ul>{request.required_actions.map((item) => <li key={item}>{actionTypeLabels[item] ?? item}</li>)}</ul></div>
          )}
          {request.ai_recommendation && <AiSuggestion suggestion={request.ai_recommendation} />}
          <div className="ai-meta">
            {request.rule_hits.length > 0 && <span>命中规则 {request.rule_hits.map((rule) => <code key={rule}>{rule}</code>)}</span>}
            {request.decision_confidence != null && <span>置信度 <b>{Math.round(request.decision_confidence * 100)}%</b></span>}
          </div>
        </section>
      )}

      {request.workflow_status === "Closed" || request.resolved_at ? (
        <section className="staff-section staff-section--result">
          <h3><CheckCircle2 size={16} />处理结果</h3>
          <dl className="kv">
            <dt>结论</dt><dd>{request.decision ? decisionLabels[request.decision] ?? request.decision : "—"}</dd>
            <dt>批准</dt><dd>{request.approved_quantity ?? "—"} 件 · {request.approved_amount != null ? yuan(request.approved_amount) : "—"}</dd>
            <dt>处理人</dt><dd>{request.resolved_by ?? "智能客服自动裁决"}</dd>
            {request.resolved_by && request.ai_recommendation?.decision && (() => {
              const outcome = aiAgreement(request.ai_recommendation, request.decision, request.approved_amount);
              return <><dt>对比 AI</dt><dd><span className={`pill pill--sm ${outcome === "agree" ? "pill--green" : "pill--orange"}`}>{agreementLabels[outcome]}</span></dd></>;
            })()}
            {request.resolved_at && <><dt>时间</dt><dd>{formatDateTime(request.resolved_at)}</dd></>}
            {request.resolution_notes && <><dt>依据</dt><dd>{request.resolution_notes}</dd></>}
          </dl>
        </section>
      ) : null}

      <div className="staff-detail-grid">
        <section className="staff-section">
          <h3><ReceiptText size={16} />订单与商品</h3>
          <dl className="kv">
            <dt>实付金额</dt><dd>{yuan(Number(order.amount ?? 0))}</dd>
            <dt>下单</dt><dd>{String(order.order_date ?? "—")}</dd>
            <dt>发货</dt><dd>{String(order.shipped_date ?? "未发货")}</dd>
            <dt>签收</dt><dd>{String(order.delivered_date ?? "未签收")}</dd>
          </dl>
          <div className="staff-items">
            {detail.order_items.map((item) => {
              const target = targetIds.has(item.order_item_id);
              const mismatch = item.shipped_product_id && item.shipped_product_id !== item.product_id;
              const short = item.shipped_quantity != null && item.shipped_quantity < item.quantity;
              return (
                <div key={item.order_item_id} className={`staff-item ${target ? "staff-item--target" : ""}`}>
                  <div><b>{item.sku_name}</b><small>{item.product_id} · {item.sku_id}</small></div>
                  <span>{yuan(item.unit_price)} × {item.quantity}</span>
                  <span>实付 {yuan(item.paid_amount)}</span>
                  <span className={mismatch || short ? "text-warn" : "muted"}>
                    {item.shipped_quantity == null ? (order.shipped_date ? "出库数量未记录" : "未出库") : mismatch ? `实发 ${item.shipped_product_id}` : short ? `实发 ${item.shipped_quantity} 件` : `已出库 ${item.shipped_quantity} 件`}
                  </span>
                  {target && <em className="staff-item__flag">本次售后</em>}
                </div>
              );
            })}
          </div>
        </section>

        <section className="staff-section">
          <h3><UserRound size={16} />客户画像</h3>
          {detail.customer ? (
            <dl className="kv">
              <dt>会员等级</dt><dd>{vipLabels[detail.customer.vip_level] ?? detail.customer.vip_level}</dd>
              <dt>注册日期</dt><dd>{detail.customer.register_date ?? "—"}</dd>
              <dt>历史退款</dt><dd className={detail.customer.refund_history_count >= 10 ? "text-warn" : ""}>{detail.customer.refund_history_count} 次</dd>
              {detail.customer.canceled_order_count != null && <><dt>取消订单</dt><dd>{detail.customer.canceled_order_count} 次</dd></>}
            </dl>
          ) : <p className="muted">无客户资料</p>}
          <h4>其他售后记录（{detail.customer_requests.length}）</h4>
          {detail.customer_requests.length === 0 && <p className="muted">无</p>}
          <ul className="staff-history-mini">
            {detail.customer_requests.map((item) => (
              <li key={item.request_id}>
                <button className="link-btn" onClick={() => onOpenRequest?.(item.request_id)} disabled={!onOpenRequest}>{item.request_id}</button>
                <span>{item.request_reason}</span>
                <StatusPill status={item.workflow_status} decision={item.decision} small />
              </li>
            ))}
          </ul>
        </section>
      </div>

      {(detail.logistics.length > 0 || detail.tracking) && (
        <section className="staff-section">
          <h3><Truck size={16} />物流</h3>
          {detail.logistics.map((event) => (
            <div key={`${event.carrier}-${event.exception_recorded_date}`} className={`logistics-alert ${event.resolved ? "logistics-alert--resolved" : ""}`}>
              <AlertTriangle size={15} />
              <span><b>{event.carrier} · {event.exception_type}</b><small>{event.exception_recorded_date} · {event.resolved ? "已处理" : "未处理"}</small></span>
            </div>
          ))}
          {detail.tracking && (
            <>
              <div className="tracking-head"><span>{detail.tracking.carrier}</span><span>运单号 <code>{detail.tracking.tracking_number}</code></span><span className="pill pill--orange">{detail.tracking.current_status}</span></div>
              <ol className="tracking-timeline">
                {[...detail.tracking.checkpoints].reverse().map((checkpoint, index) => (
                  <li key={`${checkpoint.time}-${index}`} className={index === 0 ? "latest" : ""}><i /><div><b>{checkpoint.description}</b><small>{checkpoint.time} · {checkpoint.location}</small></div></li>
                ))}
              </ol>
            </>
          )}
        </section>
      )}

      {detail.evidence.length > 0 && (
        <section className="staff-section">
          <h3><FileImage size={16} />证据记录（{detail.evidence.length} 轮）</h3>
          <div className="staff-evidence-strip">
            {detail.evidence.map((item, index) => (
              <figure key={item.evidence_id} className={`staff-evidence-thumb staff-evidence-thumb--${item.verification_status.toLowerCase()}`}>
                {item.media_type.startsWith("image/") && item.file_url ? (
                  <button onClick={() => setLightbox({ src: `${API_BASE}${item.file_url}`, title: `第 ${index + 1} 轮 · ${item.evidence_id}` })}>
                    <img src={`${API_BASE}${item.file_url}`} alt={item.original_filename} />
                  </button>
                ) : <span className="staff-evidence-thumb__placeholder"><FileImage size={18} /></span>}
                <figcaption>
                  <b>第 {index + 1} 轮 · {evidenceStatusLabels[item.verification_status] ?? item.verification_status}</b>
                  {item.customer_description && <p>“{item.customer_description}”</p>}
                  <small>{item.reviewed_by ? `${item.reviewed_by} 审核` : "待审核"}{item.review_notes ? ` · ${item.review_notes}` : ""}</small>
                </figcaption>
              </figure>
            ))}
          </div>
          {detail.evidence.filter((item) => item.verification_status === "Rejected").length >= 2 && (
            <div className="staff-warning"><AlertTriangle size={14} />已达到两轮补证上限，请做最终人工判断，不要仅凭次数关闭。</div>
          )}
        </section>
      )}

      {(detail.return_order || detail.actions.length > 0) && (
        <section className="staff-section">
          <h3><Package size={16} />退货与履约</h3>
          {detail.return_order && (
            <div className="staff-line">
              <b>退货单 {detail.return_order.return_order_id}</b>
              <span className="pill pill--orange">{returnStatusLabels[detail.return_order.workflow_status] ?? detail.return_order.workflow_status}</span>
              <span className="muted">{detail.return_order.return_carrier ? `${detail.return_order.return_carrier} ${detail.return_order.return_tracking_number}` : "客户尚未填写寄回物流"}</span>
            </div>
          )}
          {detail.actions.map((action) => (
            <div key={action.action_id} className="staff-line">
              <b>{actionTypeLabels[action.action_type] ?? action.action_type}</b>
              <span className={`pill ${action.workflow_status === "Succeeded" ? "pill--green" : action.workflow_status === "Failed" ? "pill--red" : "pill--blue"}`}>{actionStatusLabels[action.workflow_status] ?? action.workflow_status}</span>
              <span className="muted">{action.external_transaction_id ? `流水号 ${action.external_transaction_id}` : `尝试 ${action.attempts}/${action.max_attempts}`}</span>
            </div>
          ))}
        </section>
      )}

      {actionable && (
        <ResolvePanel
          detail={detail}
          token={token}
          identity={identity}
          canWrite={canWrite}
          onClaimed={() => setReloadKey((key) => key + 1)}
          onResolved={(message) => onChanged(message)}
        />
      )}

      {lightbox && <ImageLightbox src={lightbox.src} title={lightbox.title} onClose={() => setLightbox(null)} />}
    </div>
  );
}

function ResolvePanel({ detail, token, identity, canWrite, onClaimed, onResolved }: {
  detail: StaffDetail;
  token: string;
  identity: StaffIdentity | null;
  canWrite: boolean;
  onClaimed: () => void;
  onResolved: (message: string) => void;
}) {
  const request = detail.service_request;
  const [decision, setDecision] = useState("");
  const [quantity, setQuantity] = useState("");
  const [amount, setAmount] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const now = useNow();

  const target = useMemo(() => {
    const requestItem = detail.request_items[0];
    return detail.order_items.find((item) => item.order_item_id === requestItem?.order_item_id) ?? detail.order_items[0];
  }, [detail]);
  const unitPaid = target ? target.paid_amount / target.quantity : 0;
  const maxAmount = target ? target.paid_amount : Number(detail.order?.amount ?? 0);

  const leaseLeft = request.lease_expires_at ? Math.floor((new Date(request.lease_expires_at).getTime() - now) / 1000) : 0;
  const leaseActive = Boolean(request.assigned_to) && leaseLeft > 0;
  const mine = leaseActive && request.assigned_to === identity?.staff_id;
  const lockedByOther = leaseActive && !mine;

  function chooseDecision(next: string) {
    setDecision(next);
    if (next === "Approve") {
      setQuantity(String(request.requested_quantity));
      setAmount((unitPaid * request.requested_quantity).toFixed(2));
    } else if (next === "PartialApprove") {
      setQuantity("");
      setAmount("");
    } else {
      setQuantity("");
      setAmount("");
    }
  }

  function adoptSuggestion(suggestion: AiRecommendation) {
    if (!suggestion.decision) return;
    setDecision(suggestion.decision);
    setQuantity(suggestion.approved_quantity != null ? String(suggestion.approved_quantity) : "");
    setAmount(suggestion.approved_amount != null ? Number(suggestion.approved_amount).toFixed(2) : "");
    setNotes(`人工复核后采纳智能客服建议（规则 ${suggestion.matched_rule_id ?? "—"}）：${suggestion.reasons.join("；") || decisionLabels[suggestion.decision]}`);
  }

  function onQuantityChange(value: string) {
    setQuantity(value);
    if (decision === "PartialApprove" && value) setAmount((unitPaid * Number(value)).toFixed(2));
  }

  async function claim() {
    setBusy(true);
    setMessage(null);
    try {
      await staffFetch(token, `/service-requests/${request.request_id}/claim`, { method: "POST", body: JSON.stringify({ lease_minutes: 15 }) });
      onClaimed();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "领取失败");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!decision) return setMessage("请选择处理结论。");
    const needsNotes = decision === "Reject" || decision === "NoActionRequired";
    if (needsNotes && !notes.trim()) return setMessage("拒绝或关闭申请时必须填写处理依据，客户会看到这段说明。");
    if ((decision === "Approve" || decision === "PartialApprove") && (!quantity || !amount)) return setMessage("请填写批准数量和金额。");
    if (amount && Number(amount) > maxAmount + 0.001) return setMessage(`批准金额不能超过该商品实付 ${yuan(maxAmount)}。`);
    if (quantity && Number(quantity) > request.requested_quantity) return setMessage(`批准数量不能超过申请数量 ${request.requested_quantity}。`);
    setBusy(true);
    setMessage(null);
    try {
      await staffFetch(token, `/service-requests/${request.request_id}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          decision,
          approved_quantity: quantity ? Number(quantity) : null,
          approved_amount: amount ? Number(amount) : null,
          expected_version: request.row_version,
          resolution_notes: notes.trim() || null,
        }),
      });
      onResolved(`工单 ${request.request_id} 已处理：${decisionLabels[decision]}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="staff-section staff-section--resolve">
      <h3><CheckCircle2 size={16} />人工处理</h3>
      <div className="claim-bar">
        {mine ? (
          <span className="claim-bar__mine"><Clock size={14} />已由你领取，剩余 <b>{formatCountdown(leaseLeft)}</b></span>
        ) : lockedByOther ? (
          <span className="claim-bar__locked"><Clock size={14} />{request.assigned_to} 正在处理（剩余 {formatCountdown(leaseLeft)}）</span>
        ) : (
          <span className="muted">领取后 15 分钟内提交结论，期间其他客服无法处理该工单。</span>
        )}
        {canWrite && !lockedByOther && (
          <button className="btn btn--outline btn--sm" onClick={() => void claim()} disabled={busy}>{mine ? "续期 15 分钟" : "领取工单"}</button>
        )}
      </div>
      {!canWrite && <p className="muted">当前账号为只读角色，不能处理工单。</p>}
      {canWrite && (
        <form className="resolve-form" onSubmit={submit}>
          <fieldset disabled={!mine || busy}>
            {request.ai_recommendation?.decision && (
              <button type="button" className="btn btn--outline btn--sm adopt-ai" onClick={() => adoptSuggestion(request.ai_recommendation!)}>
                <Bot size={14} />采纳 AI 建议：{decisionLabels[request.ai_recommendation.decision] ?? request.ai_recommendation.decision}
                {request.ai_recommendation.approved_amount != null ? ` ${yuan(Number(request.ai_recommendation.approved_amount))}` : ""}
              </button>
            )}
            <div className="decision-options">
              {Object.entries(decisionLabels).map(([key, label]) => (
                <label key={key} className={`decision-option decision-option--${key} ${decision === key ? "active" : ""}`}>
                  <input type="radio" name="decision" value={key} checked={decision === key} onChange={() => chooseDecision(key)} />
                  {label}
                </label>
              ))}
            </div>
            {(decision === "Approve" || decision === "PartialApprove") && (
              <div className="resolve-form__row">
                <label>批准数量<input type="number" min={1} max={request.requested_quantity} value={quantity} onChange={(event) => onQuantityChange(event.target.value)} /></label>
                <label>批准金额（¥）<input type="number" min={0} step="0.01" max={maxAmount} value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
                <span className="muted">该商品实付 {yuan(maxAmount)}，单件 {yuan(unitPaid)}</span>
              </div>
            )}
            <label className="resolve-form__notes">
              处理依据{decision === "Reject" || decision === "NoActionRequired" ? "（必填，客户可见）" : "（客户可见）"}
              <textarea rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={2000} placeholder="记录判断依据，会写入审计记录并反馈给客户" />
            </label>
            {decision && (
              <div className="note-templates">
                {NOTE_TEMPLATES[decision].map((template) => <button type="button" key={template} onClick={() => setNotes(template)}>{template}</button>)}
              </div>
            )}
            <button className="btn btn--primary" type="submit">{busy ? <Loader size={14} className="spin" /> : <CheckCircle2 size={15} />}提交处理结果</button>
            {!mine && <span className="muted resolve-form__hint">请先领取工单</span>}
          </fieldset>
        </form>
      )}
      {message && <div className="staff-warning"><AlertTriangle size={14} />{message}</div>}
    </section>
  );
}

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function formatCountdown(seconds: number) {
  const safe = Math.max(0, seconds);
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function AiSuggestion({ suggestion }: { suggestion: AiRecommendation }) {
  if (!suggestion.decision) return null;
  return (
    <div className="ai-suggestion">
      <span className="ai-suggestion__label">AI 建议</span>
      <b>{decisionLabels[suggestion.decision] ?? suggestion.decision}</b>
      {suggestion.approved_quantity != null && <span>{suggestion.approved_quantity} 件</span>}
      {suggestion.approved_amount != null && <span>{yuan(Number(suggestion.approved_amount))}</span>}
      {suggestion.matched_rule_id && <code>{suggestion.matched_rule_id}</code>}
      {suggestion.decision_confidence != null && <span className="muted">置信度 {Math.round(suggestion.decision_confidence * 100)}%</span>}
      {suggestion.reasons.length > 0 && <p>{suggestion.reasons.join("；")}</p>}
      <small>规则引擎在不考虑转人工条件时会给出的结论，仅供参考；你的最终结论会与它对比，用于评估 AI 质量。</small>
    </div>
  );
}
