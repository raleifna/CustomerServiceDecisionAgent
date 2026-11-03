import {
  Bean,
  ChevronLeft,
  ChevronRight,
  Fan,
  Footprints,
  Gem,
  Headset,
  Package,
  PackageCheck,
  ReceiptText,
  Shirt,
  Smartphone,
  Sofa,
  Sparkles,
  Truck,
  UserRound,
  Watch,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Customer, CustomerServiceRequestProgress, ProductCard, vipLabels } from "../api";
import { Link, Price, ProductTile } from "../components";
import { useCatalog, useCustomerOrders, useFetch } from "../hooks";
import { ProductImage } from "../ProductImage";
import { useActiveCustomerId } from "../store";

const categoryIcons = [
  { category: "数码", icon: Smartphone },
  { category: "家电", icon: Fan },
  { category: "服饰", icon: Shirt },
  { category: "鞋靴", icon: Footprints },
  { category: "家居", icon: Sofa },
  { category: "食品", icon: Bean },
  { category: "饰品", icon: Watch },
  { category: "奢侈品", icon: Gem },
];

const banners = [
  { productId: "PRO0001", eyebrow: "新品首发", title: "沉浸静享，一键降噪", copy: "AirBeat Pro 主动降噪耳机，限时直降 200 元", tone: "indigo" },
  { productId: "PRO0015", eyebrow: "焕新家电节", title: "每一口呼吸都清新", copy: "Pure5 净化器以旧换新，最高补贴 300 元", tone: "sky" },
  { productId: "PRO0006", eyebrow: "运动季", title: "轻弹每一步", copy: "CloudRun 跑步鞋会员专享 8 折起", tone: "rose" },
];

