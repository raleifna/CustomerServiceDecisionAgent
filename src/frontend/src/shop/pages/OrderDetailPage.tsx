import { AlertTriangle, CheckCircle2, ClipboardList, CreditCard, Headset, MapPin, Package, PackageCheck, Truck } from "lucide-react";
import { useEffect, useState } from "react";

import { Customer, CustomerServiceRequestProgress, OrderDetail, orderStatusLabels, yuan } from "../api";
import { Breadcrumbs, CustomizedNotice, EmptyState, Link, UserCenterLayout } from "../components";
import { useFetch } from "../hooks";
import { ProductImage } from "../ProductImage";
import { ReturnShipmentCard } from "../ReturnShipment";
import { requestBadge } from "./OrdersPage";

const statusCopy = {
  PendingShipment: { title: "等待卖家发货", hint: "商家正在备货，发货后将第一时间通知您。", icon: Package },
  Shipped: { title: "商品运输中", hint: "包裹正在运输途中，请保持电话畅通。", icon: Truck },
  Delivered: { title: "订单已签收", hint: "感谢您的购买，如商品有任何问题可随时申请售后。", icon: PackageCheck },
};

export default function OrderDetailPage({ customerId, customer, orderId }: { customerId: string; customer: Customer | undefined; orderId: string }) {
  const { data: order, error, loading } = useFetch<OrderDetail>(`/me/orders/${encodeURIComponent(orderId)}`, [customerId, orderId]);
  const [requestsVersion, setRequestsVersion] = useState(0);
  const requests = useFetch<CustomerServiceRequestProgress[]>("/me/service-requests", [customerId, requestsVersion]);
  const orderRequests = (requests.data ?? []).filter((request) => request.order_id === orderId);
  const awaitingReturn = orderRequests.find((request) => request.workflow_status === "ReturnAwaitingShipment");

  useEffect(() => {
    if (order && window.location.hash === "#tracking") {
      document.getElementById("tracking")?.scrollIntoView({ behavior: "smooth" });
    }
  }, [order]);

  if (error) {
    return <UserCenterLayout customer={customer}><EmptyState title="无法查看该订单" hint={error} action={<Link to="/orders" className="btn btn--primary">返回我的订单</Link>} /></UserCenterLayout>;
  }
  if (loading || !order) {
    return <UserCenterLayout customer={customer}><div className="panel order-detail--skeleton" /></UserCenterLayout>;
  }

  const copy = statusCopy[order.order_status];
  const StatusIcon = copy.icon;
  const goodsTotal = order.order_items.reduce((sum, item) => sum + item.unit_price * item.quantity, 0);
  const discountTotal = order.order_items.reduce((sum, item) => sum + item.discount_amount, 0);
  const checkpoints = [...(order.tracking?.checkpoints ?? [])].reverse();
  const steps = [
    { label: "提交订单", time: order.order_date, done: true },
    { label: "付款成功", time: order.order_date, done: order.payment_status === "Paid" },
    { label: "商家发货", time: order.shipped_date ?? order.promised_ship_date ?? "", done: Boolean(order.shipped_date) },
    { label: "确认收货", time: order.delivered_date ?? order.estimated_delivery_date ?? "", done: Boolean(order.delivered_date) },
  ];
  const ask = (text: string) => `/service?order=${order.order_id}&ask=${encodeURIComponent(text)}`;

  return (
    <UserCenterLayout customer={customer}>
      <Breadcrumbs items={[{ label: "我的订单", to: "/orders" }, { label: `订单 ${order.order_id}` }]} />

      <section className="panel order-status-hero">
        <div className={`order-status-hero__icon order-status-hero__icon--${order.order_status.toLowerCase()}`}><StatusIcon size={26} /></div>
        <div className="order-status-hero__copy">
          <h1>{copy.title}</h1>
          <p>{awaitingReturn ? "退货申请已通过，请按下方指引寄回商品并填写运单号。" : copy.hint}</p>
          <small>订单号 {order.order_id} · 下单时间 {order.order_date}</small>
        </div>
        <div className="order-status-hero__actions">
          {order.order_status === "PendingShipment" && <Link to={ask("这个订单什么时候发货？")} className="btn btn--primary">催发货</Link>}
          {order.order_status === "Shipped" && <Link to={ask("我的快递到哪了？")} className="btn btn--primary">物流咨询</Link>}
          <Link to={ask("我想申请售后")} className="btn btn--outline">申请售后</Link>
          <Link to={`/service?order=${order.order_id}`} className="btn btn--ghost"><Headset size={15} />联系客服</Link>
        </div>
        {order.product?.is_customized && <div className="order-status-hero__notice"><CustomizedNotice variant="banner" /></div>}
        <ol className="order-steps">
          {steps.map((step, index) => (
            <li key={step.label} className={step.done ? "done" : ""}>
              <i>{step.done ? <CheckCircle2 size={16} /> : index + 1}</i>
              <b>{step.label}</b>
              <small>{step.time || "—"}</small>
            </li>
          ))}
        </ol>
      </section>

      {orderRequests.length > 0 && (
        <section className="panel order-requests">
          <h2><ClipboardList size={17} />售后记录</h2>
          {orderRequests.map((request) => {
            const badge = requestBadge(request);
            const refunded = request.actions.some((action) => action.action_type === "RefundPayment" && action.workflow_status === "Succeeded");
            return (
              <div key={request.request_id} className="order-requests__item">
                <div className="order-requests__row">
                  <span className={`pill pill--${badge.tone}`}>{refunded ? "退款成功" : badge.label}</span>
                  <b>{request.request_reason}</b>
                  <span className="muted">申请单 {request.request_id} · {request.request_date}</span>
                  {request.approved_amount != null && <span>批准金额 {yuan(request.approved_amount)}</span>}
                  <Link to={`/after-sales?focus=${request.request_id}`} className="link-strong">查看进度</Link>
                </div>
                {request.return_order && (
                  <ReturnShipmentCard
                    returnOrder={request.return_order}
                    refundSucceeded={refunded}
                    compact
                    onSubmitted={() => setRequestsVersion((version) => version + 1)}
                  />
                )}
              </div>
            );
          })}
        </section>
      )}

      <div className="order-detail-grid">
        <section className="panel" id="tracking">
          <h2><Truck size={17} />物流信息</h2>
          {order.logistics.map((event) => (
            <div key={`${event.carrier}-${event.exception_recorded_date}`} className={`logistics-alert ${event.resolved ? "logistics-alert--resolved" : ""}`}>
              <AlertTriangle size={16} />
              <span><b>{event.carrier} · {event.exception_type}</b><small>{event.exception_recorded_date} · {event.resolved ? "已处理" : "平台处理中"}</small></span>
            </div>
          ))}
          {order.tracking ? (
            <>
              <div className="tracking-head">
                <span>{order.tracking.carrier}</span>
                <span>运单号 <code>{order.tracking.tracking_number}</code></span>
                <span className="pill pill--orange">{order.tracking.current_status}</span>
              </div>
              <ol className="tracking-timeline">
                {checkpoints.map((checkpoint, index) => (
                  <li key={`${checkpoint.time}-${index}`} className={index === 0 ? "latest" : ""}>
                    <i />
                    <div><b>{checkpoint.description}</b><small>{checkpoint.time} · {checkpoint.location}</small></div>
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p className="muted tracking-empty">{order.shipped_date ? "暂无物流轨迹" : `商品尚未发货，承诺 ${order.promised_ship_date ?? "—"} 前发货。`}</p>
          )}
        </section>

        <section className="panel">
          <h2><MapPin size={17} />收货信息</h2>
          <dl className="kv">
            <dt>收货人</dt><dd>{customerId}</dd>
            <dt>收货地址</dt><dd>演示收货地址 · 浙江省杭州市西湖区文三路 000 号</dd>
            <dt>预计送达</dt><dd>{order.estimated_delivery_date ?? "—"}</dd>
            {order.delivered_date && <><dt>签收时间</dt><dd>{order.delivered_date}</dd></>}
          </dl>
          <h2 className="panel__subtitle"><CreditCard size={17} />支付信息</h2>
          <dl className="kv">
            <dt>支付状态</dt><dd>{order.payment_status === "Paid" ? "已支付" : order.payment_status}</dd>
            <dt>支付时间</dt><dd>{order.order_date}</dd>
          </dl>
        </section>
      </div>

      <section className="panel">
        <h2><Package size={17} />商品信息</h2>
        <div className="order-items">
          <div className="order-items__row order-items__row--head"><span>商品</span><span>单价</span><span>数量</span><span>优惠</span><span>实付</span><span>出库</span></div>
          {order.order_items.map((item) => (
            <div key={item.order_item_id} className="order-items__row">
              <Link to={`/product/${item.product_id}`} className="order-items__product">
                <ProductImage product={item.product_id === order.product_id ? order.product : null} size="sm" />
                <span>
                  <b>{item.product_id === order.product_id ? order.product?.title : item.product_id}</b>
                  <small>{item.sku_name}</small>
                  {item.product_id === order.product_id && order.product?.is_customized && <CustomizedNotice variant="tag" />}
                </span>
              </Link>
              <span>{yuan(item.unit_price)}</span>
              <span>×{item.quantity}</span>
              <span>{item.discount_amount > 0 ? `-${yuan(item.discount_amount)}` : "—"}</span>
              <b>{yuan(item.paid_amount)}</b>
              <span className="muted">{item.shipped_quantity === null ? "未出库" : `已出库 ${item.shipped_quantity} 件`}</span>
            </div>
          ))}
        </div>
        <dl className="order-amounts">
          <dt>商品总额</dt><dd>{yuan(goodsTotal)}</dd>
          <dt>优惠</dt><dd>-{yuan(discountTotal)}</dd>
          <dt>运费</dt><dd>{yuan(0)}</dd>
          <dt className="order-amounts__total">实付款</dt><dd className="order-amounts__total">{yuan(order.amount)}</dd>
          {order.refunded_amount > 0 && <><dt>已退款</dt><dd className="text-green">{yuan(order.refunded_amount)}</dd></>}
        </dl>
      </section>
    </UserCenterLayout>
  );
}
