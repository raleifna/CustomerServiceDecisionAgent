import { ChevronRight, FileText, Headset, LogIn, Package, ReceiptText, UserRound } from "lucide-react";
import { ReactNode } from "react";

import { compactCount, Customer, ProductCard, vipLabels, yuan } from "./api";
import { ProductImage, Stars } from "./ProductImage";
import { linkHandler, useLocation } from "./router";

export function Link({ to, className, children, title }: { to: string; className?: string; children: ReactNode; title?: string }) {
  return <a href={to} className={className} title={title} onClick={linkHandler(to)}>{children}</a>;
}

export function Price({ value, original, size = "md" }: { value: number; original?: number | null; size?: "sm" | "md" | "lg" }) {
  const [integer, decimal] = value.toFixed(2).split(".");
  return (
    <span className={`price price--${size}`}>
      <span className="price__now"><small>¥</small>{integer}<small>.{decimal}</small></span>
      {original != null && original > value && <s className="price__was">{yuan(original)}</s>}
    </span>
  );
}

export function ProductTile({ product }: { product: ProductCard }) {
  return (
    <Link to={`/product/${product.product_id}`} className="product-tile">
      <ProductImage product={product} size="md" />
      <div className="product-tile__body">
        <h3>{product.title}</h3>
        <p>{product.subtitle}</p>
        <div className="product-tile__tags">
          {product.tags.slice(0, 2).map((tag) => <span key={tag} className={tag === "自营" ? "tag tag--brand" : "tag"}>{tag}</span>)}
          {product.is_fragile && <span className="tag tag--blue">易碎品保障</span>}
        </div>
        <div className="product-tile__foot">
          <Price value={product.price} original={product.original_price} />
          <small>{compactCount(product.review_count)}条评价</small>
        </div>
        <div className="product-tile__rating"><Stars value={product.rating} /><span>{product.rating.toFixed(1)}</span><em>{product.brand}</em></div>
      </div>
    </Link>
  );
}

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon">{icon ?? <Package size={26} />}</span>
      <b>{title}</b>
      {hint && <p>{hint}</p>}
      {action}
    </div>
  );
}

export function LoginRequired({ onLogin, what }: { onLogin: () => void; what: string }) {
  return (
    <div className="page-container">
      <EmptyState
        icon={<LogIn size={26} />}
        title={`登录后查看${what}`}
        hint="演示环境提供了一组 Mock 客户账号，选择任意账号即可登录。"
        action={<button className="btn btn--primary" onClick={onLogin}>选择演示账号登录</button>}
      />
    </div>
  );
}

export function Breadcrumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <nav className="breadcrumbs" aria-label="面包屑">
      {items.map((item, index) => (
        <span key={`${item.label}-${index}`}>
          {index > 0 && <ChevronRight size={12} />}
          {item.to ? <Link to={item.to}>{item.label}</Link> : <b>{item.label}</b>}
        </span>
      ))}
    </nav>
  );
}

const userCenterNav = [
  { to: "/orders", label: "我的订单", icon: ReceiptText, match: (path: string) => path.startsWith("/orders") },
  { to: "/after-sales", label: "退款/售后", icon: FileText, match: (path: string) => path.startsWith("/after-sales") },
  { to: "/service", label: "联系客服", icon: Headset, match: (path: string) => path.startsWith("/service") },
];

export function UserCenterLayout({ customer, children }: { customer: Customer | undefined; children: ReactNode }) {
  const { path } = useLocation();
  return (
    <div className="page-container user-center">
      <aside className="user-center__nav">
        <div className="user-center__profile">
          <span className="avatar avatar--lg"><UserRound size={22} /></span>
          <b>{customer?.customer_id ?? "—"}</b>
          <span className={`vip-badge vip-badge--${(customer?.vip_level ?? "Normal").toLowerCase()}`}>{vipLabels[customer?.vip_level ?? ""] ?? customer?.vip_level}</span>
          <small>注册于 {customer?.register_date ?? "—"}</small>
        </div>
        <nav>
          {userCenterNav.map(({ to, label, icon: Icon, match }) => (
            <Link key={to} to={to} className={match(path) ? "active" : ""}><Icon size={16} />{label}<ChevronRight size={14} /></Link>
          ))}
        </nav>
      </aside>
      <section className="user-center__main">{children}</section>
    </div>
  );
}
