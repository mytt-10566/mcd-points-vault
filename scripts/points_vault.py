#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦积分钱庄 (McPoints Vault) —— 积分与券资产估值引擎.

把麦当劳会员账户里的「积分 + 优惠券」当成一份资产组合来管理：
  1. 估值：把积分折算成人民币现值，算出账面总资产
  2. 排序：算出每一项兑换的「每 100 积分兑现价值」，找出现行最划算的出口
  3. 排险：找出即将过期的积分，量化「不管它就蒸发多少钱」
  4. 处方：用贪心算法生成一份能在过期前执行完的兑换路径

设计约束（刻意为之）：
  * 只依赖 Python 标准库，任何人 clone 下来就能跑，无需 pip install
  * 不发起任何网络请求，不读取任何密钥，所有输入由调用方以 JSON 提供
  * 输出为纯 Markdown，便于直接截图分享

用法：
    python3 points_vault.py --demo                      # 零配置演示
    python3 points_vault.py --input account.json        # 用自己的数据
    python3 points_vault.py --input account.json --json # 机器可读输出
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# --------------------------------------------------------------------------
# 常量：风险分档
# --------------------------------------------------------------------------

RISK_URGENT_DAYS = 7  # 7 天内到期 -> 紧急
RISK_WATCH_DAYS = 30  # 30 天内到期 -> 注意
WINDOW_DAYS = 30  # 「近期过期积分」的统计窗口

RISK_LABEL = {
    "urgent": "紧急",
    "watch": "注意",
    "safe": "安全",
}


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------


def parse_date(raw: Any) -> date | None:
    """尽最大努力解析各种日期写法，解析不了就返回 None（而不是抛错）。"""
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip().replace("/", "-").replace(".", "-")
    # 只取日期部分，丢掉可能带上的时间
    text = text.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%y-%m-%d", "%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%m-%d":  # 只有月日时，补上今年
            parsed = parsed.replace(year=date.today().year)
        return parsed.date()
    return None


def to_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


@dataclass
class Product:
    """麦麦商城里一个可用积分兑换的商品。"""

    name: str
    points: int
    cash_value: float
    valid_days: int | None = None
    kind: str = "其他"
    note: str = ""

    @property
    def value_per_100(self) -> float:
        """每 100 积分能兑现多少人民币。这是全篇的核心排序指标。"""
        if self.points <= 0:
            return 0.0
        return self.cash_value / self.points * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "points": self.points,
            "cash_value": round(self.cash_value, 2),
            "value_per_100": round(self.value_per_100, 2),
            "valid_days": self.valid_days,
            "kind": self.kind,
        }


@dataclass
class Coupon:
    """账户里的一张优惠券。"""

    name: str
    cash_value: float
    min_spend: float = 0.0
    expire_date: date | None = None

    def risk(self, today: date) -> str:
        if self.expire_date is None:
            return "safe"
        return classify_days((self.expire_date - today).days)

    def as_dict(self, today: date) -> dict[str, Any]:
        return {
            "name": self.name,
            "cash_value": round(self.cash_value, 2),
            "min_spend": round(self.min_spend, 2),
            "expire_date": self.expire_date.isoformat() if self.expire_date else None,
            "days_left": (self.expire_date - today).days if self.expire_date else None,
            "risk": self.risk(today),
        }


@dataclass
class ExpiryBucket:
    """一笔即将过期的积分。"""

    points: int
    expire_date: date

    def days_left(self, today: date) -> int:
        return (self.expire_date - today).days

    def risk(self, today: date) -> str:
        return classify_days(self.days_left(today))


@dataclass
class Vault:
    """一份完整的输入快照。"""

    today: date
    available_points: int = 0
    accumulated_points: int = 0
    frozen_points: int = 0
    expiring: list[ExpiryBucket] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    coupons: list[Coupon] = field(default_factory=list)
    recent_redemptions: list[str] = field(default_factory=list)

    # -- 派生指标 ---------------------------------------------------------

    @property
    def best_value_per_100(self) -> float:
        """当前所有可行兑换里最高的每 100 积分价值，即积分的「最优汇率」。"""
        if not self.products:
            return 0.0
        return max(p.value_per_100 for p in self.products)

    @property
    def points_book_value(self) -> float:
        """可用积分按最优汇率折算的账面价值。"""
        return self.available_points * self.best_value_per_100 / 100.0

    @property
    def coupon_value(self) -> float:
        return sum(c.cash_value for c in self.coupons)

    @property
    def total_value(self) -> float:
        return self.points_book_value + self.coupon_value

    def expiring_within(self, days: int) -> list[ExpiryBucket]:
        return [b for b in self.expiring if 0 <= b.days_left(self.today) <= days]

    @property
    def at_risk_points(self) -> int:
        """30 天内会过期的积分总额。"""
        return sum(b.points for b in self.expiring_within(WINDOW_DAYS))

    @property
    def at_risk_value(self) -> float:
        """这些积分如果不管，会蒸发掉多少钱。全篇最刺眼的数字。"""
        return self.at_risk_points * self.best_value_per_100 / 100.0

    @property
    def expired_points(self) -> int:
        return sum(b.points for b in self.expiring if b.days_left(self.today) < 0)


