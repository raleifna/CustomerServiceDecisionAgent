"""Stream-sample real Amazon Reviews 2023 product metadata per Olist category,
applying the include/exclude keyword rules in category_mapping.yaml.

Products are streamed via HTTP Range requests against the public Hugging Face
dataset -- we never download a full category file (some are 10+ GB). For each
distinct Amazon top-level category referenced in the mapping, we grow the
read window until every Olist category mapped to it has at least
POOL_TARGET qualifying candidates (price present, keyword rules satisfied),
or we hit MAX_BYTES for that file.
"""

import json
import sys
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).parent
BASE_URL = (
    "https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/"
    "resolve/main/raw/meta_categories/meta_{amazon_category}.jsonl"
)
CHUNK_BYTES = 8 * 1024 * 1024  # 8MB per read step
MAX_BYTES = 200 * 1024 * 1024  # give up on a file after 200MB streamed
POOL_TARGET = 400  # candidates per Olist category is enough for later sampling


def load_rules():
    with open(HERE / "category_mapping.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def matches(text: str, include_kw: list[str], exclude_kw: list[str]) -> bool:
    if exclude_kw and any(kw in text for kw in exclude_kw):
        return False
    if not include_kw:
        return True
    return any(kw in text for kw in include_kw)


def product_text(row: dict) -> str:
    parts = [row.get("title") or ""]
    parts += row.get("features") or []
    cats = row.get("categories")
    if isinstance(cats, list):
        parts += cats
    return " ".join(parts).lower()


def stream_lines(url: str, max_bytes: int):
    """Yield complete JSON lines from a growing HTTP range read of url."""
    start = 0
    leftover = ""
    session = requests.Session()
    while start < max_bytes:
        end = start + CHUNK_BYTES - 1
        resp = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=30)
        if resp.status_code not in (200, 206):
            print(f"  ! HTTP {resp.status_code} at offset {start}, stopping", file=sys.stderr)
            return
        chunk = resp.content.decode("utf-8", errors="ignore")
        if not chunk:
            return
        text = leftover + chunk
        lines = text.split("\n")
        leftover = lines[-1]  # last line may be incomplete
        for line in lines[:-1]:
            if line.strip():
                yield line
        start += CHUNK_BYTES


def main():
    config = load_rules()
    categories = config["categories"]

    # Group Olist categories by which Amazon file they read from, so we only
    # stream each Amazon file once even if two Olist categories share it.
    by_amazon_file: dict[str, list[str]] = {}
    for olist_cat, rule in categories.items():
        by_amazon_file.setdefault(rule["amazon_category"], []).append(olist_cat)

    pools: dict[str, list[dict]] = {olist_cat: [] for olist_cat in categories}

    for amazon_category, olist_cats in by_amazon_file.items():
        url = BASE_URL.format(amazon_category=amazon_category)
        print(f"== streaming {amazon_category}.jsonl for {olist_cats} ==")
        bytes_read = 0
        for line in stream_lines(url, MAX_BYTES):
            bytes_read += len(line) + 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            price = row.get("price")
            if price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            text = product_text(row)
            for olist_cat in olist_cats:
                rule = categories[olist_cat]
                if len(pools[olist_cat]) >= POOL_TARGET:
                    continue
                if matches(text, rule.get("include_keywords") or [], rule.get("exclude_keywords") or []):
                    pools[olist_cat].append({
                        "title": row.get("title"),
                        "price_usd": price,
                        "amazon_category": amazon_category,
                        "main_category": row.get("main_category"),
                        "average_rating": row.get("average_rating"),
                        "image": (row.get("images") or [{}])[0].get("large")
                        if isinstance(row.get("images"), list) and row.get("images")
                        else None,
                        "parent_asin": row.get("parent_asin"),
                    })
            if all(len(pools[c]) >= POOL_TARGET for c in olist_cats):
                break
        for c in olist_cats:
            print(f"   {c}: {len(pools[c])} candidates ({bytes_read / 1e6:.1f}MB streamed)")

    out_path = HERE / "amazon_pools.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pools, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {out_path}")
    for c, items in pools.items():
        print(f"  {c}: {len(items)}")


if __name__ == "__main__":
    main()
