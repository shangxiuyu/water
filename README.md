# Water —— Tree 的外部上下文灌溉生态

> **Seed（种子）破土，Water（活水）长流。**
> **Water** 是 Tree（Personal Context OS）的社区扩展插件仓。每个已上架的连接器都**自带完整闭环（扫码登录 → 只读拉取 → 过滤流水 → 脱敏输出）**，零外部前置 Skill 依赖，宁缺毋滥。

---

## 核心原则

1. **端到端自包含闭环**：新用户装完一个连接器就能直接用——没登录自动弹窗扫码，扫完自动保存凭证到本机 `~/.water/`，直接拉取精华喂给 Tree `seed`。
2. **只吸 1% 养分，过滤 99% 流水**：订单金额、快递物流、时间线刷屏、健康明细、证件号**一律不碰**。
3. **显式同意，不污染主干**：征得用户同意后安装到目标 Tree 仓库的 `.tree/seed/connectors/`（Git 自动忽略）。

---

## 快速上手（30 秒闭环）

```bash
# 1. 查看可用连接器与本机登录状态
./bin/water list

# 2. 一键安装到你的 Tree 仓库（若未登录会自动弹出二维码引导扫码）
./bin/water install jike --tree /path/to/your/Tree

# 3. 回到 Tree 拉取脱敏精华，交给宿主 Agent 蒸馏写入 01/02
cd /path/to/your/Tree
./bin/tree seed extract --sources jike
```

---

## 当前支持状态

* **已就绪（Ready，完整闭环）**：
  * `jike`（即刻）：内置二维码扫码登录 + Token 自动刷新 + 收藏精华脱敏提取
* **规划中（Planned，打通真实登录与提取闭环后上架）**：
  * `xianyu`（闲鱼）、`dingdong`（叮咚买菜）、`meituan`（美团）、`keep`（Keep）、`fliggy`（飞猪）

---

## 目录结构

```text
water/
├── bin/water                  # 统一闭环 CLI（list / login / install）
├── connectors/
│   ├── _template.py           # 标准连接器开发模板
│   └── jike.py                # 即刻自包含连接器（已闭环）
├── guides/
│   └── lifestyle-sync.md      # 供 Tree `04-我如何工作/` 引用的联动规矩模板
├── registry.json              # 插件清单与隐私边界声明
└── README.md
```
