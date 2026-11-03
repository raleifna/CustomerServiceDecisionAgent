import { AlertTriangle, CheckCircle2, Copy, LoaderCircle, MapPin, PackageOpen, Truck } from "lucide-react";
import { FormEvent, useState } from "react";

import { apiFetch, RETURN_CARRIERS, ReturnOrder, yuan } from "./api";

const STEPS = [
  { key: "AwaitingShipment", label: "寄回商品" },
  { key: "InTransit", label: "退货运输中" },
  { key: "AwaitingInspection", label: "仓库验收" },
  { key: "Refund", label: "原路退款" },
];

function stepIndex(status: string) {
  if (status === "InspectionAccepted" || status === "InspectionRejected") return 3;
  return Math.max(0, STEPS.findIndex((step) => step.key === status));
}

type Props = {
  returnOrder: ReturnOrder;
  refundSucceeded?: boolean;
  compact?: boolean;
  onSubmitted?: (returnOrder: ReturnOrder) => void;
};

/** Return-shipment card for a no-reason return: where to send the goods, a
 * form for the courier + tracking number, then progress through warehouse
 * inspection to the refund. Shared by the after-sales page, the order
 * detail page and the chat. */
export function ReturnShipmentCard({ returnOrder, refundSucceeded, compact, onSubmitted }: Props) {
  const [carrier, setCarrier] = useState(RETURN_CARRIERS[0]);
  const [otherCarrier, setOtherCarrier] = useState("");
  const [tracking, setTracking] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const status = returnOrder.workflow_status;
  const current = stepIndex(status);
  const amount = Number(returnOrder.refundable_amount);
  const address = returnOrder.return_address;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const carrierName = carrier === "其他" ? otherCarrier.trim() : carrier;
    const trackingNumber = tracking.trim().toUpperCase();
    if (!carrierName) return setError("请填写快递公司。");
    if (!/^[A-Z0-9-]{6,40}$/.test(trackingNumber)) return setError("运单号应为 6–40 位字母或数字，请核对后重新填写。");
    setBusy(true);
    setError(null);
    try {
      const response = await apiFetch(`/service-requests/${returnOrder.request_id}/return-shipment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ carrier: carrierName, tracking_number: trackingNumber }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "提交失败，请稍后重试。");
      onSubmitted?.(body as ReturnOrder);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function copyAddress() {
    if (!address) return;
    try {
      await navigator.clipboard.writeText(`${address.recipient} ${address.phone} ${address.address}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard can be blocked; the address is still visible to copy by hand.
    }
  }

  return (
    <section className={`return-shipment ${compact ? "return-shipment--compact" : ""}`}>
      <header className="return-shipment__head">
        <span className="return-shipment__icon"><PackageOpen size={18} /></span>
        <div>
          <b>{headline(status, refundSucceeded)}</b>
          <small>退货单 {returnOrder.return_order_id} · 退回 {returnOrder.quantity} 件 · 验收通过后退款 <em>{yuan(amount)}</em></small>
        </div>
      </header>

      <ol className="return-shipment__steps">
        {STEPS.map((step, index) => (
          <li key={step.key} className={index < current || (index === 3 && (refundSucceeded || status === "InspectionAccepted")) ? "done" : index === current ? "current" : ""}>
            <i>{index < current ? <CheckCircle2 size={12} /> : index + 1}</i>{step.label}
          </li>
        ))}
      </ol>

      {status === "AwaitingShipment" && (
        <>
          {address && (
            <div className="return-shipment__address">
              <MapPin size={15} />
              <div><b>{address.recipient} · {address.phone}</b><span>{address.address}</span></div>
              <button type="button" className="link-btn" onClick={() => void copyAddress()}><Copy size={12} />{copied ? "已复制" : "复制"}</button>
            </div>
          )}
          <p className="return-shipment__tip">请保持商品完好、配件齐全并附原包装，寄出后在下方填写运单号，仓库收货验收后原路退款。</p>
          <form className="return-shipment__form" onSubmit={submit}>
            <label>
              快递公司
              <select value={carrier} onChange={(event) => setCarrier(event.target.value)}>
                {RETURN_CARRIERS.map((name) => <option key={name}>{name}</option>)}
                <option value="其他">其他</option>
              </select>
            </label>
            {carrier === "其他" && (
              <label>快递名称<input value={otherCarrier} onChange={(event) => setOtherCarrier(event.target.value)} maxLength={40} placeholder="填写快递公司" /></label>
            )}
            <label className="return-shipment__tracking">
              运单号
              <input value={tracking} onChange={(event) => setTracking(event.target.value)} maxLength={40} placeholder="例如 SF1234567890" autoComplete="off" />
            </label>
            <button className="btn btn--primary btn--sm" type="submit" disabled={busy || !tracking.trim()}>
              {busy ? <LoaderCircle size={14} className="spin" /> : <Truck size={14} />}提交寄回物流
            </button>
          </form>
          {error && <p className="return-shipment__error"><AlertTriangle size={13} />{error}</p>}
        </>
      )}

      {status !== "AwaitingShipment" && returnOrder.return_carrier && (
        <div className="return-shipment__facts">
          <span><Truck size={14} />{returnOrder.return_carrier} · {returnOrder.return_tracking_number}</span>
          {returnOrder.shipped_at && <span>寄出 {formatTime(returnOrder.shipped_at)}</span>}
          {returnOrder.received_at && <span>仓库签收 {formatTime(returnOrder.received_at)}</span>}
          {returnOrder.inspected_at && <span>验收 {formatTime(returnOrder.inspected_at)}</span>}
        </div>
      )}
      {status === "InspectionRejected" && (
        <p className="return-shipment__error"><AlertTriangle size={13} />验收未通过{returnOrder.inspection_notes ? `：${returnOrder.inspection_notes}` : ""}。如有疑问可联系客服。</p>
      )}
    </section>
  );
}

function headline(status: string, refundSucceeded?: boolean) {
  if (refundSucceeded) return "退款已完成";
  switch (status) {
    case "AwaitingShipment": return "退货申请已通过，请寄回商品";
    case "InTransit": return "退货运输中，等待仓库签收";
    case "AwaitingInspection": return "仓库已签收，正在验收";
    case "InspectionAccepted": return "验收通过，退款处理中";
    case "InspectionRejected": return "退货验收未通过";
    default: return "退货处理中";
  }
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
