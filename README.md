# Water —— Tree Seed 官方按需连接器仓库

本仓库是 **[Tree](https://github.com/shangxiuyu/Tree)（Personal Context OS）** 的官方按需连接器插件库，统一归入 `tree seed` 命令体系使用（不再需要单独安装 `water` CLI 或额外 Skill）。

> **为什么单独放在这里？**
> - **本机自带的通用基础源已直接内置在 `Tree` 本体中**：11 款本地 AI 客户端（Claude Code / Codex / Antigravity / Cursor / Trae / WorkBuddy / 豆包工作 / Hermes / OpenClaw / Eden / DeepSeek Harness）+ **Google Chrome**（书签与常逛站点），普通用户安装 `Tree` 后零配置直读本机历史。
> - **本仓库存放所有第三方云端平台与生活 App 的自包含连接器**：包括纯 Python 标准库零依赖的外脑与社交源（**得到大脑 `getnote`、flomo `flomo`、微信读书 `weread`、即刻 `jike`**）以及需要额外依赖的生活源（美团、淘宝/盒马、叮咚、京东、闲鱼、Keep、微信），统一通过 `tree seed add <平台ID>` 按需拉取到本机 `seed/connectors/`，保持 `Tree` 核心内核轻量干净。

---

## 按需使用流程（在 `Tree` 中直接调用）

```bash
# 1. 查看所有可按需安装的外部连接器及所需依赖
tree seed add --list

# 2. 按需下载指定连接器（包含完整自包含客户端代码，存入 seed/connectors/<id>/）
tree seed add getnote,flomo,weread,jike    # 纯标准库零依赖，下载即可用
# 或安装生活源：tree seed add meituan

# 3. 若该连接器声明了额外依赖（如 playwright），按终端提示安装；零依赖连接器可跳过此步

# 4. 扫码或 OAuth 授权登录（凭证仅保存在本机隔离目录，绝不进 Git）
tree seed login getnote

# 5. 只读提取脱敏线索至 seed/staging/，并由 AI 更新 seed/外部上下文源.md、提炼 1% 慢变量入树
tree seed extract --sources getnote
```

---

## 包含的自包含连接器清单

| 连接器 ID | 平台 | 前置依赖 | 只提取什么（源头自动剥离隐私敏感字段） |
| :--- | :--- | :--- | :--- |
| `getnote` | **得到大脑**（Get 笔记 · `biji.com`） | **无**（纯 Python 标准库，下载即用） | 知识库全景目录、近期发芽报告、个人笔记与得到课程/电子书划线（附实时 `detail` / `recall` 查询 CLI） |
| `flomo` | **flomo 浮墨笔记** | **无**（纯 Python 标准库，下载即用） | 用户稳定画像 (`memory_user`)、标签树与近期卡片（附实时 `search` / `tags` 查询 CLI，需 Max/领航者会员） |
| `weread` | **微信读书**（WeRead） | **无**（纯 Python 标准库，下载即用） | 阅读统计画像、有笔记书单、个人想法与精选划线（附实时 `bookmarks` / `reviews` 查询 CLI） |
| `jike` | **即刻 Jike** | **无**（纯 Python 标准库，下载即用） | 个人主动收藏精华帖与外链（附实时 `collections` 查询 CLI） |
| `meituan` | 美团（外卖/闪购/团购） | `playwright` | 常点餐厅、业务类别与口味偏好线索 |
| `taobao` | 淘宝 / 淘宝闪购 / 盒马 | `playwright` | 常用城市商圈 POI（不含门牌与电话）、三轨常购商品标题 |
| `dingdong` | 叮咚买菜 | `playwright` | 高频复购生鲜食材统计与饮食习惯线索 |
| `jd` | 京东 | `requests`, `playwright` | 家用硬件设备型号与常购耗材标题 |
| `xianyu` | 闲鱼 | `ego-browser`（或配置 Cookie） | 购入与出让的硬件设备标题 |
| `keep` | Keep | `requests` | 累计运动概览与长期运动项目分布（弹出官方高清二维码图扫码，无需 Playwright） |
| `wechat` | 微信 | `wechat-cli` | 微信主动收藏精华（默认不读私聊/群聊流水） |

---

## 目录结构

```text
water/
├── registry.json                  # 供 `tree seed add` 读取的连接器文件与依赖索引
├── connectors/
│   ├── getnote/                   # connector.py + getnote_client.py（纯标准库零依赖 + 实时 recall/detail CLI）
│   ├── flomo/                     # connector.py + flomo_client.py（纯标准库零依赖 + 实时 search/tags CLI）
│   ├── weread/                    # connector.py + weread_client.py（纯标准库零依赖 + 实时 bookmarks/reviews CLI）
│   ├── jike/                      # connector.py + jike_client.py（纯标准库零依赖 + 实时 collections CLI）
│   ├── meituan/                   # connector.py + meituan_client.py
│   ├── taobao/                    # connector.py + ali_client.py + hema_client.py + eleme_client.py
│   ├── dingdong/                  # connector.py + dingdong_client.py
│   ├── jd/                        # connector.py + jd_client.py
│   ├── xianyu/                    # connector.py + xianyu_client.py
│   ├── keep/                      # connector.py + keep_client.py
│   └── wechat/                    # connector.py
└── README.md
```
