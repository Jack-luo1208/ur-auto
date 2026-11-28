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
| `location_mode` | auto | `auto` 全球发现；`list` 只用 `node_locations` 文本查询 |
| `country_priority` | HK/TW/JP/SG/KR… | 国家优先顺序（靠近的先） |
| `exclude_countries` | `["CN"]` | 永不使用的国家 |
| `node_count` | 2 | 每轮产出几个节点（来自不同国家） |
| `mint_budget_seconds` | 780 | 抽节点的时间预算，到点就发布已有结果 |

余额来自 `GET /subscription/balance` 的 `balance_byte_count`（免费档 30 GiB/日，01:00Z 滚窗口）。

## 仓库

`git@github.com:Jack-luo1208/ur-auto.git` 是唯一仓库，所有逻辑与流程都在这里：`urn-pool` 定时/手动建池、验节点、转 Clash、PushPlus 推送。没有镜像仓库、没有 mirror workflow。

## GitHub Actions 用法

1. `Jack-luo1208/ur-auto` → Settings → Secrets and variables → Actions → `PUSHPLUS_TOKEN`（已配置）。推送目标走 `config.json` 里的 `pushplus_topic: "clash"`（群组）——**不要删掉这个 topic**：实测该账号的一对一消息虽然返回 `code=200` 但微信收不到，只有发到 `clash` 群组才能收到。私密群成员需在「群配置 → 查看二维码」扫码入群。
2. 首次运行会自动 bootstrap：注册 1 个 active + 5 个候补。
3. 由**外部定时器**每 2 小时触发一次 `workflow_dispatch`（GitHub 自带 cron 在高负载下会丢轮次，已从 workflow 移除）；也可在 Actions 页点 **Run workflow** 手动触发。

## 外部定时器（cron-job.org）

免费版即可满足，每 2 小时执行一次：

| 项 | 值 |
|---|---|
| URL | `https://api.github.com/repos/Jack-luo1208/ur-auto/actions/workflows/pool.yml/dispatches` |
| 方法 | `POST` |
| 请求头 | `Authorization: Bearer <PAT>`、`Accept: application/vnd.github+json` |
| Body | `{"ref":"main"}` |

PAT 用 Fine-grained token：Resource owner 选 `Jack-luo1208`，只授权 `ur-auto` 仓库，Permissions 里 **Actions: Read and write**（其余默认），有效期按需设置。PAT 只存在定时器里，不入仓库。

本机也可直接触发：`python tools/dispatch_ci.py`（从环境变量 `GH_PAT` 或 `~/.ur-auto/gh_pat` 读取 token，不会打印它）。

**推送失败即失败**：`urnpool pipeline` 在 PushPlus 投递失败时以非零退出，CI 直接报错，不会"全绿却一条没发"。

**状态持久化**：`state/pool.db` 不入库，通过 `actions/cache`（key 前缀 `urnpool-state-`）在两次运行之间传递。缓存被清空时下一轮会自动重建号池（等同首次运行）。

**订阅地址**：`https://cdn.jsdelivr.net/gh/Jack-luo1208/ur-auto@sub/clash.yaml`（jsDelivr 国内可达；raw.githubusercontent.com 在国内常被墙）。每轮 CI force-push 到 `sub` 分支（该分支只放根目录一个 `clash.yaml`），再调 jsDelivr purge 接口清缓存。推送进微信的是这个**订阅链接** + 节点表格，不再把整份 YAML 塞进消息；Clash Verge / Mihomo 里「从链接导入订阅」粘贴该 URL 即可。`sub/clash.yaml`、`sub/raw.txt` 仍作为 artifact 保留 7 天。

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

不再限定国家。每轮先用 `/network/find-provider-locations`（空 query 返回全球 86 个国家）做一次发现，按 `country_priority` 排序（默认港澳台日新韩马泰越菲印尼 → 美加英德荷法澳… ），`exclude_countries` 里的直接跳过（默认 `CN`），每个国家取 provider 最多的 2 个城市，一个国家最多出 1 个节点。

出口必须同时满足：

- ip-api `proxy=false` 且 `hosting=false`（双 false）
- ISP/org/AS 不命中机房关键词（aws/cloud/hosting/colo/oracle/ipxo/ztnet/octopus…）
- ipwho.is 二次核对：`security.hosting` 不为 true，公司域名不是 IP 租售商（ipxo、m247、zenlayer…）
- 国家与 provider 所在国一致（**硬性**）；城市只作参考（住宅 IP 常被定位到隔壁城镇，`strict_city: true` 才严格比对）
- 建好后实测 `generate_204 = 204` 与 `https://x.com/ = 200`

任一不满足即 `POST /network/remove-client`，换下一个 provider / 下一个国家。`mint_budget_seconds`（默认 780s）到点就停，把已抽到的先发出去，避免拖爆 job 超时。

## 安全说明

- 号池里的账号是免费档一次性账号（30 GiB/日），API key 与 auth_token 只存在于 `state/pool.db` 与 Actions 缓存中，不入 git、不进日志。
- `sub/` 产物含代理凭据，已在 `.gitignore` 中忽略；只通过 artifact 与 PushPlus 交付。
- PushPlus 的 token 只走 Secret/Variable，不写进仓库。
