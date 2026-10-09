# WorkBuddy 开发上下文

本文件用于说明本项目在开发过程中对腾讯 WorkBuddy 智能体的实际使用情况，作为「WorkBuddy 专项奖励」的核验材料。

## 开发方式

本项目全程使用 WorkBuddy（Agent 模式）完成，未使用其他 AI 编码工具。

## 使用过程

### 阶段一 · 赛题调研与选题决策

**输入需求**：分析麦当劳程序员创意开发大赛（M-China/mcd-developer-innovation-challenge），判断适合做什么主题。

**WorkBuddy 执行的动作**：

1. 克隆并解析大赛官方仓库 `M-China/mcd-developer-innovation-challenge`，读取 `README.md`、`activityGuidelines.md`、`CONTEST_DECLARATION.md`、`RANKING.md`
2. 克隆并解析 MCP 能力仓库 `M-China/mcd-mcp-server`，提取全部 Tool 清单（32 个工具，8 大业务域）
3. 统计排行榜现有 48 个项目的主题分布，识别红海赛道
4. 对比 Tool 覆盖度与现有项目的使用情况，定位无人使用的蓝海能力（麦麦商城 `mall-*`、历史订单 `order-list`、主题活动 `party-*`）
5. 输出选题分析报告，含竞争格局图、合规红线清单与拿星执行清单
6. 给出候选选题排序与推荐结论

**产出**：选题分析报告（含竞争格局可视化），并据此确定项目主题为「麦麦积分钱庄」。

### 阶段二 · 项目开发

**输入需求**：基于选定的「麦麦积分钱庄 —— 积分与券资产运营管家」主题，创建完整的参赛 Skill 项目。

**WorkBuddy 执行的动作**：

1. 加载 `skill-creator` 规范，按标准 Skill 结构（`SKILL.md` + `scripts/` + `references/`）组织项目
2. 实现估值引擎 `scripts/points_vault.py`：积分价值折算、每 100 积分兑现效率排序、30 天过期敞口量化、贪心兑换路径生成
3. 设计字段别名容错层，使引擎可直接消费 MCP 原始返回值
4. 编写 `SKILL.md` 定义五步执行流程与写操作确认机制
5. 编写 `references/tool-playbook.md` 与 `references/valuation-model.md`
6. 构造零配置演示数据 `examples/demo-account.json`
7. 本地运行验证引擎输出，修正语法错误与建议分档阈值
8. 按大赛要求补齐 `README.md`、`CONTEST_DECLARATION.md`（官方原文，校验哈希一致）、`MCP_INTEGRATION.md`、`mcp-config.example.json`
9. 对项目做安全检查：无真实凭证、无外链风险、无危险代码

**产出**：完整可运行、可演示、符合大赛提交要求的 Skill 项目。

## 使用的 WorkBuddy 能力

| 能力 | 用途 |
| --- | --- |
| Agent 模式 | 全流程自主执行：调研 → 设计 → 编码 → 验证 → 交付 |
| 文件系统读写 | 创建项目结构、编写全部源码与文档 |
| Shell 执行 | 克隆仓库、运行 Python 脚本验证输出、校验文件哈希 |
| 联网检索 | 抓取大赛规则与 MCP 官方文档 |
| Skill 加载 | `skill-creator` 提供 Skill 结构规范 |
| 可视化 | 生成选题竞争格局图，辅助主题决策 |

## 开发时间

2026 年 10 月 9 日

## 附注

- 本项目中所有涉及麦当劳账户数据的逻辑均通过官方 MCP 接口获取，开发阶段使用构造的演示数据验证，未使用任何真实用户数据。
- 项目未记录、未存储、未提交任何真实 MCP Token 或个人信息。