def classify_days(days_left: int) -> str:
    if days_left < 0:
        return "urgent"
    if days_left <= RISK_URGENT_DAYS:
        return "urgent"
    if days_left <= RISK_WATCH_DAYS:
        return "watch"
    return "safe"


# --------------------------------------------------------------------------
# 输入解析
# --------------------------------------------------------------------------


def _first_key(mapping: dict[str, Any], *candidates: str, default: Any = None) -> Any:
    """容忍 MCP 返回字段命名的细微差异。"""
    for name in candidates:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def build_vault(payload: dict[str, Any], today: date | None = None) -> Vault:
    today = today or parse_date(_first_key(payload, "now", "today", "current_time")) or date.today()

    acct = _first_key(payload, "account", "points_account", "my_account", default={}) or {}
    raw_expiring = _first_key(
        acct, "expiring", "expiring_points", "about_to_expire", default=[]
    ) or []

    # 兼容两种写法：列表 [{"points":..,"expire_date":..}] 或纯数字总额
    buckets: list[ExpiryBucket] = []
    if isinstance(raw_expiring, (int, float)):
        buckets.append(ExpiryBucket(points=to_int(raw_expiring), expire_date=today))
    else:
        for item in raw_expiring:
            if not isinstance(item, dict):
                continue
            pts = to_int(_first_key(item, "points", "amount", "value"))
            exp = parse_date(_first_key(item, "expire_date", "expiry", "deadline", "date"))
            if pts > 0 and exp:
                buckets.append(ExpiryBucket(points=pts, expire_date=exp))

    products: list[Product] = []
    for item in _first_key(payload, "mall_products", "products", "goods", default=[]) or []:
        if not isinstance(item, dict):
            continue
        products.append(
            Product(
                name=str(_first_key(item, "name", "title", "product_name", default="未命名")),
                points=to_int(_first_key(item, "points", "point_cost", "price_points", "cost")),
                cash_value=to_float(
                    _first_key(item, "cash_value", "value", "market_price", "reference_value")
                ),
                valid_days=(
                    to_int(_first_key(item, "valid_days", "validity_days"))
                    if _first_key(item, "valid_days", "validity_days") is not None
                    else None
                ),
                kind=str(_first_key(item, "kind", "type", "category", default="其他")),
                note=str(_first_key(item, "note", "description", default="")),
            )
        )
    products = [p for p in products if p.points > 0]

    coupons: list[Coupon] = []
    for item in _first_key(payload, "coupons", "my_coupons", default=[]) or []:
        if not isinstance(item, dict):
            continue
        coupons.append(
            Coupon(
                name=str(_first_key(item, "name", "title", default="未命名优惠券")),
                cash_value=to_float(
                    _first_key(item, "cash_value", "value", "discount", "face_value")
                ),
                min_spend=to_float(_first_key(item, "min_spend", "threshold", "min_amount")),
                expire_date=parse_date(
                    _first_key(item, "expire_date", "expiry", "deadline", "end_date")
                ),
            )
        )

    return Vault(
        today=today,
        available_points=to_int(
            _first_key(acct, "available_points", "available", "points", "balance")
        ),
        accumulated_points=to_int(_first_key(acct, "accumulated_points", "accumulated", "total")),
        frozen_points=to_int(_first_key(acct, "frozen_points", "frozen")),
        expiring=buckets,
        products=products,
        coupons=coupons,
        recent_redemptions=[
            str(x) for x in (_first_key(payload, "recent_redemptions", default=[]) or [])
        ],
    )


# --------------------------------------------------------------------------
# 处方：贪心兑换路径
# --------------------------------------------------------------------------


