import { ReceiptText, Store } from "lucide-react";
import { useMemo } from "react";

import { Customer, CustomerOrder, CustomerServiceRequestProgress, orderStatusLabels, requestWorkflowStatusLabels, yuan } from "../api";
import { EmptyState, Link, UserCenterLayout } from "../components";
import { useCustomerOrders, useFetch } from "../hooks";
import { ProductImage } from "../ProductImage";
import { navigate, useLocation } from "../router";

const TABS = [
  { key: "all", label: "全部订单", match: () => true },
  { key: "pending", label: "待发货", match: (order: CustomerOrder) => order.order_status === "PendingShipment" },
  { key: "shipping", label: "运输中", match: (order: CustomerOrder) => order.order_status === "Shipped" },
  { key: "delivered", label: "已签收", match: (order: CustomerOrder) => order.order_status === "Delivered" },
];

export function latestRequestByOrder(requests: CustomerServiceRequestProgress[] | null) {
  const map = new Map<string, CustomerServiceRequestProgress>();
  for (const request of requests ?? []) {
    const current = map.get(request.order_id);
    if (!current || request.request_date >= current.request_date) map.set(request.order_id, request);
  }
  return map;
}

// Return states first: an approved no-reason refund is "Approve" but still
// needs the customer to ship the goods back before any money moves.
const RETURN_BADGES: Record<string, { label: string; tone: string }> = {
  ReturnAwaitingShipment: { label: "待寄回商品", tone: "orange" },
  ReturnInTransit: { label: "退货运输中", tone: "orange" },
  ReturnAwaitingInspection: { label: "退货验收中", tone: "orange" },
  ExecutionPending: { label: "退款处理中", tone: "green" },
  Executing: { label: "退款处理中", tone: "green" },
};

export function requestBadge(request: CustomerServiceRequestProgress): { label: string; tone: string } {
  if (RETURN_BADGES[request.workflow_status]) return RETURN_BADGES[request.workflow_status];
  if (request.decision === "Approve") return { label: "售后已通过", tone: "green" };
  if (request.decision === "PartialApprove") return { label: "部分通过", tone: "green" };
  if (request.decision === "Reject") return { label: "售后未通过", tone: "gray" };
  if (request.decision === "NoActionRequired") return { label: "售后已关闭", tone: "gray" };
  return { label: `售后${requestWorkflowStatusLabels[request.workflow_status] ?? "处理中"}`, tone: "orange" };
}

export default function OrdersPage({ customerId, customer }: { customerId: string; customer: Customer | undefined }) {
  const { query } = useLocation();
  const tab = TABS.find((item) => item.key === query.get("tab")) ?? TABS[0];
  const orders = useCustomerOrders(customerId);
  const requests = useFetch<CustomerServiceRequestProgress[]>(`/customers/${customerId}/service-requests`);
  const requestByOrder = useMemo(() => latestRequestByOrder(requests.data), [requests.data]);
  const list = (orders.data ?? []).filter(tab.match);

  return (
    <UserCenterLayout customer={customer}>
      <div className="panel">
        <nav className="tabs">
          {TABS.map((item) => {
            const count = (orders.data ?? []).filter(item.match).length;
            return (
              <button key={item.key} className={item.key === tab.key ? "active" : ""} onClick={() => navigate(item.key === "all" ? "/orders" : `/orders?tab=${item.key}`, { replace: true })}>
                {item.label}{item.key !== "all" && count > 0 && <em>{count}</em>}
              </button>
            );
          })}
          <Link to="/after-sales" className="tabs__link">退款/售后</Link>
        </nav>

        {orders.loading && !orders.data && <div className="order-card order-card--skeleton" />}
        {orders.error && <EmptyState title="订单加载失败" hint={orders.error} />}
        {!orders.loading && list.length === 0 && !orders.error && (
          <EmptyState icon={<ReceiptText size={26} />} title="暂无相关订单" action={<Link to="/" className="btn btn--primary">去逛逛</Link>} />
        )}

        <div className="order-list">
          {list.map((order) => {
            const request = requestByOrder.get(order.order_id);
            const badge = request ? requestBadge(request) : null;
            return (
              <article key={order.order_id} className="order-card">
                <header>
                  <span>{order.order_date}</span>
                  <span>订单号：<Link to={`/orders/${order.order_id}`}>{order.order_id}</Link></span>
                  <span className="order-card__store"><Store size={13} />{order.product?.brand ?? "ShopCare 优选"}</span>
                  <span className={`status-text status-text--${order.order_status.toLowerCase()}`}>{orderStatusLabels[order.order_status]}</span>
                </header>
                <div className="order-card__body">
                  <Link to={`/product/${order.product_id}`} className="order-card__product">
                    <ProductImage product={order.product} size="sm" />
                    <div>
                      <b>{order.product?.title ?? order.product_id}</b>
                      <small>{order.product_category} · 数量 ×{order.quantity}</small>
                      {badge && <span className={`pill pill--${badge.tone}`}>{badge.label}</span>}
                    </div>
                  </Link>
                  <div className="order-card__amount">
                    <b>{yuan(order.amount)}</b>
                    <small>{order.payment_status === "Paid" ? "已支付" : order.payment_status}</small>
                  </div>
                  <div className="order-card__state">
                    <b>{orderStatusLabels[order.order_status]}</b>
                    <small>
                      {order.order_status === "Delivered" ? `签收于 ${order.delivered_date}`
                        : order.order_status === "Shipped" ? `预计 ${order.estimated_delivery_date ?? "—"} 送达`
                          : `承诺 ${order.promised_ship_date ?? "—"} 前发货`}
                    </small>
                    <Link to={`/orders/${order.order_id}`}>订单详情</Link>
                  </div>
                  <div className="order-card__actions">
                    {order.order_status === "PendingShipment" && <Link to={`/service?order=${order.order_id}&ask=${encodeURIComponent("这个订单什么时候发货？")}`} className="btn btn--outline btn--sm">催发货</Link>}
                    {order.order_status === "Shipped" && <Link to={`/orders/${order.order_id}#tracking`} className="btn btn--outline btn--sm">查看物流</Link>}
                    {request?.workflow_status === "ReturnAwaitingShipment" && (
                      <Link to={`/after-sales?focus=${request.request_id}`} className="btn btn--primary btn--sm">寄回商品</Link>
                    )}
                    {request
                      ? <Link to={`/after-sales?focus=${request.request_id}`} className="btn btn--ghost btn--sm">售后进度</Link>
                      : <Link to={`/service?order=${order.order_id}&ask=${encodeURIComponent("我想申请售后")}`} className="btn btn--ghost btn--sm">申请售后</Link>}
                    <Link to={`/service?order=${order.order_id}`} className="btn btn--ghost btn--sm">联系客服</Link>
                    <Link to={`/product/${order.product_id}`} className="btn btn--ghost btn--sm">再次购买</Link>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </UserCenterLayout>
  );
}
