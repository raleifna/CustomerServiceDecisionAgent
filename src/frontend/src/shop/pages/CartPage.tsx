import { Minus, Plus, ShoppingCart, Trash2 } from "lucide-react";
import { useMemo } from "react";

import { yuan } from "../api";
import { EmptyState, Link, Price, ProductTile } from "../components";
import { useCatalog } from "../hooks";
import { ProductImage } from "../ProductImage";
import { navigate } from "../router";
import { removeCartLines, setAllCartSelected, updateCartLine, useCart } from "../store";

export default function CartPage() {
  const cart = useCart();
  const catalog = useCatalog();
  const byId = useMemo(() => new Map(catalog.map((product) => [product.product_id, product])), [catalog]);

  const lines = cart.map((line) => ({ line, product: byId.get(line.product_id) }));
  const selected = lines.filter(({ line, product }) => line.selected && product);
  const total = selected.reduce((sum, { line, product }) => sum + product!.price * line.quantity, 0);
  const saved = selected.reduce((sum, { line, product }) => sum + Math.max(0, (product!.original_price ?? product!.price) - product!.price) * line.quantity, 0);
  const allSelected = cart.length > 0 && cart.every((line) => line.selected);
  const pieces = selected.reduce((sum, { line }) => sum + line.quantity, 0);

  if (cart.length === 0) {
    return (
      <div className="page-container">
        <h1 className="page-title">购物车</h1>
        <EmptyState icon={<ShoppingCart size={26} />} title="购物车空空如也" hint="去挑几件喜欢的商品吧" action={<Link to="/" className="btn btn--primary">去逛逛</Link>} />
        <h2 className="section-title">猜你喜欢</h2>
        <div className="product-grid">{catalog.slice(0, 5).map((product) => <ProductTile key={product.product_id} product={product} />)}</div>
      </div>
    );
  }

  return (
    <div className="page-container cart">
      <h1 className="page-title">购物车<small>（全部 {cart.length}）</small></h1>
      <div className="cart-table">
        <div className="cart-row cart-row--head">
          <label className="checkbox"><input type="checkbox" checked={allSelected} onChange={(event) => setAllCartSelected(event.target.checked)} />全选</label>
          <span>商品信息</span><span>单价</span><span>数量</span><span>小计</span><span>操作</span>
        </div>
        {lines.map(({ line, product }) => (
          <div className="cart-row" key={line.sku_id}>
            <label className="checkbox"><input type="checkbox" checked={line.selected} onChange={(event) => updateCartLine(line.sku_id, { selected: event.target.checked })} aria-label="选择商品" /></label>
            <div className="cart-row__product">
              <Link to={`/product/${line.product_id}`}><ProductImage product={product} size="sm" /></Link>
              <div>
                <Link to={`/product/${line.product_id}`} className="cart-row__title">{product?.title ?? line.product_id}</Link>
                <small>规格：{line.sku_name}</small>
              </div>
            </div>
            <span>{product ? <Price value={product.price} original={product.original_price} size="sm" /> : "—"}</span>
            <span className="stepper">
              <button onClick={() => updateCartLine(line.sku_id, { quantity: Math.max(1, line.quantity - 1) })} disabled={line.quantity <= 1} aria-label="减少"><Minus size={12} /></button>
              <input value={line.quantity} onChange={(event) => updateCartLine(line.sku_id, { quantity: Math.max(1, Math.min(99, Number(event.target.value.replace(/\D/g, "")) || 1)) })} aria-label="数量" />
              <button onClick={() => updateCartLine(line.sku_id, { quantity: Math.min(99, line.quantity + 1) })} aria-label="增加"><Plus size={12} /></button>
            </span>
            <b className="cart-row__subtotal">{product ? yuan(product.price * line.quantity) : "—"}</b>
            <button className="link-btn" onClick={() => removeCartLines([line.sku_id])}><Trash2 size={13} />删除</button>
          </div>
        ))}
      </div>

      <div className="cart-bar">
        <label className="checkbox"><input type="checkbox" checked={allSelected} onChange={(event) => setAllCartSelected(event.target.checked)} />全选</label>
        <button className="link-btn" onClick={() => removeCartLines(cart.filter((line) => line.selected).map((line) => line.sku_id))}>删除选中</button>
        <span className="cart-bar__spacer" />
        <span>已选 <b>{pieces}</b> 件</span>
        <span className="cart-bar__total">合计：<b>{yuan(total)}</b>{saved > 0 && <small>已优惠 {yuan(saved)}</small>}</span>
        <button className="btn btn--primary btn--lg" disabled={selected.length === 0} onClick={() => navigate("/checkout")}>去结算</button>
      </div>
    </div>
  );
}
