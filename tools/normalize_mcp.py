#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把麦当劳 MCP 的原始返回归一化成 points_vault.py 的输入格式。

为什么需要这一层
----------------
MCP 返回的是「给人看的」结构，和估值引擎需要的「可计算」结构差得很远，实测有三处硬差异：

  1. query-my-account 不返回带到期日的积分批次，只按月分桶
     （currentMouthExpirePoint / nextMouthExpirePoint / lastMouthExpirePoint）。
  2. mall-points-products 里积分商品的 price 恒为 "0"，现金参照价只出现在
     商品名（"21.9元巨无霸可乐组合"）和 mall-product-detail 的
     skuList[].extTradePrice 里 —— 而且那是「用券后你要付多少」，
     不是「你能省多少」。真正的价值 = 常规价 − 用券价。
  3. mall-points-products 会把已下架商品一并返回（status 恒为 2，不可用作在售判据），
     必须逐个调 mall-product-detail 才能确认是否还能兑换。

输入快照目录约定
----------------
    <snapshot>/
      query-my-account.json          # 必填
      mall-points-products.json      # 必填
      query-meals.json               # 可选，缺省则无法定价，只输出结构
      spu-status.json                # 可选，mall-product-detail 汇总的在售状态
      query-my-coupons.txt           # 可选，用户的券列表（MCP 返回的是 markdown 文本）

用法
----
    python3 tools/normalize_mcp.py --snapshot ./snapshot --now 2026-10-09 -o input.json
    python3 tools/normalize_mcp.py --snapshot ./snapshot --explain

只依赖标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# --------------------------------------------------------------------------
# 兑品名 -> 菜单单品名 的参照表
# MCP 的兑换说明里用的是「中可乐」「麦辣鸡翅-2块」这类内部叫法，
# 菜单里则是「可乐」「麦辣鸡翅」。这张表是两者的显式对照，
# 没有对照关系的一律不定价（宁可标缺失，也不猜）。
# --------------------------------------------------------------------------

NAME_ALIASES = {
    "中可乐": "可乐",
    "可乐": "可乐",
    "中薯条": "薯条",
    "薯条": "薯条",
    "菠萝派": "派",
    "香芋派": "派",
    "奶香大米派": "派",
    "麦乐鸡-4块": "麦乐鸡",
    "麦辣鸡翅-2块": "麦辣鸡翅",
    "麦辣鸡翅2块": "麦辣鸡翅",
    "麦辣鸡翅": "麦辣鸡翅",
    "麦辣鸡腿汉堡": "麦辣鸡腿汉堡",
    "板烧鸡腿堡": "板烧鸡腿堡",
    "巨无霸": "巨无霸",
    "那么大鸡排（椒盐风味）": "那么大鸡排（椒盐风味）",
    "麦咖啡™美式中杯": "麦咖啡™美式",
    "麦咖啡™奶铁中杯": "麦咖啡™奶铁",
}


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_menu_index(meals_payload):
    """菜单名 -> (常规价, 现价)。常规价是官方券面说明里「节省金额」的比较基准。"""
    index = {}
    if not meals_payload:
        return index
    meals = meals_payload.get("data", {}).get("meals", {})
    for meal in meals.values():
        name = meal.get("name")
        if not name:
            continue
        try:
            index[name] = (float(meal.get("originalPrice") or 0), float(meal.get("currentPrice") or 0))
        except (TypeError, ValueError):
            continue
    return index


def menu_lookup(index, label):
    """按参照表把券面叫法映射到菜单项。

    只认「显式对照表」和「菜单里完全同名」两种情况，刻意不做子串模糊匹配 ——
    实测中模糊匹配会把「可口可乐麦炫酷」错配成「可乐」，凭空造出一个不存在的省额。
    宁可返回 None，也不要一个看起来合理但错误的数字。
    返回 (菜单名, 常规价, 现价)。
    """
    canonical = NAME_ALIASES.get(label, label)
    if canonical in index:
        original, current = index[canonical]
        return canonical, original, current
    if label in index:
        original, current = index[label]
        return label, original, current
    return None


def parse_redemption(note):
    """从 mall-product-detail 的 note 里抽出「本券能换到什么」。

    分两种情况：
      a) 套餐券 —— 构成写在「含1份X+1份Y」里，只认这一段；
      b) 单品/任选券 —— 写成「凭本券可享受 N 元购买 A/B/C 任选1件」。
    """
    if not note:
        return None
    m = re.search(r"【兑换内容】[：:](.*?)(?:<br|</p|$)", note, re.S)
    if not m:
        return None
    text = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    combo = re.search(r"含\s*\d*\s*份?[^。；;，,]*", text)
    if combo and ("+" in combo.group(0) or "＋" in combo.group(0)):
        return combo.group(0).strip()

    m2 = re.search(r"(?:购买|购)\s*([^。；;]+)", text)
    if m2:
        return m2.group(1).strip()
    return None


