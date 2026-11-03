import {
  BadgeCheck,
  ChevronDown,
  Headset,
  LogOut,
  RotateCcw,
  Search,
  ShieldCheck,
  ShoppingBag,
  ShoppingCart,
  Truck,
  UserRound,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import ServiceCenter from "../ServiceCenter";
import { apiFetch, CATEGORIES, vipLabels } from "./api";
import { Link, LoginRequired } from "./components";
import { useDemoAccounts, useMyProfile } from "./hooks";
import CartPage from "./pages/CartPage";
import CheckoutPage from "./pages/CheckoutPage";
import HomePage from "./pages/HomePage";
import OrderDetailPage from "./pages/OrderDetailPage";
import OrdersPage from "./pages/OrdersPage";
import ProductDetailPage from "./pages/ProductDetailPage";
import ProductListPage from "./pages/ProductListPage";
import { navigate, useLocation } from "./router";
import { CustomerSession, setSession, useActiveCustomerId, useCart } from "./store";

const HOT_KEYWORDS = ["降噪耳机", "咖啡机", "跑步鞋", "空气净化器", "四件套"];

export default function Storefront() {
  const location = useLocation();
  const customers = useDemoAccounts();
  const customerId = useActiveCustomerId();
  const customer = useMyProfile(customerId);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState<string | null>(null);
  const cart = useCart();
  const [accountOpen, setAccountOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState(location.query.get("q") ?? "");
  const testAccounts = customers.filter((item) => item.test_scenario);
  const datasetAccounts = customers.filter((item) => !item.test_scenario);
  const [first, second] = location.segments;
  const isService = first === "service" || first === "after-sales";

  // Coming back from the Staff console: /?resumeCustomer=… — the session is
  // still in this browser, so just reopen the service center.
  useEffect(() => {
    if (!location.query.get("resumeCustomer")) return;
    navigate(customerId ? "/service" : "/", { replace: true });
  }, [location.query, customerId]);

  useEffect(() => {
    if (first === "search") setSearch(location.query.get("q") ?? "");
  }, [first, location.query]);

  useEffect(() => setMenuOpen(false), [location.path]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const q = search.trim();
    navigate(q ? `/search?q=${encodeURIComponent(q)}` : "/search");
  }

  async function login(id: string) {
    setLoggingIn(id);
    setLoginError(null);
    try {
      const response = await apiFetch("/auth/demo-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_id: id }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "登录失败，请稍后重试。");
      const session: CustomerSession = { access_token: body.access_token, customer_id: body.customer.customer_id, expires_at: body.expires_at };
      setSession(session);
      setAccountOpen(false);
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "登录失败，请稍后重试。");
    } finally {
      setLoggingIn(null);
    }
  }

  function logout() {
    setSession(null);
    setMenuOpen(false);
    navigate("/");
  }

  const openLogin = () => setAccountOpen(true);
  const cartCount = cart.reduce((sum, line) => sum + line.quantity, 0);

  let page;
  if (!first) page = <HomePage customer={customer} onLogin={openLogin} />;
  else if (first === "search") page = <ProductListPage />;
  else if (first === "product" && second) page = <ProductDetailPage productId={second} customerId={customerId} onLogin={openLogin} />;
  else if (first === "cart") page = <CartPage />;
  else if (!customerId) {
    const what = first === "checkout" ? "结算信息" : first === "orders" ? "我的订单" : first === "service" ? "客服会话" : "售后进度";
    page = <LoginRequired onLogin={openLogin} what={what} />;
  } else if (first === "checkout") page = <CheckoutPage customer={customer} />;
  else if (first === "orders" && second) page = <OrderDetailPage customerId={customerId} customer={customer} orderId={second} />;
  else if (first === "orders") page = <OrdersPage customerId={customerId} customer={customer} />;
  else if (isService) {
    page = (
      <ServiceCenter
        key={customerId}
        customerId={customerId}
        workspace={first === "after-sales" ? "requests" : "chat"}
        routeOrderId={location.query.get("order")}
        routeProductId={location.query.get("product")}
        routePrefill={location.query.get("ask")}
        routeFocus={location.query.get("focus")}
      />
    );
  } else page = <NotFound />;

  return (
    <div className="shop">
      <div className="utility-bar">
        <div className="page-container utility-bar__inner">
          <span>您好，欢迎来到 ShopCare 优选</span>
          <nav>
            {customerId ? (
              <div className="account-menu">
                <button onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen}>
                  <UserRound size={13} />{customerId}
                  {customer && <em className={`vip-badge vip-badge--${customer.vip_level.toLowerCase()}`}>{vipLabels[customer.vip_level] ?? customer.vip_level}</em>}
                  <ChevronDown size={12} />
                </button>
                {menuOpen && (
                  <div className="account-menu__panel">
                    <Link to="/orders">我的订单</Link>
                    <Link to="/after-sales">退款/售后</Link>
                    <button onClick={() => { setMenuOpen(false); openLogin(); }}><RotateCcw size={13} />切换账号</button>
                    <button onClick={logout}><LogOut size={13} />退出登录</button>
                  </div>
                )}
              </div>
            ) : (
              <button className="utility-bar__login" onClick={openLogin}>请登录</button>
            )}
            <Link to="/orders">我的订单</Link>
            <Link to="/after-sales">退款/售后</Link>
            <Link to="/service">客服中心</Link>
            <a href={`/staff${customerId ? `?returnCustomer=${encodeURIComponent(customerId)}` : ""}`}>人工工作台</a>
          </nav>
        </div>
      </div>

      <header className="shop-header">
        <div className="page-container shop-header__inner">
          <Link to="/" className="logo">
            <span className="logo__mark"><ShoppingBag size={20} /></span>
            <span className="logo__text"><b>ShopCare</b><small>优选 · 智能售后</small></span>
          </Link>
          <div className="shop-search">
            <form onSubmit={submitSearch}>
              <Search size={16} />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索商品、品牌" aria-label="搜索商品" />
              <button type="submit">搜索</button>
            </form>
            <div className="shop-search__hot">
              {HOT_KEYWORDS.map((keyword) => <Link key={keyword} to={`/search?q=${encodeURIComponent(keyword)}`}>{keyword}</Link>)}
            </div>
          </div>
          <div className="shop-header__actions">
            <Link to="/service" className={`header-action ${first === "service" ? "header-action--active" : ""}`}><Headset size={18} /><span>客服</span></Link>
            <Link to="/cart" className="header-action header-action--cart">
              <ShoppingCart size={18} /><span>购物车</span>
              {cartCount > 0 && <em>{cartCount > 99 ? "99+" : cartCount}</em>}
            </Link>
          </div>
        </div>
        <nav className="category-bar">
          <div className="page-container category-bar__inner">
            <Link to="/" className={!first ? "active" : ""}>首页</Link>
            {CATEGORIES.map((category) => (
              <Link
                key={category}
                to={`/search?category=${encodeURIComponent(category)}`}
                className={first === "search" && location.query.get("category") === category ? "active" : ""}
              >{category}</Link>
            ))}
            <Link to="/search?sort=sales" className={first === "search" && location.query.get("sort") === "sales" && !location.query.get("category") ? "active" : ""}>热销榜</Link>
          </div>
        </nav>
      </header>

      <main className="shop-main">{page}</main>

      <footer className="shop-footer">
        <div className="page-container">
          <div className="shop-footer__promises">
            <span><BadgeCheck size={22} /><b>正品保障</b><small>自营及授权商家</small></span>
            <span><Truck size={22} /><b>极速配送</b><small>多仓直发 · 次日达</small></span>
            <span><RotateCcw size={22} /><b>无忧售后</b><small>7 天无理由退货</small></span>
            <span><ShieldCheck size={22} /><b>智能客服</b><small>多智能体 7×24 小时</small></span>
          </div>
          <p className="shop-footer__note">
            ShopCare 优选是 CustomerServiceDecisionAgent 的教学演示站点：商品、订单与售后数据均来自项目内置的 Mock 数据集，不产生真实交易。
          </p>
        </div>
      </footer>

      {accountOpen && (
        <div className="modal-backdrop" onClick={() => setAccountOpen(false)}>
          <div className="modal account-modal" role="dialog" aria-modal="true" aria-label="选择演示账号" onClick={(event) => event.stopPropagation()}>
            <header>
              <div><h2>选择演示账号</h2><p>每个账号对应 Mock 数据集中的一位客户，登录后可查看其订单并发起售后。</p></div>
              <button className="icon-btn" onClick={() => setAccountOpen(false)} aria-label="关闭"><X size={18} /></button>
            </header>
            {testAccounts.length > 0 && (
              <section className="account-modal__section">
                <h3>测试账号 <small>订单日期贴近今天，可直接体验各类售后场景</small></h3>
                <div className="account-modal__tests">
                  {testAccounts.map((item) => (
                    <button key={item.customer_id} className={item.customer_id === customerId ? "active" : ""} onClick={() => void login(item.customer_id)} disabled={loggingIn !== null}>
                      <span className="account-modal__test-head">
                        <b>{item.customer_id}</b>
                        <em className={`vip-badge vip-badge--${item.vip_level.toLowerCase()}`}>{vipLabels[item.vip_level] ?? item.vip_level}</em>
                      </span>
                      <strong>{item.test_scenario}</strong>
                      <ul>{(item.test_hints ?? []).map((hint) => <li key={hint}>{hint}</li>)}</ul>
                    </button>
                  ))}
                </div>
              </section>
            )}
            <details className="account-modal__section account-modal__legacy" open={testAccounts.length === 0}>
              <summary>评测数据集客户（{datasetAccounts.length}）<small>订单多为 2026 年 6–8 月，大部分已超出售后时效</small></summary>
              <div className="account-modal__grid">
                {datasetAccounts.map((item) => (
                  <button key={item.customer_id} className={item.customer_id === customerId ? "active" : ""} onClick={() => void login(item.customer_id)} disabled={loggingIn !== null}>
                    <span className="avatar"><UserRound size={16} /></span>
                    <span><b>{item.customer_id}</b><small>{vipLabels[item.vip_level] ?? item.vip_level} · 历史退款 {item.refund_history_count} 次</small></span>
                  </button>
                ))}
              </div>
            </details>
            {loginError && <p className="account-modal__error">{loginError}</p>}
            {customers.length === 0 && <p className="muted account-modal__loading">正在加载账号列表…（若一直为空，说明后端未开启演示登录 DEMO_LOGIN_ENABLED）</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function NotFound() {
  return (
    <div className="page-container">
      <div className="empty-state">
        <b>页面不存在</b>
        <p>您访问的页面可能已下架或地址有误。</p>
        <Link to="/" className="btn btn--primary">返回首页</Link>
      </div>
    </div>
  );
}
