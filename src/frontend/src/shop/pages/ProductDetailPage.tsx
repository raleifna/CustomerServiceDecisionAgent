import {
  BadgeCheck,
  Check,
  Headset,
  Heart,
  MapPin,
  Minus,
  PackageCheck,
  Plus,
  ShieldCheck,
  ShoppingCart,
  Store,
  Truck,
} from "lucide-react";
import { useMemo, useState } from "react";

import { compactCount, orderStatusLabels, ProductCard, ProductDetail } from "../api";
import { Breadcrumbs, CustomizedNotice, EmptyState, Link, Price, ProductTile } from "../components";
import { useCustomerOrders, useFetch } from "../hooks";
import { ProductImage, Stars } from "../ProductImage";
import { navigate } from "../router";
import { addToCart } from "../store";

type Tab = "detail" | "specs" | "reviews" | "service";

export default function ProductDetailPage({ productId, customerId, onLogin }: { productId: string; customerId: string | null; onLogin: () => void }) {
  const { data: product, loading, error } = useFetch<ProductDetail>(`/products/${encodeURIComponent(productId)}`);
  const related = useFetch<ProductCard[]>(product ? `/products?category=${encodeURIComponent(product.category)}&sort=sales` : null);
  const orders = useCustomerOrders(customerId);
  const [image, setImage] = useState<0 | 1 | 2 | 3>(0);
  const [skuId, setSkuId] = useState<string | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [tab, setTab] = useState<Tab>("detail");
  const [toast, setToast] = useState<string | null>(null);

  const purchased = useMemo(
    () => (orders.data ?? []).filter((order) => order.product_id === productId),
    [orders.data, productId],
  );

  if (error) {
    return <div className="page-container"><EmptyState title="商品不存在或已下架" hint={error} action={<Link to="/" className="btn btn--primary">返回首页</Link>} /></div>;
  }
  if (loading || !product) {
    return <div className="page-container"><div className="pdp pdp--skeleton" /></div>;
  }

  const sku = product.skus.find((item) => item.sku_id === skuId) ?? product.skus[0];

  function flash(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(null), 2200);
  }

  function handleAddToCart() {
    addToCart({ product_id: product!.product_id, sku_id: sku.sku_id, sku_name: sku.sku_name, quantity });
    flash("已加入购物车");
  }

  function handleBuyNow() {
    if (!customerId) {
      onLogin();
      return;
    }
    navigate(`/checkout?buy=${encodeURIComponent(product!.product_id)}&sku=${encodeURIComponent(sku.sku_id)}&skuName=${encodeURIComponent(sku.sku_name)}&qty=${quantity}`);
  }

  const relatedProducts = (related.data ?? []).filter((item) => item.product_id !== product.product_id).slice(0, 4);
  const ratingCounts = [5, 4, 3, 2, 1].map((star) => product.reviews.filter((review) => review.rating === star).length);

  return (
    <div className="page-container">
      <Breadcrumbs items={[{ label: "首页", to: "/" }, { label: product.category, to: `/search?category=${encodeURIComponent(product.category)}` }, { label: product.brand }]} />

      <section className="pdp">
        <div className="pdp-gallery">
          <ProductImage product={product} size="lg" variant={image} />
          <div className="pdp-gallery__thumbs">
            {([0, 1, 2, 3] as const).map((index) => (
              <button key={index} className={image === index ? "active" : ""} onMouseEnter={() => setImage(index)} onClick={() => setImage(index)} aria-label={`图片 ${index + 1}`}>
                <ProductImage product={product} size="xs" variant={index} />
              </button>
            ))}
          </div>
          <div className="pdp-gallery__meta"><button className="link-btn"><Heart size={14} />收藏商品</button><span>商品编号 {product.product_id}</span></div>
        </div>

        <div className="pdp-info">
          <h1>
            {product.tags.includes("自营") && <span className="tag tag--brand">自营</span>}
            {product.title}
          </h1>
          <p className="pdp-info__subtitle">{product.subtitle}</p>

          <div className="pdp-price">
            <div className="pdp-price__row">
              <span className="pdp-price__label">到手价</span>
              <Price value={product.price} original={product.original_price} size="lg" />
              {product.original_price && product.original_price > product.price && (
                <span className="pdp-price__save">已省 ¥{(product.original_price - product.price).toFixed(0)}</span>
              )}
            </div>
            <div className="pdp-price__row pdp-price__row--promo">
              <span className="pdp-price__label">促销</span>
              <span className="tag tag--outline">会员专享</span><span>铂金会员享优先售后通道</span>
            </div>
            <div className="pdp-price__stats">
              <span><b>{product.rating.toFixed(1)}</b><Stars value={product.rating} /></span>
              <span>累计评价 <b>{compactCount(product.review_count)}</b></span>
              <span>月销 <b>{compactCount(product.monthly_sales)}</b></span>
            </div>
          </div>

          <dl className="pdp-options">
            <dt>配送</dt>
            <dd className="pdp-options__delivery"><MapPin size={14} />至 <b>默认收货地址</b><span className="muted">· 现货，预计 48 小时内发货</span></dd>
            <dt>服务</dt>
            <dd className="pdp-options__promises">
              {product.service_promises.map((promise) => <span key={promise.title} title={promise.detail}><Check size={12} />{promise.title}</span>)}
            </dd>
            <dt>规格</dt>
            <dd className="pdp-options__skus">
              {product.skus.map((item) => (
                <button key={item.sku_id} className={item.sku_id === sku.sku_id ? "active" : ""} onClick={() => setSkuId(item.sku_id)}>{item.sku_name}</button>
              ))}
            </dd>
            <dt>数量</dt>
            <dd>
              <span className="stepper">
                <button onClick={() => setQuantity(Math.max(1, quantity - 1))} disabled={quantity <= 1} aria-label="减少"><Minus size={13} /></button>
                <input value={quantity} onChange={(event) => setQuantity(Math.max(1, Math.min(99, Number(event.target.value.replace(/\D/g, "")) || 1)))} aria-label="数量" />
                <button onClick={() => setQuantity(Math.min(99, quantity + 1))} aria-label="增加"><Plus size={13} /></button>
              </span>
            </dd>
          </dl>

          {product.is_customized && <CustomizedNotice variant="banner" />}

          <div className="pdp-actions">
            <button className="btn btn--outline btn--lg" onClick={handleAddToCart}><ShoppingCart size={17} />加入购物车</button>
            <button className="btn btn--primary btn--lg" onClick={handleBuyNow}>立即购买</button>
            <Link to={`/service?product=${encodeURIComponent(product.product_id)}`} className="btn btn--ghost btn--lg"><Headset size={17} />咨询客服</Link>
          </div>

          {purchased.length > 0 && (
            <div className="pdp-purchased">
              <PackageCheck size={18} />
              <div>
                <b>您购买过此商品</b>
                {purchased.map((order) => (
                  <span key={order.order_id}>
                    <Link to={`/orders/${order.order_id}`}>订单 {order.order_id}</Link>
                    <em>{orderStatusLabels[order.order_status]}</em>
                    <Link to={`/service?order=${order.order_id}`} className="link-strong">售后咨询</Link>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        <aside className="pdp-store">
          <div className="pdp-store__head"><span className="avatar"><Store size={16} /></span><div><b>{product.brand}</b><small>{product.tags.includes("自营") ? "ShopCare 自营旗舰店" : "品牌授权店"}</small></div></div>
          <div className="pdp-store__scores"><span>商品<b>4.9</b></span><span>服务<b>4.8</b></span><span>物流<b>4.9</b></span></div>
          <Link to={`/search?q=${encodeURIComponent(product.brand.split(" ")[0])}`} className="btn btn--ghost btn--block">进店逛逛</Link>
          <div className="pdp-store__assurance">
            <span><BadgeCheck size={14} />正品保障</span>
            <span><Truck size={14} />顺丰/京东物流</span>
            <span><ShieldCheck size={14} />智能售后</span>
          </div>
        </aside>
      </section>

      <section className="pdp-body">
        <div className="pdp-tabs-panel">
          <nav className="pdp-tabs">
            <button className={tab === "detail" ? "active" : ""} onClick={() => setTab("detail")}>商品介绍</button>
            <button className={tab === "specs" ? "active" : ""} onClick={() => setTab("specs")}>规格参数</button>
            <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}>商品评价({compactCount(product.review_count)})</button>
            <button className={tab === "service" ? "active" : ""} onClick={() => setTab("service")}>售后保障</button>
          </nav>

          {tab === "detail" && (
            <div className="pdp-detail">
              <ul className="pdp-highlights">
                {product.highlights.map((item) => <li key={item}><Check size={14} />{item}</li>)}
              </ul>
              <p>{product.description}</p>
              <div className="pdp-detail__visual">
                <ProductImage product={product} size="lg" variant={1} />
                <div><span className="eyebrow-text">{product.brand}</span><h3>{product.subtitle}</h3></div>
              </div>
            </div>
          )}

          {tab === "specs" && (
            <table className="spec-table">
              <tbody>
                {product.specs.map((spec) => <tr key={spec.label}><th>{spec.label}</th><td>{spec.value}</td></tr>)}
                <tr><th>商品编号</th><td>{product.product_id}</td></tr>
                <tr><th>品类</th><td>{product.category}</td></tr>
              </tbody>
            </table>
          )}

          {tab === "reviews" && (
            <div className="reviews">
              <div className="reviews__summary">
                <div><b>{Math.round((product.rating / 5) * 100)}%</b><small>好评率</small></div>
                <div className="reviews__bars">
                  {ratingCounts.map((count, index) => (
                    <span key={index}><em>{5 - index} 星</em><i><i style={{ width: `${product.reviews.length ? (count / product.reviews.length) * 100 : 0}%` }} /></i></span>
                  ))}
                </div>
              </div>
              {product.reviews.map((review, index) => (
                <article key={index} className="review">
                  <header><span className="avatar avatar--sm">{review.user.slice(-1)}</span><b>{review.user}</b><Stars value={review.rating} /><time>{review.date}</time></header>
                  <p>{review.content}</p>
                  <small>{review.sku_name}</small>
                </article>
              ))}
              {product.reviews.length === 0 && <p className="muted">暂无评价</p>}
            </div>
          )}

          {tab === "service" && (
            <div className="service-promises">
              {product.service_promises.map((promise) => (
                <div key={promise.title}><ShieldCheck size={20} /><span><b>{promise.title}</b><small>{promise.detail}</small></span></div>
              ))}
              <p className="muted">售后申请由 ShopCare 多智能体客服根据订单、物流与证据自动审核，大额或高风险申请会转人工复核。</p>
            </div>
          )}
        </div>

        <aside className="pdp-related">
          <h3>同类热销</h3>
          {relatedProducts.map((item) => <ProductTile key={item.product_id} product={item} />)}
          {relatedProducts.length === 0 && <p className="muted">暂无推荐</p>}
        </aside>
      </section>

      {toast && <div className="toast"><Check size={16} />{toast}<Link to="/cart">去结算</Link></div>}
    </div>
  );
}
