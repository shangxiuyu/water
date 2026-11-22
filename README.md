# Water —— Tree 的外部上下文灌溉生态（Community Connectors）

> **Seed（种子）破土，Water（活水）长流。**
> **Water** 是 [Tree](https://github.com/shangxiuyu/Tree)（Personal Context OS）的社区扩展插件仓。它负责安全、克制地连接外部 App（如即刻、闲鱼、叮咚买菜、淘宝、京东等），**过滤掉 99% 的订单流水与信息噪音，只把 1% 的高纯度慢变量养分输送回 Tree**。

---

## 核心原则（三条红线）

1. **核心解耦**：Tree 核心仓保持零依赖、纯标准库；所有涉及第三方平台接口、Cookie 或本地 Skill 桥接的脚本，全部留在 **Water**，按需安装。
2. **显式同意**：宿主 Agent 必须先向用户说明连接器**只读什么、依赖什么**，经用户明确同意后，才将对应脚本安装到目标 Tree 仓库的 `.tree/seed/connectors/`。
3. **只吸养分，不搬流水**：订单明细、快递物流、账单金额、时间线刷屏**一律不进 Tree**；只提取跨周期有效的饮食偏好、设备资产、关注领域与独立思考。

---

## 目录结构

```text
water/
├── README.md                  # 本说明与安装规范
├── registry.json              # 供 AI Agent 快速检索的连接器清单
├── connectors/                # 标准只读连接器（对接 Tree `.tree/seed/connectors/`）
│   ├── _template.py           # 开发者扩展模板
│   ├── jike.py                # 即刻（收藏与原创观点蒸馏）
│   ├── xianyu.py              # 闲鱼（持有设备资产与交易避坑偏好）
│   └── dingdong.py            # 叮咚买菜（饮食偏好与基础食材习惯）
└── guides/
    └── lifestyle-sync.md      # 供 Tree `04-我如何工作/` 引用的联动规矩模板
```

---

## 安装方式（经用户同意后执行）

将你需要的单个连接器复制到你的 Tree 仓库 `.tree/seed/connectors/` 下（该目录默认被 Tree 的 `.gitignore` 忽略，不会污染个人认知仓库）：

```bash
# 示例：安装即刻连接器到你的 Tree 仓库
mkdir -p /path/to/your/Tree/.tree/seed/connectors
cp connectors/jike.py /path/to/your/Tree/.tree/seed/connectors/

# 验证是否被 Tree 识别
cd /path/to/your/Tree
./bin/tree seed scan
```

拉取脱敏精华到暂存区（`.tree/seed/staging/`），由宿主 Agent 蒸馏写入 `01~04`：

```bash
./bin/tree seed extract --sources jike
```

---

## 贡献新连接器

参考 [`connectors/_template.py`](./connectors/_template.py)，实现 `CONNECTOR_ID`、`discover()` 和 `extract(days, max_sessions, helpers)` 三个约定即可，并在 `registry.json` 登记元数据。
