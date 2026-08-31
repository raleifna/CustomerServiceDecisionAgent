"""Stratified-sample real Olist orders per target category, with state
diversity (on-time / late / undelivered / canceled / multi-item / high-value),
then assign each sampled order_item a real Amazon product from amazon_pools.json
(built by fetch_amazon_pools.py) via the category's keyword-filtered pool.

Corrections applied per review round 2:
  1. Every timestamp is shifted by one constant offset so the whole imported
     timeline sits just before SCENARIO_AS_OF_TIME, instead of mixing real
     2016-2018 dates with a real "today". Relative deltas (ship time, lateness)
     are preserved exactly; only the absolute calendar position moves.
  2. "TRACKING_STALLED" is no longer asserted -- Olist has no continuous
     tracking feed, only milestone timestamps. The only exception we can
     honestly derive is "past estimated_delivery_date and still not
     delivered", tagged source_type=DERIVED. An order that is merely
     in-transit and not yet due gets no exception record at all.
  3. order_status keeps Olist's real value; refund_status is a separate,
     explicitly-synthetic field (not silently inferred as "Refunded").
  4. Amounts: convert the order's original-currency total once, and record
     rounding_adjustment = order_total - sum(item amounts) instead of letting
     independently-rounded item conversions silently drift from the total.

Corrections applied per review round 3:
  5. SCENARIO_AS_OF_TIME now anchors to the real wall-clock date the script is
     run on (not a hardcoded future date) so the imported timeline never lands
     after "today" -- a service request filed live during a demo still reads
     as happening after every imported order. In the running app this same
     role is meant to be played by a BusinessClock service (see migration
     notes), not scattered datetime.now() calls.
  6. Product-price sanity check: an Amazon candidate is only assigned to an
     Olist product if its CNY-converted price falls within a reasonable band
     of the real Olist transaction price for that product (0.3x-3x). If no
     pool candidate qualifies after retrying, the order is dropped rather
     than shipping an implausible price pairing (e.g. a "$569 USB cable").
  7. Customers are now aggregated by Olist's customer_unique_id (the real
     person), not orders.customer_id (a distinct pseudo-id Olist mints per
     order even for repeat customers) -- otherwise every order looked like a
     first-time customer and refund-history/VIP-tier logic had no real basis.

Output stays under data/external_raw/ as traceable staging files -- nothing
here is read by the running app. The Postgres migration is a separate step.
"""

import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

HERE = Path(__file__).parent
random.seed(20260831)

# note (round 4): the anchor is configurable so re-running the pipeline is
# reproducible instead of silently reshuffling every date each time it's run.
# ANCHOR_DATE_OVERRIDE=None means "use today" (convenient during iteration);
# once a batch is frozen for an actual demo, pin it here explicitly so the
# same import_batch_id always regenerates the same dates.
ANCHOR_DATE_OVERRIDE = date(2026, 8, 31)  # frozen for the v2.0 demo dataset
PIPELINE_VERSION = "v2.0"
IMPORT_BATCH_ID = "olist-amazon-2026-08-31-v2.0"

SCENARIO_AS_OF_TIME = ANCHOR_DATE_OVERRIDE or date.today()  # BusinessClock's role in production
PRICE_BAND_MIN, PRICE_BAND_MAX = 0.3, 3.0  # note 6: acceptable mapped-price / real-price ratio
BRL_TO_CNY = Decimal("1.35")   # approximate long-run average, not a live FX rate
USD_TO_CNY = Decimal("7.10")   # approximate, not a live FX rate

