import { Search } from "lucide-react";

import { CATEGORIES, ProductCard } from "../api";
import { Breadcrumbs, EmptyState, Link, ProductTile } from "../components";
import { useFetch } from "../hooks";
import { navigate, useLocation } from "../router";

const SORTS = [
  { key: "", label: "综合" },
  { key: "sales", label: "销量" },
  { key: "rating", label: "好评" },
  { key: "price_asc", label: "价格 ↑" },
  { key: "price_desc", label: "价格 ↓" },
];

export default function ProductListPage() {
  const { query } = useLocation();
  const category = query.get("category") ?? "";
  const q = query.get("q") ?? "";
  const sort = query.get("sort") ?? "";
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (q) params.set("q", q);
  if (sort) params.set("sort", sort);
  const path = `/products${params.toString() ? `?${params}` : ""}`;
  const { data, loading, error } = useFetch<ProductCard[]>(path);

  function update(patch: Record<string, string>) {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([key, value]) => (value ? next.set(key, value) : next.delete(key)));
    navigate(`/search${next.toString() ? `?${next}` : ""}`, { replace: true });
  }

  const title = q ? `“${q}” 的搜索结果` : category || "全部商品";

  return (
    <div className="page-container listing">
      <Breadcrumbs items={[{ label: "首页", to: "/" }, { label: "全部商品", to: "/search" }, ...(category ? [{ label: category }] : []), ...(q ? [{ label: `搜索：${q}` }] : [])]} />

      <section className="filter-panel">
        <div className="filter-row">
          <span className="filter-row__label">分类</span>
          <div className="filter-row__options">
            <button className={!category ? "active" : ""} onClick={() => update({ category: "" })}>全部</button>
            {CATEGORIES.map((item) => (
              <button key={item} className={category === item ? "active" : ""} onClick={() => update({ category: item })}>{item}</button>
            ))}
          </div>
        </div>
        {q && (
          <div className="filter-row">
            <span className="filter-row__label">关键词</span>
            <div className="filter-row__options">
              <span className="filter-chip">{q}<button onClick={() => update({ q: "" })} aria-label="清除关键词">×</button></span>
            </div>
          </div>
        )}
      </section>

      <div className="sort-bar">
        <h1>{title}</h1>
        <div className="sort-bar__options">
          {SORTS.map((item) => (
            <button key={item.key} className={sort === item.key ? "active" : ""} onClick={() => update({ sort: item.key })}>{item.label}</button>
          ))}
        </div>
        <span className="muted">共 {data?.length ?? 0} 件商品</span>
      </div>

      {error && <EmptyState title="商品加载失败" hint={error} />}
      {!error && !loading && data?.length === 0 && (
        <EmptyState
          icon={<Search size={26} />}
          title="没有找到相关商品"
          hint="换个关键词试试，或者看看其他分类"
          action={<Link to="/search" className="btn btn--ghost">查看全部商品</Link>}
        />
      )}
      <div className="product-grid">
        {(data ?? []).map((product) => <ProductTile key={product.product_id} product={product} />)}
        {loading && !data && Array.from({ length: 10 }, (_, index) => <div key={index} className="product-tile product-tile--skeleton" />)}
      </div>
    </div>
  );
}
