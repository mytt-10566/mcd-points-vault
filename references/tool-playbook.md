# MCP 工具调用手册

麦当劳 MCP Server 接入地址 `https://mcp.mcd.cn`，Streamable HTTP 协议，请求头携带 `Authorization: Bearer <TOKEN>`。

本技能实际使用的工具及其用途如下。

## 核心工具（必调）

### `now-time-info`
返回当前完整时间。用途：计算积分/券的剩余天数。**不要**用模型自身的时间推测，一律以此工具返回为准。

### `query-my-account`
查询积分账户信息，返回可用积分、累计积分、冻结积分、即将过期积分。

映射到标准字段：

| 返回值含义 | 标准字段 |
| --- | --- |
| 可用积分 | `account.available_points` |
| 累计积分 | `account.accumulated_points` |
| 冻结积分 | `account.frozen_points` |
| 即将过期积分（含到期日） | `account.expiring[]`，每项含 `points` 与 `expire_date` |

这是本技能最关键的一次调用。若「即将过期积分」为空或缺字段，如实说明数据缺失。

### `mall-points-products`
查询麦麦商城内可用积分兑换或现金购买的商品（不含第三方兑换码）。

映射到标准字段：

| 返回值含义 | 标准字段 |
| --- | --- |
| 商品名称 | `mall_products[].name` |
| 所需积分 | `mall_products[].points` |
| 商品类型 | `mall_products[].kind` |

**关键缺口**：该接口不直接返回商品的市场参考价，而这是计算「每 100 积分兑现价值」的必要输入。补全方式见下节。

### `mall-product-detail`
查询商城商品详情（图片、积分、有效期、说明）。用途：为积分效率靠前的候选商品补充 `valid_days`，判断兑换后是否来得及使用。

### `query-my-coupons`
查询用户账户下的优惠券列表。映射为 `coupons[]`，含 `name`、`cash_value`、`min_spend`、`expire_date`。

## 辅助工具（按需调用）

### `available-coupons` / `auto-bind-coupons`
`available-coupons` 查询麦麦省当前可领取的券，用于给出「还能额外锁定多少权益」的增量建议。
`auto-bind-coupons` 自动领取全部可领券 —— **属于账户写操作，必须经用户确认后执行**。

### `calculate-price`
根据商品列表与优惠券试算金额、配送费、优惠金额与应付总价。用途：验证「积分兑换 + 券」的实际组合收益，把券的 `cash_value` 从面值校正为真实可抵扣额。

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

## 补全 `cash_value` 的三种方式

按可靠性从高到低选择：

1. **`mall-product-detail`**：若返回中包含商品价值或可兑换权益的说明，直接采用。
2. **`query-meals` 交叉比对**：对餐品类兑换券，用同款餐品在当前门店的实际售价作为参考价。
3. **人工给定**：请用户提供大致市价，并在报告中标注为估算。

若三者均不可用，把该商品的 `cash_value` 置为 `0` 并在报告说明中提示数据不足 —— **不要编造价格**。

## 限流与错误处理

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| 401 | Token 无效、过期或未提供 | 提示用户重新申请并配置 MCP Token |
| 429 | 超过 600 次/分钟 | 降低请求频率，合并可并行调用 |

单次体检的合理调用量为 5~8 次，远低于限流阈值。避免在循环中反复轮询。