export default function HomePage({ customer, onLogin }: { customer: Customer | undefined; onLogin: () => void }) {
  const products = useCatalog();
  const customerId = useActiveCustomerId();
  const orders = useCustomerOrders(customerId);
  const requests = useFetch<CustomerServiceRequestProgress[]>(customerId ? `/customers/${customerId}/service-requests` : null);
  const openAfterSales = (requests.data ?? []).filter((request) => request.workflow_status !== "Closed").length;
  const [slide, setSlide] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setSlide((current) => (current + 1) % banners.length), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const byId = useMemo(() => new Map(products.map((product) => [product.product_id, product])), [products]);
  const flashSale = useMemo(
    () => products
      .filter((product) => product.original_price && product.original_price > product.price)
      .sort((a, b) => (b.original_price! - b.price) / b.original_price! - (a.original_price! - a.price) / a.original_price!)
      .slice(0, 5),
    [products],
  );
  const recommended = useMemo(() => [...products].sort((a, b) => b.monthly_sales - a.monthly_sales), [products]);
  const counts = useMemo(() => {
    const list = orders.data ?? [];
    return {
      pending: list.filter((order) => order.order_status === "PendingShipment").length,
      shipping: list.filter((order) => order.order_status === "Shipped").length,
      delivered: list.filter((order) => order.order_status === "Delivered").length,
    };
  }, [orders.data]);

  const banner = banners[slide];
  const bannerProduct = byId.get(banner.productId);

  return (
    <div className="page-container home">
      <section className="home-hero">
        <aside className="home-categories">
          {categoryIcons.map(({ category, icon: Icon }) => (
            <Link key={category} to={`/search?category=${encodeURIComponent(category)}`}>
              <Icon size={16} /><span>{category}</span><ChevronRight size={13} />
            </Link>
          ))}
        </aside>

        <div className={`home-banner home-banner--${banner.tone}`}>
          <div className="home-banner__copy">
            <span className="home-banner__eyebrow"><Sparkles size={13} />{banner.eyebrow}</span>
            <h1>{banner.title}</h1>
            <p>{banner.copy}</p>
            {bannerProduct && (
              <div className="home-banner__price">
                <Price value={bannerProduct.price} original={bannerProduct.original_price} size="lg" />
              </div>
            )}
            <Link to={`/product/${banner.productId}`} className="btn btn--light">立即抢购</Link>
          </div>
          <ProductImage product={bannerProduct} size="lg" className="home-banner__image" />
          <button className="home-banner__nav home-banner__nav--prev" aria-label="上一张" onClick={() => setSlide((slide + banners.length - 1) % banners.length)}><ChevronLeft size={18} /></button>
          <button className="home-banner__nav home-banner__nav--next" aria-label="下一张" onClick={() => setSlide((slide + 1) % banners.length)}><ChevronRight size={18} /></button>
          <div className="home-banner__dots">
            {banners.map((item, index) => <button key={item.productId} className={index === slide ? "active" : ""} onClick={() => setSlide(index)} aria-label={`第 ${index + 1} 张`} />)}
          </div>
        </div>

        <aside className="home-user">
          <div className="home-user__head">
            <span className="avatar avatar--lg"><UserRound size={22} /></span>
            {customer ? (
              <div><b>Hi，{customer.customer_id}</b><span className={`vip-badge vip-badge--${customer.vip_level.toLowerCase()}`}>{vipLabels[customer.vip_level] ?? customer.vip_level}</span></div>
            ) : (
              <div><b>Hi，欢迎来到 ShopCare</b><small>登录后查看订单与售后</small></div>
            )}
          </div>
          {customer ? (
            <div className="home-user__orders">
              <Link to="/orders?tab=pending"><Package size={18} /><b>{counts.pending}</b><small>待发货</small></Link>
              <Link to="/orders?tab=shipping"><Truck size={18} /><b>{counts.shipping}</b><small>运输中</small></Link>
              <Link to="/orders?tab=delivered"><PackageCheck size={18} /><b>{counts.delivered}</b><small>已签收</small></Link>
              <Link to="/after-sales"><ReceiptText size={18} /><b>{openAfterSales}</b><small>售后中</small></Link>
            </div>
          ) : (
            <button className="btn btn--primary btn--block" onClick={onLogin}>登录 / 选择演示账号</button>
          )}
          <Link to="/service" className="home-user__service">
            <Headset size={20} />
            <span><b>智能售后助手</b><small>退款、物流、催发货，一句话搞定</small></span>
            <ChevronRight size={14} />
          </Link>
          <ul className="home-user__notice">
            <li><em>公告</em>售后申请支持上传图片证据，审核进度实时可查</li>
            <li><em>服务</em>高风险或大额申请将由人工客服复核</li>
          </ul>
        </aside>
      </section>

      {flashSale.length > 0 && (
        <section className="home-section flash-sale">
          <header>
            <h2><Zap size={20} />限时秒杀</h2>
            <span className="flash-sale__timer">本场距结束 <Countdown /></span>
            <Link to="/search?sort=price_asc">更多好价 <ChevronRight size={14} /></Link>
          </header>
          <div className="flash-sale__row">
            {flashSale.map((product) => <FlashItem key={product.product_id} product={product} />)}
          </div>
        </section>
      )}

      <section className="home-section">
        <header>
          <h2>为你推荐</h2>
          <span className="muted">根据热销与好评为你精选</span>
        </header>
        <div className="product-grid">
          {recommended.map((product) => <ProductTile key={product.product_id} product={product} />)}
          {products.length === 0 && Array.from({ length: 10 }, (_, index) => <div key={index} className="product-tile product-tile--skeleton" />)}
        </div>
      </section>
    </div>
  );
}

function FlashItem({ product }: { product: ProductCard }) {
  const discount = Math.round((1 - product.price / product.original_price!) * 100);
  return (
    <Link to={`/product/${product.product_id}`} className="flash-item">
      <ProductImage product={product} size="sm" />
      <b>{product.title}</b>
      <Price value={product.price} original={product.original_price} size="sm" />
      <span className="flash-item__bar"><i style={{ width: `${Math.min(95, 40 + discount * 2)}%` }} /><small>已抢 {Math.min(95, 40 + discount * 2)}%</small></span>
    </Link>
  );
}

function Countdown() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const end = new Date(now);
  end.setHours(Math.floor(end.getHours() / 2) * 2 + 2, 0, 0, 0);
  const left = Math.max(0, Math.floor((end.getTime() - now) / 1000));
  const parts = [Math.floor(left / 3600), Math.floor((left % 3600) / 60), left % 60].map((part) => String(part).padStart(2, "0"));
  return <span className="countdown">{parts.map((part, index) => <i key={index}>{part}</i>)}</span>;
}