def parse_components(text):
    """把兑换内容拆成若干「候选组合」，每个组合是若干单品。

    套券套餐 -> 一组：含1份巨无霸+1份中可乐        ==> [["巨无霸", "中可乐"]]
    任选券   -> 多组：中薯条/奥利奥麦旋风任选1件     ==> [["中薯条"], ["奥利奥麦旋风"]]
    """
    if not text:
        return []
    text = re.sub(r"任选\s*\d*\s*件?|二选一|任选", "", text).strip()
    if "+" in text or "＋" in text:
        parts = [p.strip() for p in re.split(r"[+＋]", text) if p.strip()]
        cleaned = []
        for p in parts:
            p = re.sub(r"^含?\s*\d*\s*份?", "", p).strip()
            p = re.sub(r"[，,。].*$", "", p).strip()  # 剪掉「，不可变更」这类尾句
            if p:
                cleaned.append(p)
        return [cleaned] if cleaned else []
    opts = [p.strip() for p in re.split(r"[/／]", text) if p.strip()]
    return [[o] for o in opts if o]


def best_reference(component_groups, menu_index):
    """在「任选」的多个组合里取对用户最有利的一个，返回 (常规价合计, 命中明细)。

    任选场景取最大是刻意的：报告给的是「这笔积分最多能换回多少」的上限口径，
    与引擎用「最优汇率」折算积分保持同一套逻辑。
    """
    best_total, best_detail = 0.0, []
    for group in component_groups:
        total, detail, ok = 0.0, [], True
        for label in group:
            hit = menu_lookup(menu_index, label)
            if not hit:
                ok = False
                break
            detail.append("%s ¥%.2f" % (hit[0], hit[1]))
            total += hit[1]
        if ok and total > best_total:
            best_total, best_detail = total, detail
    return best_total, best_detail


def collect_mall_products(snapshot, menu_index, explain=False):
    """遍历商场列表，逐个用在售状态过滤，再算出净节省。"""
    listing = load_json(os.path.join(snapshot, "mall-points-products.json"))
    if not listing:
        return [], []
    status = load_json(os.path.join(snapshot, "spu-status.json")) or []
    status_map = {str(r["spuId"]): r for r in status}

    detail_dir = os.path.join(snapshot, "detail")
    products, skipped = [], []

    for item in listing.get("data", []):
        spu_id = str(item.get("spuId"))
        points = float(item.get("point") or 0)
        if points <= 0:
            continue  # 纯现金商品，不构成积分出口
        row = status_map.get(spu_id)
        if row is not None and not row.get("live"):
            skipped.append((spu_id, item.get("spuName"), "已下架"))
            continue

        detail = load_json(os.path.join(detail_dir, "detail_%s.json" % spu_id)) or {}
        sku = (detail.get("data", {}).get("skuList") or [{}])[0]
        coupon_price = sku.get("extTradePrice")
        note = detail.get("data", {}).get("note", "")
        content = parse_redemption(note)

        if coupon_price is None or not content:
            skipped.append((spu_id, item.get("spuName"), "缺少用券价或兑换说明，无法定价"))
            continue

        groups = parse_components(content)
        reference, hit = best_reference(groups, menu_index)
        if reference <= 0:
            skipped.append((spu_id, item.get("spuName"), "构成单品不在菜单中，无法定价"))
            continue

        saving = round(reference - float(coupon_price), 2)
        if saving <= 0:
            skipped.append((spu_id, item.get("spuName"), "用券价不低于常规价，无节省"))
            continue

        products.append({
            "name": item.get("spuName"),
            "points": int(points),
            "cash_value": saving,
            "kind": item.get("catName") or "其他",
            "note": "常规价 %.2f − 用券价 %.2f｜%s" % (reference, float(coupon_price), "、".join(hit)),
        })
        if explain:
            print("  ✅ %-22s %5d 分  常规 %.2f  用券 %.2f  省 %.2f"
                  % (item.get("spuName"), int(points), reference, float(coupon_price), saving))

    if explain:
        print()
        for spu_id, name, why in skipped:
            print("  ⏭  %-22s %s" % (name, why))
    return products, skipped


COUPON_BLOCK = re.compile(
    r"^##\s+(?P<name>.+?)\n"
    r"(?:.*?\n)*?"
    r"-\s*\*\*优惠\*\*:\s*¥(?P<price>[0-9.]+).*?\n"
    r"-\s*\*\*有效期\*\*:\s*(?P<valid>[^\n]+)",
    re.M,
)