def build_rescue_plan(need_points: int, products: Iterable[Product]) -> list[dict[str, Any]]:
    """用最少的「浪费」覆盖即将过期的积分。

    策略：按每 100 积分价值从高到低贪心取用，每次尽可能拿满还能付得起的数量。
    不做全局最优（那是背包问题，对用户来说是过度设计），但保证：
      * 优先消耗兑换效率最高的商品
      * 不推荐超出需求的兑换量
    """
    plan: list[dict[str, Any]] = []
    remaining = need_points
    if remaining <= 0:
        return plan

    for product in sorted(products, key=lambda p: p.value_per_100, reverse=True):
        if remaining < product.points:
            continue
        qty = max(1, remaining // product.points)
        plan.append(
            {
                "name": product.name,
                "qty": qty,
                "points_each": product.points,
                "points_total": product.points * qty,
                "value_each": round(product.cash_value, 2),
                "value_total": round(product.cash_value * qty, 2),
                "value_per_100": round(product.value_per_100, 2),
            }
        )
        remaining -= product.points * qty
        if remaining <= 0:
            break

    return plan


# --------------------------------------------------------------------------
# 报告渲染
# --------------------------------------------------------------------------


def money(value: float) -> str:
    return f"¥{value:,.2f}"


def render_markdown(vault: Vault) -> str:
    lines: list[str] = []
    add = lines.append

    add("# 麦麦积分钱庄 · 资产体检报告")
    add("")
    add(f"报告日期：{vault.today.isoformat()}")
    add("")

    # ---- 一句话结论 -----------------------------------------------------
    add("## 一句话结论")
    add("")
    if vault.expired_points > 0:
        add(
            f"> 已有 **{vault.expired_points:,} 积分** 过期作废。"
            f"当前账户仍有 **{money(vault.total_value)}** 的可用资产。"
        )
    elif vault.at_risk_points > 0:
        add(
            f"> 你的麦麦账户现值 **{money(vault.total_value)}**，"
            f"其中 **{vault.at_risk_points:,} 积分**（约 {money(vault.at_risk_value)}）"
            f"将在 {WINDOW_DAYS} 天内过期 —— 不管它就真的没了。"
        )
    else:
        add(
            f"> 你的麦麦账户现值 **{money(vault.total_value)}**，"
            f"暂无积分临近过期。资产状态健康。"
        )
    add("")

    # ---- 资产总览 -------------------------------------------------------
    add("## 资产总览")
    add("")
    add("| 项目 | 数值 | 说明 |")
    add("| --- | ---: | --- |")
    add(f"| 可用积分 | {vault.available_points:,} | 可随时兑换 |")
    if vault.frozen_points:
        add(f"| 冻结积分 | {vault.frozen_points:,} | 暂不可用 |")
    if vault.accumulated_points:
        add(f"| 累计积分 | {vault.accumulated_points:,} | 历史总量 |")
    add(f"| 积分最优汇率 | {vault.best_value_per_100:.2f} 元/100分 | 现行最划算的兑换出口 |")
    add(f"| 积分账面价值 | {money(vault.points_book_value)} | 按最优汇率折算 |")
    add(f"| 券面总值 | {money(vault.coupon_value)} | {len(vault.coupons)} 张券 |")
    add(f"| **资产合计** | **{money(vault.total_value)}** | 积分 + 券 |")
    if vault.at_risk_points:
        add(f"| ⚠️ 风险敞口 | {money(vault.at_risk_value)} | {WINDOW_DAYS} 天内过期部分 |")
    add("")

    # ---- 过期排险 -------------------------------------------------------
    add("## 过期排险")
    add("")
    if not vault.expiring:
        add("暂无即将过期的积分。")
        add("")
    else:
        add("| 到期日 | 剩余天数 | 积分 | 约值 | 状态 |")
        add("| --- | ---: | ---: | ---: | --- |")
        for bucket in sorted(vault.expiring, key=lambda b: b.expire_date):
            days = bucket.days_left(vault.today)
            risk = bucket.risk(vault.today)
            value = bucket.points * vault.best_value_per_100 / 100.0
            days_text = "已过期" if days < 0 else f"{days} 天"
            add(
                f"| {bucket.expire_date.isoformat()} | {days_text} | "
                f"{bucket.points:,} | {money(value)} | {RISK_LABEL[risk]} |"
            )
        add("")

        plan = build_rescue_plan(vault.at_risk_points, vault.products)
        if plan:
            covered = sum(item["points_total"] for item in plan)
            gained = sum(item["value_total"] for item in plan)
            add("### 建议兑换路径")
            add("")
            add(
                f"用 **{covered:,} 积分** 换回 **{money(gained)}** 的实物/权益，"
                f"覆盖 {WINDOW_DAYS} 天内的过期敞口："
            )
            add("")
            add("| 兑换项 | 数量 | 消耗积分 | 换回价值 | 效率 |")
            add("| --- | ---: | ---: | ---: | ---: |")
            for item in plan:
                add(
                    f"| {item['name']} | ×{item['qty']} | {item['points_total']:,} | "
                    f"{money(item['value_total'])} | {item['value_per_100']:.2f} 元/100分 |"
                )
            add("")
            leftover = vault.at_risk_points - covered
            if leftover > 0:
                add(
                    f"> 仍有 **{leftover:,} 积分** 无法用现有商品覆盖，"
                    f"建议等商城上新，或先用 `auto-bind-coupons` 领券锁住权益。"
                )
                add("")
        elif vault.at_risk_points > 0:
            add("> 当前商城没有可用积分兑换的商品，建议先用 `auto-bind-coupons` 领券兜底。")
            add("")

    # ---- 兑换效率排行 ---------------------------------------------------
    add("## 积分变现效率排行")
    add("")
    if not vault.products:
        add("未获取到商城商品数据，请先调用 `mall-points-products`。")
        add("")
    else:
        add("每 100 积分能换回多少人民币 —— 数字越大越划算，优先从这里出手。")
        add("")
        add("| 排名 | 兑换项 | 所需积分 | 参考价值 | 每 100 积分兑 | 建议 |")
        add("| ---: | --- | ---: | ---: | ---: | --- |")
        ranked = sorted(vault.products, key=lambda p: p.value_per_100, reverse=True)
        best = ranked[0].value_per_100 if ranked else 0.0
        for index, product in enumerate(ranked, start=1):
            if product.value_per_100 >= best * 0.93:
                advice = "**优先兑换**"
            elif product.value_per_100 >= best * 0.72:
                advice = "值得考虑"
            else:
                advice = "暂缓"
            add(
                f"| {index} | {product.name} | {product.points:,} | "
                f"{money(product.cash_value)} | {product.value_per_100:.2f} 元 | {advice} |"
            )
        add("")

    # ---- 券到期提醒 -----------------------------------------------------
    if vault.coupons:
        add("## 券到期提醒")
        add("")
        add("| 券名 | 面值 | 门槛 | 到期日 | 剩余天数 | 状态 |")
        add("| --- | ---: | ---: | --- | ---: | --- |")
        for coupon in sorted(
            vault.coupons, key=lambda c: (c.expire_date or date.max)
        ):
            days = (coupon.expire_date - vault.today).days if coupon.expire_date else None
            risk = coupon.risk(vault.today)
            add(
                f"| {coupon.name} | {money(coupon.cash_value)} | "
                f"{'满 ' + money(coupon.min_spend) if coupon.min_spend else '无门槛'} | "
                f"{coupon.expire_date.isoformat() if coupon.expire_date else '—'} | "
                f"{days if days is not None else '—'} | {RISK_LABEL[risk]} |"
            )
        add("")

    add("---")
    add("")
    add("*本报告由「麦麦积分钱庄」自动生成，数据来自麦当劳 MCP 实时返回结果。*")
    add("")
    add(
        "*积分价值为参考估算，不构成投资或消费建议；"
        "商品、价格与库存请以麦当劳官方渠道为准。*"
    )

    return "\n".join(lines)


def render_json(vault: Vault) -> str:
    payload = {
        "report_date": vault.today.isoformat(),
        "summary": {
            "available_points": vault.available_points,
            "frozen_points": vault.frozen_points,
            "accumulated_points": vault.accumulated_points,
            "best_value_per_100": round(vault.best_value_per_100, 2),
            "points_book_value": round(vault.points_book_value, 2),
            "coupon_value": round(vault.coupon_value, 2),
            "total_value": round(vault.total_value, 2),
            "at_risk_points": vault.at_risk_points,
            "at_risk_value": round(vault.at_risk_value, 2),
            "expired_points": vault.expired_points,
        },
        "expiring": [
            {
                "points": b.points,
                "expire_date": b.expire_date.isoformat(),
                "days_left": b.days_left(vault.today),
                "risk": b.risk(vault.today),
                "value": round(b.points * vault.best_value_per_100 / 100.0, 2),
            }
            for b in sorted(vault.expiring, key=lambda b: b.expire_date)
        ],
        "rescue_plan": build_rescue_plan(vault.at_risk_points, vault.products),
        "products": [
            p.as_dict()
            for p in sorted(vault.products, key=lambda p: p.value_per_100, reverse=True)
        ],
        "coupons": [c.as_dict(vault.today) for c in vault.coupons],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def load_payload(path: str | None) -> dict[str, Any]:
    if path == "-" or path is None:
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def demo_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, os.pardir, "examples", "demo-account.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="points_vault",
        description="麦麦积分钱庄 · 积分与券资产估值引擎",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", "-i", help="输入 JSON 文件路径，'-' 表示从 stdin 读取")
    source.add_argument("--demo", action="store_true", help="使用内置示例数据运行")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    parser.add_argument(
        "--today",
        help="覆盖「今天」的日期（YYYY-MM-DD），用于复现历史报告",
    )
    args = parser.parse_args(argv)

    use_demo = args.demo or (not args.input and sys.stdin.isatty())

    try:
        if use_demo:
            with open(demo_path(), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = load_payload(args.input)
    except FileNotFoundError:
        print(f"找不到输入文件：{args.input or demo_path()}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"输入不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    if args.today and isinstance(payload, dict):
        payload["now"] = args.today

    vault = build_vault(payload)
    print(render_json(vault) if args.json else render_markdown(vault))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
