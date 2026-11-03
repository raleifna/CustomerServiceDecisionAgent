import { CircleAlert, CreditCard, MapPin, Wallet, X } from "lucide-react";
import { useMemo, useState } from "react";

import { Customer, yuan } from "../api";
import { EmptyState, Link } from "../components";
import { useCatalog } from "../hooks";
import { ProductImage } from "../ProductImage";
import { useLocation } from "../router";
import { useCart } from "../store";

const PAYMENTS = [
  { key: "wallet", label: "ShopCare 钱包", icon: Wallet },
  { key: "card", label: "银行卡快捷支付", icon: CreditCard },
];

export default function CheckoutPage({ customer }: { customer: Customer | undefined }) {
  const { query } = useLocation();
  const cart = useCart();
  const catalog = useCatalog();
  const [payment, setPayment] = useState("wallet");
  const [noticeOpen, setNoticeOpen] = useState(false);
  const byId = useMemo(() => new Map(catalog.map((product) => [product.product_id, product])), [catalog]);

  // "立即购买" passes a single line in the URL; otherwise check out the
  // selected cart lines.
  const buy = query.get("buy");
  const lines = buy
    ? [{ product_id: buy, sku_id: query.get("sku") ?? "", sku_name: query.get("skuName") ?? "", quantity: Math.max(1, Number(query.get("qty")) || 1) }]
    : cart.filter((line) => line.selected);
  const rows = lines.map((line) => ({ line, product: byId.get(line.product_id) })).filter((row) => row.product);
  const goods = rows.reduce((sum, { line, product }) => sum + product!.price * line.quantity, 0);
  const discount = rows.reduce((sum, { line, product }) => sum + Math.max(0, (product!.original_price ?? product!.price) - product!.price) * line.quantity, 0);

  if (catalog.length > 0 && rows.length === 0) {
    return <div className="page-container"><EmptyState title="没有需要结算的商品" action={<Link to="/cart" className="btn btn--primary">返回购物车</Link>} /></div>;
  }

  return (
    <div className="page-container checkout">
      <h1 className="page-title">确认订单</h1>

      <section className="checkout-card">
        <h2>收货地址</h2>
        <div className="address-card active">
          <MapPin size={18} />
          <div><b>{customer?.customer_id ?? "演示用户"}（默认）</b><span>演示收货地址 · 浙江省杭州市西湖区文三路 000 号</span></div>
        </div>
      </section>

      <section className="checkout-card">
        <h2>商品清单</h2>
        {rows.map(({ line, product }) => {
          const skuName = line.sku_name || product!.title;
          return (
            <div className="checkout-line" key={`${line.product_id}-${line.sku_id}`}>
              <ProductImage product={product} size="sm" />
              <div><b>{product!.title}</b><small>{skuName}</small></div>
              <span>{yuan(product!.price)} × {line.quantity}</span>
              <b>{yuan(product!.price * line.quantity)}</b>
            </div>
          );
        })}
      </section>

      <section className="checkout-card">
        <h2>支付方式</h2>
        <div className="payment-options">
          {PAYMENTS.map(({ key, label, icon: Icon }) => (
            <button key={key} className={payment === key ? "active" : ""} onClick={() => setPayment(key)}><Icon size={17} />{label}</button>
          ))}
        </div>
      </section>

      <div className="checkout-summary">
        <dl>
          <dt>商品金额</dt><dd>{yuan(goods + discount)}</dd>
          <dt>优惠</dt><dd className="text-brand">-{yuan(discount)}</dd>
          <dt>运费</dt><dd>包邮</dd>
        </dl>
        <div className="checkout-summary__pay">
          <span>应付金额：<b>{yuan(goods)}</b></span>
          <button className="btn btn--primary btn--lg" onClick={() => setNoticeOpen(true)}>提交订单</button>
        </div>
      </div>

      {noticeOpen && (
        <div className="modal-backdrop" onClick={() => setNoticeOpen(false)}>
          <div className="modal notice-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <button className="icon-btn notice-modal__close" onClick={() => setNoticeOpen(false)} aria-label="关闭"><X size={18} /></button>
            <CircleAlert size={34} className="text-brand" />
            <h2>演示环境暂不支持下单</h2>
            <p>本站用于演示多智能体售后决策。订单、物流与售后案例来自项目固定的 Mock 数据集，提交订单不会生成新订单，也不会发生任何扣款。</p>
            <p>您可以前往「我的订单」，对已有订单体验退款、物流查询与售后申请。</p>
            <div className="notice-modal__actions">
              <button className="btn btn--ghost" onClick={() => setNoticeOpen(false)}>继续浏览</button>
              <Link to="/orders" className="btn btn--primary">查看我的订单</Link>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
