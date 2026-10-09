# MCP 工具调用手册

麦当劳 MCP Server 接入地址 `https://mcp.mcd.cn`，Streamable HTTP 协议，请求头携带 `Authorization: Bearer <TOKEN>`。

本技能实际使用的工具及其用途如下。

## 核心工具（必调）

### `now-time-info`
返回当前完整时间。用途：计算积分/券的剩余天数。**不要**用模型自身的时间推测，一律以此工具返回为准。

### `query-my-account`
查询积分账户信息。**实测返回的是月份粒度的桶，不是逐笔积分批次**：

| 返回值 | 含义 | 标准字段 |
| --- | --- | --- |
| `availablePoint` | 可用积分（**字符串小数**，如 `"2741.9"`） | `account.available_points` |
| `accumulativePoint` | 累计积分 | `account.accumulated_points` |
| `frozenPoint` | 冻结积分 | `account.frozen_points` |
| `expiredPoint` | **历史累计已过期积分** | `account.expired_points` |
| `currentMouthExpirePoint` | 本月将过期积分 | `account.expiring[]` |
| `nextMouthExpirePoint` | 下月将过期积分 | 参考信息 |
| `lastMouthExpirePoint` | 上月已过期积分 | 参考信息 |
| `usedPoint` | 已使用积分 | 参考信息 |

⚠️ **没有任何字段给出具体到期日**，只有「本月/下月」这种月份粒度。所以「还剩几天到期」只能用当月最后一天作为**上界**，并在报告中如实标注这是推算值。

这是本技能最关键的一次调用。若 `currentMouthExpirePoint` 缺失或为 0，如实说明数据缺失，不要凭空推断。

### `mall-points-products`
查询麦麦商城的商品列表。**实测返回 50 项，其中只有 24 项是积分商品，另 26 项是纯现金业务**（生日派对、主题派对、品鉴会、读书会，`point = 0`），必须筛选。

| 返回值 | 含义 | 标准字段 |
| --- | --- | --- |
| `spuName` | 商品名称（**金额常写在名字里**，如 `"21.9元巨无霸可乐组合"`） | `mall_products[].name` |
| `point` | 所需积分（积分为 `"0"` 时是现金商品，直接跳过） | `mall_products[].points` |
| `catName` | 商品类型 | `mall_products[].kind` |
| `price` | **积分商品恒为 `"0"`，不可用作参考价** | 不可用 |
| `status` | **实测恒为 `2`，无法区分为在售/已下架** | 不可用 |

⚠️ **该接口会把已下架商品一并返回。** 实测 24 个积分兑换项里 17 个已下架（71%）。**必须逐个调 `mall-product-detail` 验证**，以下架错误码 `610403` 为准。

### `mall-product-detail`
按 `spuId` 查商品详情。两个关键字段：

- `data.skuList[].extTradePrice` —— **用券价**（你还要付多少钱），**不是券的价值**
- `data.note` —— 券面说明，其中【兑换内容】写明了套餐/任选的**具体构成**，是定价的唯一依据

**关键缺口**：接口不直接返回商品的市场参考价，而这是计算「每 100 积分兑现价值」的必要输入。补全方式见下节。

### `query-nearby-stores` → `query-meals`
`query-meals` 必传 `storeCode` / `orderType` / `beType`，其中 `storeCode` 需先用 `query-nearby-stores` 取得（`searchType=2` 按城市+关键词搜索，`searchType=1` 搜收藏门店）。

`query-meals` 返回的 `data.meals` 是**以餐品编码为键的 dict**（不是数组），每项含 `name` / `currentPrice` / `originalPrice`。

- `originalPrice`（常规价）= 计算净节省的基准，与麦当劳券面说明第 10 条「节省金额是与常规价格相比较」一致
- `currentPrice`（现价）= 给「免费券」估值时用现价，更保守

### `query-my-coupons`
查询用户账户下的优惠券列表。

⚠️ **实测没有结构化返回** —— 只有一段 markdown 文本（含 `<img>` 标签与 `- **优惠**: ¥0 (用券价格)` 这样的行），必须正则解析。映射为 `coupons[]`，含 `name`、`cash_value`、`expire_date`。

`优惠: ¥0 (用券价格)` 表示「凭此券 0 元拿走该单品」，其价值 = 该单品的**现价**（保守口径，用 `query-meals` 的 `currentPrice`）。

## 辅助工具（按需调用）

### `available-coupons` / `auto-bind-coupons`
`available-coupons` 查询麦麦省当前可领取的券，用于给出「还能额外锁定多少权益」的增量建议。
`auto-bind-coupons` 自动领取全部可领券 —— **属于账户写操作，必须经用户确认后执行**。

### `calculate-price`
根据商品列表与优惠券试算金额、配送费、优惠金额与应付总价。用途：验证「积分兑换 + 券」组合的真实抵扣效果。

### `campaign-calendar`
查询当月营销活动日历（进行中、往期、未来）。用途：判断是否值得择时兑换（例如临近大促时先持有积分）。

### `mall-order-list` / `mall-order-detail`
查询麦麦商城近一年的兑换订单。用途：避免重复推荐用户已经兑换过的商品，并识别其偏好品类。

## 写操作工具（高风险，需用户确认）

| 工具 | 影响 | 处理方式 |
| --- | --- | --- |
| `mall-create-order` | 真实扣减积分、发券或扣减实物库存 | 必须展示「消耗 N 积分换取 X」并取得明确同意 |
| `auto-bind-coupons` | 自动领取账户下所有可领券 | 说明将领取的券数量后取得同意 |

## 明确不使用的工具

- `draw-lottery` / `query-lottery-info` —— 积分抽奖。不提供概率分析或中奖率优化建议，规避「宣扬赌博」的合规风险。
- `create-order` / `cancel-order` / `party-order-create` —— 点餐与主题活动下单，不属于资产运营范畴。

## `cash_value` 怎么来：净节省，不是面值

`cash_value` 在本引擎里定义为**净节省额**，而不是券面金额：

```
cash_value = 常规价 − 用券价
```

推导链条（以 `spuId 17081` 为例）：

1. 商品名 `"21.9元巨无霸可乐组合"` → 用券价 ¥21.9
2. `mall-product-detail` 的【兑换内容】→「含 1 份巨无霸 + 1 份中可乐」
3. `query-meals` 的 `originalPrice` → 巨无霸 ¥25.50 + 中可乐 ¥9.50 = 常规价 ¥35.00
4. 净节省 = 35.00 − 21.9 = **¥13.10**；800 积分 → `13.10 / 800 × 100 = 1.64 元/100分`

⚠️ 若误把用券价当价值，会得出 `21.9 / 800 × 100 = 2.74 元/100分`，**既高估规模又颠倒排序**。详见 `valuation-model.md`。

**无法定价时怎么办**：宁可在报告中列出「该项无法定价」及具体原因（如「构成单品不在菜单中」「已下架」），也不要估算。归一化工具 `tools/normalize_mcp.py --explain` 会把每一项的定价过程与失败原因都打印出来，便于人工复核。

## 限流与错误处理

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| 401 | Token 无效、过期或未提供 | 提示用户重新申请并配置 MCP Token |
| 429 | 超过 600 次/分钟 | 降低请求频率，合并可并行调用 |

单次体检的合理调用量为 5~8 次，远低于限流阈值。避免在循环中反复轮询。
