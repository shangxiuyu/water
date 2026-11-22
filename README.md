# Water Skill —— Tree 的外部上下文灌溉技能

> **Seed（种子）破土，Water（活水）长流。**
> **Water** 是一个整合型的 **Agent Skill**，专门配合 [Tree](https://github.com/shangxiuyu/Tree)（Personal Context OS）使用。它把外部 App（即刻、叮咚买菜、闲鱼、美团、Keep、淘宝/盒马、京东、微信）的**登录/初始化、只读拉取、流水过滤与脱敏提炼**打包成一个统一技能，让用户对 AI 说一句话就能完成外部上下文灌溉。

---

## 一键安装

```bash
./bin/water install-skill
```

安装后，在任何支持的 Agent（Claude Code / Cursor / Antigravity / Eden 等）里直接说：
* *“把我的即刻收藏接入 Tree”*
* *“帮我看看 water 支持哪些外部平台”*

Agent 会自动遵循 [SKILL.md](./SKILL.md) 跑完：**查状态 → 未登录时弹二维码让你扫 → 拉取并过滤 99% 流水 → 提炼 1% 慢变量写入 Tree**。

---

## CLI 手动用法

```bash
./bin/water list          # 查看支持平台与本机登录状态
./bin/water login jike    # 单独触发扫码登录（凭证仅存 ~/.water/）
./bin/water fetch jike    # 闭环拉取脱敏精华（未登录自动引导扫码）
```

---

## 目录结构

```text
water/
├── SKILL.md                   # 整合 Skill 契约（触发条件、闭环流程、隐私红线）
├── bin/water                  # 统一执行引擎（list / login / fetch / install-skill）
├── scripts/
│   ├── _bridge.py             # 共享本地套件定位与隐私脱敏辅助
│   ├── jike.py                # 即刻
│   ├── dingdong.py            # 叮咚买菜
│   ├── xianyu.py              # 闲鱼
│   ├── meituan.py             # 美团
│   ├── keep.py                # Keep
│   ├── taobao.py              # 淘宝 / 闪购 / 盒马
│   └── jd.py                  # 京东
│   └── wechat.py              # 微信（默认仅读收藏）
├── guides/
│   └── lifestyle-sync.md      # Tree `04-我如何工作/` 联动规矩模板
├── registry.json              # 平台能力与隐私边界清单
└── README.md
```
