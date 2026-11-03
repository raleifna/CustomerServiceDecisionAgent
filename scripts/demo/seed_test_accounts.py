"""Generate the demo test accounts used for hands-on chat testing.

The hand-built evaluation fixtures (data/mock_db/*.json) are pinned to
June-August 2026, while the running system judges every after-sales window
against the real date (BusinessClock). After a few weeks every fixture order
is outside the 7-day no-reason / 30-day quality windows, which makes them
poor for trying the chat by hand. This script writes a separate set of
accounts whose order dates are relative to *today*, one account per family
of scenarios, into data/mock_db/test_accounts/ -- an overlay that
mock_db_repository.load_table() appends after the fixture rows.

Ids are fixed (customers CUS0101-CUS0107, orders ORD99<customer><seq>), so
re-running only refreshes the dates. Re-run it whenever the dates have gone
stale; the docker backend mounts ./data, so no rebuild is needed:

    python scripts/demo/seed_test_accounts.py              # dates relative to today
    python scripts/demo/seed_test_accounts.py --today 2026-11-03

After-sales requests created while testing live in PostgreSQL, keyed by
these stable order ids; to try a scenario again from scratch, reset the
database with `docker compose down -v`.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "mock_db" / "test_accounts"


@dataclass
class Item:
    product_id: str
    sku_id: str
    sku_name: str
    unit_price: float
    quantity: int = 1
    discount: float = 0.0
    shipped_product_id: str | None = None  # defaults to product_id once shipped
    shipped_quantity: int | None = None    # defaults to quantity once shipped


@dataclass
class Order:
    seq: int
    items: list[Item]
    # Day offsets relative to today (negative = in the past).
    ordered: int
    promised_ship: int | None = None
    shipped: int | None = None
    estimated_delivery: int | None = None
    delivered: int | None = None
    carrier: str = "顺丰速运"
    exception: str | None = None           # unresolved logistics exception, if any


@dataclass
class Account:
    number: int
    title: str
    vip_level: str
    registered_days_ago: int
    refund_history_count: int
    orders: list[Order]
    hints: list[str] = field(default_factory=list)

    @property
    def customer_id(self) -> str:
        return f"CUS{self.number:04d}"

    def order_id(self, seq: int) -> str:
        return f"ORD99{self.number:04d}{seq:04d}"


ACCOUNTS = [
    Account(101, "新手体验：发货 / 物流 / 无理由退货", "Gold", 420, 0, [
        Order(1, [Item("PRO0001", "PRO0001-BLACK", "曜石黑 / 标准版", 699)], ordered=-1, promised_ship=1, estimated_delivery=4),
        Order(2, [Item("PRO0006", "PRO0006-WHITE-42", "云朵白 / 42码", 399)], ordered=-3, promised_ship=-2, shipped=-2, estimated_delivery=1, carrier="京东物流"),
        Order(3, [Item("PRO0011", "PRO0011-DEFAULT", "燕麦色 / M", 399)], ordered=-6, promised_ship=-5, shipped=-5, estimated_delivery=-3, delivered=-3),
    ], hints=[
        "待发货耳机订单：问“什么时候发货”，再回复“需要”催发货；或说“我不想要了，帮我退款”走未发货取消",
        "运输中跑步鞋订单：问“我的快递到哪了”",
        "3 天前签收的针织衫：说“我不想要了”，7 天内无理由退货自动批准",
    ]),
    Account(102, "质量问题 / 商品破损（需上传图片证据）", "Silver", 760, 1, [
        Order(1, [Item("PRO0009", "PRO0009-DEFAULT", "石墨灰 / 奶茶轴", 799)], ordered=-15, promised_ship=-14, shipped=-14, estimated_delivery=-12, delivered=-12),
        Order(2, [Item("PRO0008", "PRO0008-DEFAULT", "雾蓝 / 4 人份", 199)], ordered=-4, promised_ship=-3, shipped=-3, estimated_delivery=-1, delivered=-1),
        Order(3, [Item("PRO0015", "PRO0015-DEFAULT", "标准款 / 白", 799)], ordered=-23, promised_ship=-22, shipped=-22, estimated_delivery=-20, delivered=-20),
    ], hints=[
        "12 天前签收的键盘：说“键盘有几个键失灵了，质量有问题”，按提示上传图片证据",
        "昨天签收的陶瓷餐具（易碎品）：说“收到时碗碎了”，上传破损照片",
        "20 天前签收的净化器：说“我不想要了”，超过 7 天会被拒绝（对照组）",
    ]),
    Account(103, "物流异常 / 发货超时", "Normal", 300, 0, [
        Order(1, [Item("PRO0002", "PRO0002-DEFAULT", "标准款 / 银色", 1299)], ordered=-9, promised_ship=-8, shipped=-8, estimated_delivery=-4, carrier="京东物流", exception="物流停滞超72小时"),
        Order(2, [Item("PRO0005", "PRO0005-DEFAULT", "标准款 / 雪白", 459)], ordered=-5, promised_ship=-2, estimated_delivery=1),
    ], hints=[
        "咖啡机订单已超过预计送达且物流停滞：问“快递怎么一直不动”，或说“一直没收到货”",
        "循环扇订单已超过承诺发货时间仍未发货：问“怎么还不发货”",
    ]),
    Account(104, "大额订单 · 铂金会员（转人工）", "Platinum", 1600, 2, [
        Order(1, [Item("PRO0010", "PRO0010-DEFAULT", "经典黑 / 金扣", 12999)], ordered=-5, promised_ship=-4, shipped=-4, estimated_delivery=-2, delivered=-2),
        Order(2, [Item("PRO0016", "PRO0016-DEFAULT", "雅黑 / 32GB", 1099)], ordered=-7, promised_ship=-6, shipped=-6, estimated_delivery=-4, delivered=-4),
    ], hints=[
        "12999 元链条包：说“我不想要了”，大额申请会转人工，可到「人工工作台」处理",
        "阅读器订单：同样的无理由退货作对照，金额正常会自动批准",
    ]),
    Account(105, "风控：新注册 + 高频退款（转人工）", "Normal", 12, 12, [
        Order(1, [Item("PRO0012", "PRO0012-DEFAULT", "海盐蓝", 599)], ordered=-5, promised_ship=-4, shipped=-4, estimated_delivery=-2, delivered=-2),
    ], hints=[
        "注册 12 天、历史退款 12 次：对音箱订单说“我不想要了”，观察风控转人工",
    ]),
    Account(106, "多商品订单 / 定制商品", "Gold", 900, 1, [
        Order(1, [
            Item("PRO0001", "PRO0001-WHITE", "月光白 / 标准版", 699),
            Item("PRO0012", "PRO0012-DEFAULT", "海盐蓝", 599),
        ], ordered=-6, promised_ship=-5, shipped=-5, estimated_delivery=-3, delivered=-3),
        Order(2, [Item("PRO0018", "PRO0018-DEFAULT", "刻字款 / 银色", 499)], ordered=-5, promised_ship=-4, shipped=-4, estimated_delivery=-2, delivered=-2),
    ], hints=[
        "耳机 + 音箱的多商品订单：说“我想退掉音箱”，客服会让你确认要售后的商品",
        "定制刻字项链：说“我不想要了”，定制商品不支持无理由退货",
    ]),
    Account(107, "错发 / 少发", "Silver", 520, 0, [
        Order(1, [Item("PRO0013", "PRO0013-DEFAULT", "浅蓝 / 27", 329, shipped_product_id="PRO0003")], ordered=-5, promised_ship=-4, shipped=-4, estimated_delivery=-2, delivered=-2),
        Order(2, [Item("PRO0020", "PRO0020-DEFAULT", "经典风味 / 24 颗", 79, quantity=3, shipped_quantity=2)], ordered=-5, promised_ship=-4, shipped=-4, estimated_delivery=-2, delivered=-2),
    ], hints=[
        "买的牛仔裤收到了夹克：说“你们发错货了”",
        "冻干咖啡买了 3 盒只收到 2 盒：说“少发了一盒”",
    ]),
]

_TRACKING_PREFIX = {"顺丰速运": "SF", "京东物流": "JD", "中通快递": "ZTO", "圆通速递": "YTO"}


def _day(today: date, offset: int | None) -> str | None:
    return None if offset is None else (today + timedelta(days=offset)).isoformat()


def build(today: date) -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = {
        "customers.json": [], "orders.json": [], "order_items.json": [],
        "logistics.json": [], "logistics_tracking.json": [], "accounts_guide.json": [],
    }
    for account in ACCOUNTS:
        tables["customers.json"].append({
            "customer_id": account.customer_id,
            "vip_level": account.vip_level,
            "register_date": _day(today, -account.registered_days_ago),
            "refund_history_count": account.refund_history_count,
        })
        tables["accounts_guide.json"].append({
            "customer_id": account.customer_id,
            "title": account.title,
            "hints": account.hints,
        })
        for order in account.orders:
            order_id = account.order_id(order.seq)
            shipped = order.shipped is not None
            primary = order.items[0]
            item_rows = []
            for index, item in enumerate(order.items):
                suffix = chr(ord("A") + index) if len(order.items) > 1 else ""
                paid = round(item.unit_price * item.quantity - item.discount, 2)
                item_rows.append({
                    "order_item_id": f"OI{order_id[3:]}{suffix}",
                    "order_id": order_id,
                    "product_id": item.product_id,
                    "sku_id": item.sku_id,
                    "sku_name": item.sku_name,
                    "unit_price": item.unit_price,
                    "quantity": item.quantity,
                    "discount_amount": item.discount,
                    "paid_amount": paid,
                    "shipped_product_id": (item.shipped_product_id or item.product_id) if shipped else None,
                    "shipped_quantity": (item.shipped_quantity if item.shipped_quantity is not None else item.quantity) if shipped else None,
                })
            tables["order_items.json"].extend(item_rows)
            tables["orders.json"].append({
                "order_id": order_id,
                "customer_id": account.customer_id,
                "product_id": primary.product_id,
                "quantity": sum(item.quantity for item in order.items),
                "shipped_product_id": item_rows[0]["shipped_product_id"],
                "shipped_quantity": sum(row["shipped_quantity"] for row in item_rows) if shipped else None,
                "order_date": _day(today, order.ordered),
                "promised_ship_date": _day(today, order.promised_ship),
                "shipped_date": _day(today, order.shipped),
                "delivered_date": _day(today, order.delivered),
                "delivery_recipient_type": "Self" if order.delivered is not None else None,
                "estimated_delivery_date": _day(today, order.estimated_delivery),
                "amount": round(sum(row["paid_amount"] for row in item_rows), 2),
                "payment_status": "Paid",
            })
            if order.exception:
                tables["logistics.json"].append({
                    "order_id": order_id,
                    "carrier": order.carrier,
                    "exception_type": order.exception,
                    "exception_recorded_date": _day(today, -2),
                    "resolved": False,
                })
            if shipped:
                tables["logistics_tracking.json"].append(_tracking(today, order_id, order))
    return tables


def _tracking(today: date, order_id: str, order: Order) -> dict:
    def at(offset: int, clock: str) -> str:
        return f"{_day(today, offset)} {clock}"

    checkpoints = [
        {"time": at(order.shipped, "09:20"), "location": "杭州转运中心", "description": "快件已由杭州转运中心发出"},
        {"time": at(order.shipped + 1, "06:45"), "location": "上海分拨中心", "description": "快件到达上海分拨中心"},
    ]
    if order.exception:
        checkpoints.append({
            "time": at(order.shipped + 2, "11:00"), "location": "上海分拨中心",
            "description": "快件离开上海分拨中心后超过72小时没有新的物流节点，平台已发起物流催查",
        })
        status = "异常处理中"
    elif order.delivered is not None:
        checkpoints += [
            {"time": at(order.delivered, "08:30"), "location": "徐汇区营业点", "description": "快件正在派送中，派送员：王师傅"},
            {"time": at(order.delivered, "15:12"), "location": "徐汇区营业点", "description": "快件已签收，签收人：本人"},
        ]
        status = "已签收"
    else:
        status = "运输中"
    prefix = _TRACKING_PREFIX.get(order.carrier, "SC")
    return {
        "order_id": order_id,
        "carrier": order.carrier,
        "tracking_number": f"{prefix}99{order_id[-8:]}",
        "current_status": status,
        "checkpoints": [cp for cp in checkpoints if cp["time"][:10] <= today.isoformat()],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(),
                        help="anchor date for the generated orders (default: today)")
    args = parser.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, rows in build(args.today).items():
        (OUTPUT_DIR / filename).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    print(f"已生成 {len(ACCOUNTS)} 个测试账号（日期基准 {args.today}）→ {OUTPUT_DIR.relative_to(PROJECT_ROOT)}")
    for account in ACCOUNTS:
        print(f"  {account.customer_id}  {account.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
