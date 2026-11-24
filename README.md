# urn-auto

URnetwork 免费档账号池 + 自动订阅（Clash）+ PushPlus 推送。
每 2 小时由 GitHub Actions 跑一轮：刷新余额 → 淘汰耗尽账号 → 候补顶上 → 补满 5 个候补 → 取干净美国住宅出口 → 转 Clash → 推送。

## 池规则

| 参数 | 默认 | 含义 |
|---|---|---|
| `standby_target` | 5 | 候补账号数量 |
| `refill_when_standby_at_or_below` | 1 | 候补掉到 1 个就补齐到 5 |
| `exhaust_threshold_bytes` | 268435456 (256 MiB) | 余额低于此值即视为耗尽 |
| `max_creates_per_tick` | 5 | 单轮最多新建账号（平台限制 5 个/分钟） |
| `node_count` | 1 | 每轮产出几个节点 |
| `node_locations` | Seattle / Chicago / Miami / LA | 轮询的取样城市 |

余额来自 `GET /subscription/balance` 的 `balance_byte_count`（免费档 30 GiB/日，01:00Z 滚窗口）。

## 两个仓库的分工

| 仓库 | 角色 | 干什么 |
|---|---|---|
| `Jack-luo1208/ur-auto` | **主跑仓库** | 定时/手动跑 `urn-pool`：建池、验节点、转 Clash、PushPlus 推送 |
| `cctrylyj/ur-auto` | 源仓库 | 只跑 `mirror`：把代码强推到主跑仓库，自己不再跑池 |

两个 workflow 都用 `if: github.repository == ...` 做了互斥，所以**任何一次改动只会跑一轮池**，不会双倍建号、也不会双倍推送。

## GitHub Actions 用法

1. 主跑仓库 `Jack-luo1208/ur-auto` → Settings → Secrets and variables → Actions → `PUSHPLUS_TOKEN`（已配置）。
2. 首次运行会自动 bootstrap：注册 1 个 active + 5 个候补。
3. `schedule` 每 2 小时跑一次；也可在 Actions 页点 **Run workflow** 手动触发。

**同步**：`.github/workflows/mirror.yml` 用 `MIRROR_TOKEN`（对主跑仓库有 `Contents: write` 的 PAT）强推；本机可用

```powershell
powershell -ExecutionPolicy Bypass -File scripts\set-mirror-token.ps1 -Token ghp_xxxx -Dispatch
```

没配令牌时 mirror 绿灯跳过，不影响池流程。

**状态持久化**：`state/pool.db` 不入库，通过 `actions/cache`（key 前缀 `urnpool-state-`）在两次运行之间传递。缓存被清空时下一轮会自动重建号池（等同首次运行）。

**产物**：`sub/clash.yaml`、`sub/raw.txt` 作为 artifact 保留 7 天；同一份 Clash 配置同时推送进微信。

## 订阅格式

生成的订阅本身就是 **Clash/Mihomo YAML**（`mixed-port: 7890`，`proxies` 为 `socks5`，`PROXY` 选择组 + `AUTO` url-test 组，规则 `LAN/CN 直连，其余走 PROXY`）。
pipeline 先产出 `socks://` 链接（`sub/raw.txt`），再由 `subscription.ensure_clash()` 判定：源已是 Clash（含 `proxies:`/`proxy-groups:`/`rules:`）就原样透传，否则按同一套节点参数转成 Clash——两条路径产出的 YAML 结构一致，推送的一律是 Clash。
推送内容是 HTML 表格（节点/出口 IP/城市/ISP）+ `<pre>` 里的完整 YAML，直接复制进 Clash Verge / Mihomo 即可。

## 本地运行

```bash
pip install -r requirements.txt
python -m urnpool status                 # 看池
python -m urnpool tick                   # 跑一轮控制
python -m urnpool sub --push             # 生成订阅并推送（需 PUSHPLUS_TOKEN）
python -m urnpool pipeline --out sub     # CI 同款：tick + 订阅 + 推送
python -m urnpool simulate --standby 2   # 本地演练淘汰/补齐，不烧真实额度
```

## 节点判定（不达标就删）

出口必须同时满足：`country=United States`、ip-api `proxy=false`、`hosting=false`、ISP 非机房关键词、城市与 provider 所在地一致；建好后实测 `generate_204 = 204` 与 `https://x.com/ = 200`。任一不满足即 `POST /network/remove-client`，换下一个 provider。

## 安全说明

- 号池里的账号是免费档一次性账号（30 GiB/日），API key 与 auth_token 只存在于 `state/pool.db` 与 Actions 缓存中，不入 git、不进日志。
- `sub/` 产物含代理凭据，已在 `.gitignore` 中忽略；只通过 artifact 与 PushPlus 交付。
- PushPlus 的 token 只走 Secret/Variable，不写进仓库。