with open(HERE / "category_mapping.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

TIERS = CONFIG["sampling_tiers"]
DIVERSITY = CONFIG["diversity_buckets"]
TARGET_CATS = set(CONFIG["categories"])


def load_csv(name):
    with open(HERE / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_dt(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def cny(amount_decimal: Decimal) -> float:
    return float(amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def main():
    print("loading Olist CSVs...")
    with open(HERE / "product_category_name_translation.csv", encoding="utf-8-sig") as f:
        trans = {r["product_category_name"]: r["product_category_name_english"] for r in csv.DictReader(f)}

    products = load_csv("olist_products_dataset")
    prod_cat = {p["product_id"]: trans.get(p["product_category_name"], p["product_category_name"]) for p in products}

    # real Olist payment records -- note: an order can have more than one
    # (split payment across a voucher + a card, etc.); preserved as-is.
    PAYMENT_METHOD_CN = {
        "credit_card": "信用卡", "boleto": "银行转账", "voucher": "代金券",
        "debit_card": "借记卡", "not_defined": "未知",
    }
    payments_by_order = defaultdict(list)
    for p in load_csv("olist_order_payments_dataset"):
        payments_by_order[p["order_id"]].append(p)

    # note 7: customer_id is a per-order pseudo-id Olist mints even for a
    # repeat customer -- customer_unique_id is the real person.
    customer_unique = {c["customer_id"]: c["customer_unique_id"] for c in load_csv("olist_customers_dataset")}

    orders = load_csv("olist_orders_dataset")
    orders_by_id = {o["order_id"]: o for o in orders}

    order_items = load_csv("olist_order_items_dataset")
    items_by_order = defaultdict(list)
    for it in order_items:
        items_by_order[it["order_id"]].append(it)

    # --- build per-order aggregates, assign a primary target category ---
    order_aggs = []
    for order_id, items in items_by_order.items():
        order = orders_by_id.get(order_id)
        if order is None:
            continue
        cat_counts = defaultdict(int)
        for it in items:
            cat = prod_cat.get(it["product_id"])
            if cat in TARGET_CATS:
                cat_counts[cat] += 1
        if not cat_counts:
            continue
        primary_cat = max(cat_counts, key=cat_counts.get)

        total_amount_brl = sum(Decimal(it["price"]) for it in items)
        purchase_dt = parse_dt(order["order_purchase_timestamp"])
        carrier_dt = parse_dt(order["order_delivered_carrier_date"])
        delivered_dt = parse_dt(order["order_delivered_customer_date"])
        estimated_dt = parse_dt(order["order_estimated_delivery_date"])
        status = order["order_status"]  # real Olist field, kept as-is

        is_delivered = delivered_dt is not None
        is_late = bool(is_delivered and estimated_dt and delivered_dt > estimated_dt)
        is_canceled = status in ("canceled", "unavailable")
        is_undelivered_raw = (not is_delivered) and (not is_canceled)
        is_multi_item = len(items) > 1
        is_high_value = total_amount_brl >= 300

        order_aggs.append({
            "order_id": order_id,
            "customer_id": customer_unique.get(order["customer_id"], order["customer_id"]),
            "primary_category": primary_cat, "items": items,
            "total_amount_brl": total_amount_brl,
            "purchase_dt": purchase_dt, "carrier_dt": carrier_dt,
            "delivered_dt": delivered_dt, "estimated_dt": estimated_dt,
            "status": status, "is_delivered": is_delivered, "is_late": is_late,
            "is_canceled": is_canceled, "is_undelivered_raw": is_undelivered_raw,
            "is_multi_item": is_multi_item, "is_high_value": is_high_value,
        })

    print(f"candidate orders across target categories: {len(order_aggs)}")

    # --- stratified sample per category ---
    by_cat = defaultdict(list)
    for agg in order_aggs:
        by_cat[agg["primary_category"]].append(agg)

    def bucket_of(agg):
        if agg["is_canceled"]:
            return "canceled"
        if agg["is_undelivered_raw"]:
            return "in_transit_or_undelivered"
        if agg["is_late"]:
            return "delivered_late"
        return "delivered_on_time"

    selected = []
    for cat, pool in by_cat.items():
        rule = CONFIG["categories"][cat]
        target = TIERS[rule["tier"]]["target"]
        random.shuffle(pool)
        by_bucket = defaultdict(list)
        for agg in pool:
            by_bucket[bucket_of(agg)].append(agg)

        chosen_ids, chosen = set(), []
        for bucket_name in ("delivered_on_time", "delivered_late", "in_transit_or_undelivered", "canceled"):
            want = round(target * DIVERSITY.get(bucket_name, 0))
            for agg in by_bucket[bucket_name][:want]:
                if agg["order_id"] not in chosen_ids:
                    chosen.append(agg); chosen_ids.add(agg["order_id"])
        for flag, key in (("is_multi_item", "multi_item"), ("is_high_value", "high_value")):
            want = round(target * DIVERSITY.get(key, 0))
            extra = [a for a in pool if a[flag] and a["order_id"] not in chosen_ids]
            for agg in extra[:want]:
                chosen.append(agg); chosen_ids.add(agg["order_id"])
        for agg in [a for a in pool if a["order_id"] not in chosen_ids]:
            if len(chosen) >= target:
                break
            chosen.append(agg); chosen_ids.add(agg["order_id"])

        chosen = chosen[:target]
        selected.extend(chosen)
        print(f"  {cat}: pool={len(pool)} selected={len(chosen)}")

    print(f"\ntotal selected orders: {len(selected)}")

    # --- compute the single timeline shift: newest purchase in the sample
    # lands exactly SHIP_BUFFER_DAYS before SCENARIO_AS_OF_TIME, so even the
    # most recent order still reads as "a few days ago", not "today". ---
    # The shift must be anchored to the LATEST timestamp of any kind in the
    # sample (order/ship/deliver/estimate), not just purchase date -- a real
    # long-delivery order can have its delivered_date land after the anchor
    # even when its purchase date alone would look safely in the past.
    all_dts = []
    for a in selected:
        all_dts.extend(dt for dt in (a["purchase_dt"], a["carrier_dt"], a["delivered_dt"], a["estimated_dt"]) if dt)
    newest_any_timestamp = max(all_dts)
    SHIFT_DAYS = (SCENARIO_AS_OF_TIME - timedelta(days=3) - newest_any_timestamp.date()).days
    print(f"timeline shift: {SHIFT_DAYS} days (so newest order lands 3 days before scenario_as_of_time)")

    def shift(dt):
        if dt is None:
            return None
        return (dt + timedelta(days=SHIFT_DAYS)).date().isoformat()

    with open(HERE / "amazon_pools.json", encoding="utf-8") as f:
        amazon_pools = json.load(f)

    olist_to_pro_id = {}
    new_products = []
    product_counter = 0
    price_check_failures = 0

    def get_or_create_product(olist_product_id, category, reference_price_cny):
        """Return a PRO-RW id whose (converted) price is within
        [PRICE_BAND_MIN, PRICE_BAND_MAX] x the real Olist transaction price
        for this product, or None if no pool candidate qualifies -- note 6."""
        nonlocal product_counter, price_check_failures
        if olist_product_id in olist_to_pro_id:
            return olist_to_pro_id[olist_product_id]
        pool = amazon_pools.get(category) or []
        if not pool:
            return None
        candidates = []
        for item in pool:
            price_cny = float(Decimal(str(item["price_usd"])) * USD_TO_CNY)
            ratio = price_cny / float(reference_price_cny) if reference_price_cny else 0
            if PRICE_BAND_MIN <= ratio <= PRICE_BAND_MAX:
                candidates.append(item)
        if not candidates:
            price_check_failures += 1
            return None
        amazon_item = random.choice(candidates)
        product_counter += 1
        pro_id = f"PRO-RW-{product_counter:05d}"
        olist_to_pro_id[olist_product_id] = pro_id
        new_products.append({
            "product_id": pro_id,
            "category": category,
            "price": cny(Decimal(str(amazon_item["price_usd"])) * USD_TO_CNY),
            "is_fragile": category in ("bed_bath_table", "electronics", "computers_accessories"),
            "is_customized": False,
            "_source": {
                "olist_category": category,
                "amazon_title": amazon_item["title"],
                "amazon_category": amazon_item["amazon_category"],
                "amazon_parent_asin": amazon_item["parent_asin"],
                "mapping_method": "constrained_random_assignment_within_category",
                "price_conversion": "USD_to_CNY_approx_7.10",
                "reference_olist_price_cny": float(reference_price_cny),
                "price_band_applied": [PRICE_BAND_MIN, PRICE_BAND_MAX],
            },
        })
        return pro_id

    new_orders, new_order_items, new_logistics, new_payments, new_shipments = [], [], [], [], []
    payment_counter = 0
    customer_map = {}
    item_counter = 0
    dropped_orders = 0
    refund_status_choices = ["处理中", "已完成", "失败"]
    refund_status_weights = [20, 75, 5]

    for agg in selected:
        # -- note 6: resolve every distinct product in this order to an
        # Amazon candidate BEFORE committing anything; drop the whole order
        # if any item has no price-plausible match rather than shipping an
        # implausible pairing. --
        item_prices_brl = [Decimal(it["price"]) for it in agg["items"]]
        pro_ids_for_items = []
        order_ok = True
        for it, price_brl in zip(agg["items"], item_prices_brl):
            ref_price_cny = price_brl * BRL_TO_CNY
            pro_id = get_or_create_product(it["product_id"], agg["primary_category"], ref_price_cny)
            if pro_id is None:
                order_ok = False
                break
            pro_ids_for_items.append(pro_id)
        if not order_ok:
            dropped_orders += 1
            continue

        order_num = len(new_orders) + 1
        order_id = f"ORD-RW-{order_num:05d}"
        cust_key = agg["customer_id"]
        if cust_key not in customer_map:
            customer_map[cust_key] = f"CUS-RW-{len(customer_map) + 1:05d}"
        customer_id = customer_map[cust_key]

        first_pro_id = pro_ids_for_items[0]

        shipped_date = shift(agg["carrier_dt"])
        delivered_date = shift(agg["delivered_dt"])
        estimated_delivery_date = shift(agg["estimated_dt"])

        # order-level total: convert once from the original-currency sum
        order_total_cny = agg["total_amount_brl"] * BRL_TO_CNY

        # item-level: convert each item independently and round it to the
        # 2dp value that actually gets displayed on each line. The gap we
        # need to record is between the order-level total (rounded once,
        # from the unrounded original-currency sum) and the sum of these
        # already-rounded per-line display values -- that's the number a
        # customer could actually notice not adding up on screen.
        item_rows = []
        displayed_item_total = Decimal("0")
        for it in agg["items"]:
            unit_price_cny = Decimal(it["price"]) * BRL_TO_CNY
            displayed_price = Decimal(str(cny(unit_price_cny)))
            displayed_item_total += displayed_price
            item_rows.append(unit_price_cny)
        rounding_adjustment = round(cny(order_total_cny) - float(displayed_item_total), 2)

        # -- shipments: every order that was ever shipped (or is a canceled/
        # unavailable order with no shipment at all) gets exactly one row,
        # not just the ones with an exception -- otherwise "non-canceled,
        # shipped orders must have a shipment record" has no way to hold. --
        is_overdue_undelivered = (
            agg["is_undelivered_raw"]
            and estimated_delivery_date is not None
            and estimated_delivery_date < SCENARIO_AS_OF_TIME.isoformat()
        )
        if agg["is_canceled"]:
            shipment_status = "Canceled"
        elif delivered_date:
            shipment_status = "Delivered"
        elif is_overdue_undelivered:
            shipment_status = "Overdue"
        elif shipped_date:
            shipment_status = "InTransit"
        else:
            shipment_status = "Pending"  # never shipped, not (yet) canceled either

        shipment_id = f"SHP-RW-{order_num:05d}"
        new_shipments.append({
            "shipment_id": shipment_id, "order_id": order_id,
            "carrier": "第三方物流" if shipped_date else None,
            "shipped_at": shipped_date, "delivered_at": delivered_date,
            "estimated_delivery_date": estimated_delivery_date,
            "shipment_status": shipment_status,
        })

        # -- point 2: only a real, derivable exception, never an asserted
        # "stalled 72h" claim we have no tracking data to support --
        if agg["is_late"] and delivered_date and estimated_delivery_date:
            new_logistics.append({
                "shipment_id": shipment_id, "order_id": order_id, "carrier": "第三方物流",
                "exception_type": "派送延迟",
                "exception_recorded_date": estimated_delivery_date,
                "resolved": True,
                "source_type": "DERIVED",
                "derivation": "delivered_date > estimated_delivery_date (both real Olist fields)",
            })
        elif is_overdue_undelivered:
            new_logistics.append({
                "shipment_id": shipment_id, "order_id": order_id, "carrier": "第三方物流",
                "exception_type": "超过预计送达时间仍未签收",
                "exception_recorded_date": estimated_delivery_date,
                "resolved": False,
                "source_type": "DERIVED",
                "derivation": "estimated_delivery_date has passed scenario_as_of_time with no delivered_date; "
                              "Olist provides no continuous tracking feed, so a specific stall duration "
                              "(e.g. '72 hours') is NOT asserted",
            })
        # else: legitimately still in transit and not yet due -- no exception recorded

        # -- point 3: order_status stays Olist's real value; refund_status is
        # explicitly synthetic and independent of it --
        order_status = agg["status"]
        refund_status = None
        if order_status in ("canceled", "unavailable"):
            refund_status = random.choices(refund_status_choices, weights=refund_status_weights)[0]

        new_orders.append({
            "order_id": order_id, "customer_id": customer_id,
            "product_id": first_pro_id, "quantity": len(agg["items"]),
            "shipped_product_id": first_pro_id if shipped_date else None,
            "shipped_quantity": len(agg["items"]) if shipped_date else None,
            "order_date": shift(agg["purchase_dt"]),
            "shipped_date": shipped_date, "delivered_date": delivered_date,
            "delivery_recipient_type": "Unknown",
            "estimated_delivery_date": estimated_delivery_date,
            "amount": cny(order_total_cny),
            "rounding_adjustment": rounding_adjustment,
            "order_status": order_status,        # real Olist field
            "refund_status": refund_status,       # synthetic, only set when canceled/unavailable
            "payment_status": "Paid",             # payment itself was made; refund is tracked separately
        })

        for p in payments_by_order.get(agg["order_id"], []):
            payment_counter += 1
            new_payments.append({
                "payment_id": f"PAY-RW-{payment_counter:06d}",
                "order_id": order_id,
                "amount": cny(Decimal(p["payment_value"]) * BRL_TO_CNY),
                "currency": "CNY",
                "payment_method": PAYMENT_METHOD_CN.get(p["payment_type"], p["payment_type"]),
                "payment_installments": int(p["payment_installments"]),
                "paid_at": shift(agg["purchase_dt"]),
            })

        for it, unit_price_cny, pro_id in zip(agg["items"], item_rows, pro_ids_for_items):
            item_counter += 1
            price = cny(unit_price_cny)
            new_order_items.append({
                "order_item_id": f"OI-RW-{item_counter:06d}", "order_id": order_id,
                "product_id": pro_id, "sku_id": f"{pro_id}-DEFAULT", "sku_name": "默认规格",
                "unit_price": price, "quantity": 1, "discount_amount": 0,
                "paid_amount": price,
                "shipped_product_id": pro_id if shipped_date else None,
                "shipped_quantity": 1 if shipped_date else None,
            })

    # note (round 4): a canceled order is NOT evidence a refund was
    # requested or paid out -- Olist's cancellation status proves nothing
    # about refund outcome. This is renamed to canceled_order_count and kept
    # DERIVED; the true refund_request_count / successful_refund_count /
    # refund_amount_total belong on the after_sales_requests/refunds tables
    # in the runtime schema and must be computed from those, live -- never
    # pre-baked here. Conflating the two would make RISK-XXX misclassify any
    # customer who ever canceled an order as a "high-frequency refunder".
    cancels_by_customer = defaultdict(int)
    for o in new_orders:
        if o["order_status"] in ("canceled", "unavailable"):
            cancels_by_customer[o["customer_id"]] += 1

    new_customers = [
        {
            "customer_id": cus_id,
            "source_unique_id": olist_customer_unique_id,  # real Olist customer_unique_id -- traceability link
            "vip_level": random.choices(
                ["Bronze", "Silver", "Gold", "Platinum"], weights=[55, 25, 15, 5]
            )[0],
            "register_date": None,  # Olist has no account-registration field; left null, not fabricated
            "canceled_order_count": cancels_by_customer.get(cus_id, 0),
            "_source": {
                "vip_level": "SYNTHETIC_ENRICHMENT",
                "canceled_order_count": "DERIVED",
                "source_unique_id": "REAL",
                "note": "refund_request_count/successful_refund_count/refund_amount_total are NOT "
                        "stored here -- they must be computed live from after_sales_requests/refunds "
                        "in the runtime schema, not pre-baked on the customer record.",
            },
        }
        for olist_customer_unique_id, cus_id in customer_map.items()
    ]
    repeat_customers = sum(1 for cus_id in customer_map.values()
                            if sum(1 for o in new_orders if o["customer_id"] == cus_id) > 1)

    meta = {
        "scenario_as_of_time": SCENARIO_AS_OF_TIME.isoformat(),
        "timeline_shift_days": SHIFT_DAYS,
        "brl_to_cny_rate": str(BRL_TO_CNY),
        "usd_to_cny_rate": str(USD_TO_CNY),
        "note": "All monetary rates are fixed approximations for demo purposes, not live FX rates.",
        "dataset_anchor_date": SCENARIO_AS_OF_TIME.isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "import_batch_id": IMPORT_BATCH_ID,
        "orders_dropped_for_price_mismatch": dropped_orders,
        "price_check_failures": price_check_failures,
        "distinct_customers": len(customer_map),
        "repeat_customers_in_sample": repeat_customers,
    }

    out = {
        "new_orders": new_orders, "new_order_items": new_order_items,
        "new_products": new_products, "new_customers": new_customers,
        "new_logistics": new_logistics, "new_payments": new_payments,
        "new_shipments": new_shipments,
        "_meta": [meta],
    }
    for name, rows in out.items():
        path = HERE / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"wrote {path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
