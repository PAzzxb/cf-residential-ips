# 家宽 IP 池（台湾/香港/新加坡/美国/日本/韩国）

> 2026-09-04 从全网深挖 + 三轮实测筛出的 **Cloudflare 家宽入口 IP**。
> 架构：家宽盒子(住宅宽带)做 TCP 转发入口 → CF 边缘对应分区机房(TPE/HKG/SIN/ICN/NRT) → Worker。
> 好处：① 入口非 CF 段，不易撞墙 ② 就近分区延迟低 ③ 节点显示正确地区旗帜。

## 文件说明

| 文件 | 内容 |
|---|---|
| `家宽入口_已验证21.txt` | 三轮 VLESS 端到端实测存活的 21 个，格式 `IP:PORT#备注`，可直接粘进 KV ADD.txt |
| `RES候选池_六地区.txt` | 上游全部住宅候选(TW13/HK80/SG1/US704/JP81/KR8)，按存活天数+速度排序 |
| `refresh.py` | 刷新工具，见下 |

## 随时拉取（三个入口）

**1. 本目录文件**（shared 同步 + 手机 Download 目录各一份）

**2. 上游仓库（每 2 小时自动更新）**
```
https://raw.githubusercontent.com/Xiaobei09/proxyip/main/data/valid/all.txt
```
行格式 `IP:PORT#🇹🇼TW→TW-278ms-1.68MB/s-RES-CN-U23`，筛 `-RES-` 即住宅。

**3. 家宽反代域名族**（DNS 直接解析，IP 会自动轮换）
```
tw.william.us.ci     # 台湾 HINET 中华电信
kr.william.us.ci     # 韩国 KT
<cc>.bestcf.eu.cc    # 18 地区，早中晚三更（wanwushequ/ProxyIP 仓库维护）
```

## 刷新方法

```bash
# 只拉最新候选池（不探测，零风险）
python3 refresh.py

# 拉候选 + 每地区前 8 个做端到端实测（小样本，约 50 次握手）
python3 refresh.py --test 8
```
实测通过的写进 `家宽入口_已验证.txt`，把内容追加到 CF KV 的 `ADD.txt` 即上线。
（在 Minis 里直接说"帮我刷新家宽池"也行）

## 命名与上线

ADD.txt 里备注写 `TW 家宽` / `KR 家宽` 这类，Worker 的统一命名函数会自动出
`088 🇹🇼 台湾` 这样的节点名（编号接在现有节点后面）。

## 本轮已剔除的死节点（两轮复测均失败，勿再试）

HK: 219.76.13.181 / 47.239.4.246(三端口) · JP: 23.27.52.76 · US: 144.225.246.111 / 23.106.46.112 / 43.153.115.58

## 注意

- 上游 `RES` 标记约 6-7 成真住宅（混有 AkileCloud/腾讯云标错的），用前抽验：
  `curl "http://ip-api.com/json/<IP>?fields=countryCode,asname,hosting"`，hosting=false 才是住宅
- 出口仍是 CF Workers egress（`2a09:bac*::`），**这不是住宅出口代理**——它的价值在入口侧
- 测入口别用 curl `--resolve`（沙盒代理会绕过它），用纯 socket；实际机房看 cf-ray 尾部 colo