def collect_coupons(snapshot, menu_index, explain=False):
    """解析 query-my-coupons 的 markdown 文本。

    券在 MCP 里没有结构化返回，只有这段 markdown。用券价 ¥0 的券等于「免费用一件」，
    其价值就是该单品的价格。

    这里刻意用**现价**而不是常规价：券是「省下你本来要付的钱」，
    而你本来要付的是今天的标价。麦咖啡美式常规价 19 元、现价只有 9.9 元，
    按 19 元算会凭空放大一倍，属于自欺。
    """
    text = load_text(os.path.join(snapshot, "query-my-coupons.txt"))
    if not text:
        return [], []
    text = re.sub(r"<img[^>]*>", "", text)
    coupons, skipped = [], []
    for m in COUPON_BLOCK.finditer(text):
        name = m.group("name").strip()
        price = float(m.group("price"))
        valid = m.group("valid").strip()
        dates = re.findall(r"(20\d{2}-\d{2}-\d{2})", valid)
        expire = dates[-1] if dates else None
        hit = menu_lookup(menu_index, name)
        if hit:
            value = hit[2]  # 现价口径
            basis = "%s 现价" % hit[0]
        elif price == 0:
            skipped.append((name, "菜单中无对应单品，无法定价"))
            continue
        else:
            value = 0.0
            basis = "—"
        coupons.append({
            "name": name,
            "cash_value": round(value, 2),
            "expire_date": expire,
        })
        if explain:
            print("  🎟  %-20s 用券价 ¥%.1f  参照 %s  ¥%.2f" % (name, price, basis, value))
    return coupons, skipped


def build(snapshot, now=None, explain=False):
    account = load_json(os.path.join(snapshot, "query-my-account.json"))
    if not account:
        raise SystemExit("缺少 query-my-account.json")
    acct = account.get("data", {})

    time_info = load_json(os.path.join(snapshot, "now-time-info.json"))
    today = (time_info or {}).get("data", {}).get("date") or now

    menu_index = build_menu_index(load_json(os.path.join(snapshot, "query-meals.json")))

    if explain:
        print("【商场兑换项】")
    products, skipped_products = collect_mall_products(snapshot, menu_index, explain)
    if explain:
        print("\n【我的优惠券】")
    coupons, skipped_coupons = collect_coupons(snapshot, menu_index, explain)

    # 「本月将过期」只有月份粒度，没有具体日期 —— 用当月最后一天作为最晚到期日，
    # 这是上界而非精确值，报告中会明确标注。
    expiring = []
    this_month = float(acct.get("currentMouthExpirePoint") or 0)
    if this_month > 0 and today:
        y, m = int(today[:4]), int(today[5:7])
        last_day = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
        expiring.append({
            "points": this_month,
            "expire_date": "%04d-%02d-%02d" % (y, m, last_day),
            "_derived": "由 currentMouthExpirePoint 推算，为最晚到期日（上界）",
        })

    payload = {
        "now": today or now,
        "account": {
            "available_points": float(acct.get("availablePoint") or 0),
            "accumulated_points": float(acct.get("accumulativePoint") or 0),
            "frozen_points": float(acct.get("frozenPoint") or 0),
            "expired_points": float(acct.get("expiredPoint") or 0),
            "expiring": expiring,
        },
        "mall_products": products,
        "coupons": coupons,
        "recent_redemptions": [],
        "_meta": {
            "source": "麦当劳中国 MCP Server (mcp.mcd.cn)",
            "account_id_redacted": bool(acct.get("accountId")),
            "listed_points_products": sum(
                1 for i in (load_json(os.path.join(snapshot, "mall-points-products.json")) or {}).get("data", [])
                if float(i.get("point") or 0) > 0
            ),
            "priced_products": len(products),
            "unpriced_products": [{"name": n, "reason": w} for _s, n, w in skipped_products],
            "unpriced_coupons": [{"name": n, "reason": w} for n, w in skipped_coupons],
        },
    }
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="麦当劳 MCP 返回 → points_vault 输入")
    parser.add_argument("--snapshot", required=True, help="原始 MCP 返回所在目录")
    parser.add_argument("--now", help="覆盖报告基准日期（YYYY-MM-DD）")
    parser.add_argument("-o", "--out", help="输出文件；缺省写 stdout")
    parser.add_argument("--explain", action="store_true", help="打印每一项的定价过程")
    args = parser.parse_args(argv)

    payload = build(args.snapshot, args.now, args.explain)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        meta = payload["_meta"]
        print("\n✅ 已写出 %s" % args.out)
        print("   商城列出积分商品 %d 项 → 成功定价 %d 项（其余 %d 项无法定价或已下架）"
              % (meta["listed_points_products"], meta["priced_products"],
                 len(meta["unpriced_products"])))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
