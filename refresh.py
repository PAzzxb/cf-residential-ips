#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三源聚合家宽入口 IP 池刷新工具
用法:
  python3 refresh.py            # 拉最新上游清单 → 筛六地区住宅候选 → 更新 RES候选池_六地区.txt
  python3 refresh.py --test 8   # 同上，并对每地区前 8 个做 VLESS 端到端实测 → 更新 家宽入口_已验证.txt
依赖: 仅 Python 标准库（--test 需能直连网络）
上游:
  - Xiaobei09/proxyip (实测, 60%)
  - LancelotRar/best-cf-ips (LR优选, 20%)
  - ymyuuu/IPDB (IPDB, 20%)
"""
import base64, collections, json, os, re, socket, ssl, struct, sys, time, uuid as uuidmod
import urllib.request

# 上游源配置
SOURCES = {
    'xiaobei': {
        'url': 'https://raw.githubusercontent.com/Xiaobei09/proxyip/main/data/valid/all.txt',
        'weight': 0.60,
    },
    'lancelot': {
        'url': 'https://raw.githubusercontent.com/LancelotRar/best-cf-ips/main/best-cf-ip-scanned-top400.txt',
        'weight': 0.20,
    },
    'ipdb': {
        'url': 'https://raw.githubusercontent.com/ymyuuu/IPDB/main/proxy.txt',
        'weight': 0.20,
    },
}

# 你的节点信息（改这里）
PAGES_HOST = 'static-assets-6bm.pages.dev'
VLESS_UUID = '66d6b88c-1956-42ee-a412-a528f98205d9'
REGIONS = ('TW', 'HK', 'SG', 'US', 'JP', 'KR')
HERE = os.path.dirname(os.path.abspath(__file__))

# 已验证清单：优先仓库根的 家宽入口_已验证*.txt，没有就落在根目录
VERIFIED_GLOB = '家宽入口_已验证*.txt'

def fetch_url(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            print(f'拉取重试{i}: {url} - {e}')
            time.sleep(4)
    return None

def fetch_upstream():
    """拉取三个上游源"""
    data = {}
    for name, cfg in SOURCES.items():
        print(f'拉取 {name}: {cfg["url"]}')
        txt = fetch_url(cfg['url'])
        if txt is None:
            print(f'  警告: {name} 拉取失败，跳过')
            continue
        data[name] = txt
        print(f'  成功: {len(txt.splitlines())} 行')
    return data

def parse_xiaobei(txt):
    """解析 Xiaobei09 格式: IP:PORT#FLAG REGION ..."""
    rows = collections.defaultdict(list)
    for line in txt.splitlines():
        line = line.strip()
        if '#' not in line:
            continue
        addr, note = line.split('#', 1)
        if ':' not in addr:
            continue
        m = re.match(r'^[\U0001F1E6-\U0001F1FF]{2}([A-Z]{2})', note)
        if not m or m.group(1) not in REGIONS:
            continue
        if 'RES' not in note.split('-'):
            continue
        lat = re.search(r'-(\d+)ms', note)
        spd = re.search(r'-([\d.]+)MB/s', note)
        up = re.search(r'-U(\d+)', note)
        rows[m.group(1)].append({
            'addr': addr,
            'note': note,
            'latency': int(lat.group(1)) if lat else 9999,
            'speed': float(spd.group(1)) if spd else 0.0,
            'uptime': int(up.group(1)) if up else 0,
            'source': 'xiaobei',
        })
    return rows

def parse_lancelot(txt):
    """解析 LancelotRar 格式: IP:PORT#REGION FLAG"""
    rows = collections.defaultdict(list)
    for line in txt.splitlines():
        line = line.strip()
        if '#' not in line or line.startswith('#'):
            continue
        addr, note = line.split('#', 1)
        if ':' not in addr:
            continue
        m = re.match(r'^([A-Z]{2})\s', note)
        if not m or m.group(1) not in REGIONS:
            continue
        rows[m.group(1)].append({
            'addr': addr,
            'note': note.strip(),
            'latency': 9999,
            'speed': 0.0,
            'uptime': 0,
            'source': 'lancelot',
        })
    return rows

def parse_ipdb(txt):
    """解析 IPDB 格式: IP（无地区标签，需要查询）"""
    ips = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or ':' in line:
            continue
        ips.append(line)
    return ips

def query_ip_regions(ips):
    """用 ip-api.com 批量查询 IP 地区（最多 100 个一批）"""
    regions = {}
    batch_size = 100
    for i in range(0, len(ips), batch_size):
        batch = ips[i:i+batch_size]
        payload = json.dumps([{'query': ip, 'fields': 'countryCode'} for ip in batch]).encode()
        try:
            req = urllib.request.Request(
                'http://ip-api.com/batch?fields=countryCode',
                data=payload,
                headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                results = json.loads(r.read().decode())
                for ip, res in zip(batch, results):
                    cc = res.get('countryCode', '')
                    if cc in REGIONS:
                        regions[ip] = cc
        except Exception as e:
            print(f'  IP 查询失败 batch {i//batch_size}: {e}')
        time.sleep(1.5)  # 速率限制
    return regions

def merge_sources(data):
    """合并三个源，按地区分组"""
    all_rows = collections.defaultdict(list)

    # Xiaobei09
    if 'xiaobei' in data:
        rows = parse_xiaobei(data['xiaobei'])
        for region, items in rows.items():
            all_rows[region].extend(items)

    # LancelotRar
    if 'lancelot' in data:
        rows = parse_lancelot(data['lancelot'])
        for region, items in rows.items():
            all_rows[region].extend(items)

    # IPDB（需要查询地区）
    if 'ipdb' in data:
        ips = parse_ipdb(data['ipdb'])
        print(f'查询 {len(ips)} 个 IPDB IP 的地区...')
        ip_regions = query_ip_regions(ips)
        print(f'  找到 {len(ip_regions)} 个有效地区 IP')
        for ip, region in ip_regions.items():
            all_rows[region].append({
                'addr': f'{ip}:443',
                'note': f'{region} IPDB',
                'latency': 9999,
                'speed': 0.0,
                'uptime': 0,
                'source': 'ipdb',
            })

    return all_rows

def select_nodes(all_rows, quota_per_region=20):
    """按权重选择节点"""
    selected = []
    for region in REGIONS:
        rows = all_rows.get(region, [])
        if not rows:
            continue

        # 按源分组
        by_source = collections.defaultdict(list)
        for r in rows:
            by_source[r['source']].append(r)

        # 按权重分配配额
        region_quota = quota_per_region
        source_quotas = {
            'xiaobei': int(region_quota * SOURCES['xiaobei']['weight']),
            'lancelot': int(region_quota * SOURCES['lancelot']['weight']),
            'ipdb': int(region_quota * SOURCES['ipdb']['weight']),
        }

        # 从每个源选择节点
        for source, quota in source_quotas.items():
            candidates = by_source.get(source, [])
            if not candidates:
                continue
            # 排序：Xiaobei 按速度/延迟，其他按原顺序
            if source == 'xiaobei':
                candidates.sort(key=lambda x: (-x['speed'], x['latency']))
            selected.extend(candidates[:quota])

        # 如果不够，用 Xiaobei 补足
        if len([s for s in selected if s['addr'] in [r['addr'] for r in rows]]) < region_quota:
            existing = {s['addr'] for s in selected}
            for r in by_source.get('xiaobei', []):
                if r['addr'] not in existing:
                    selected.append(r)
                    existing.add(r['addr'])
                    if len(existing) >= region_quota:
                        break

    return selected

def vless_check(addr):
    """端到端：WS 升级 + VLESS 请求 ifconfig.me:80，返回 (ok, 出口IP)"""
    ip, port = addr.rsplit(':', 1)
    port = int(port)
    U = uuidmod.UUID(VLESS_UUID).bytes
    try:
        sock = socket.create_connection((ip, port), timeout=10)
        ss = ssl.create_default_context().wrap_socket(sock, server_hostname=PAGES_HOST)
        key = base64.b64encode(os.urandom(16)).decode()
        ss.sendall((f'GET /?ed=2560 HTTP/1.1\r\nHost: {PAGES_HOST}\r\nUpgrade: websocket\r\n'
                    f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                    'Sec-WebSocket-Version: 13\r\nUser-Agent: Mozilla/5.0\r\n\r\n').encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            d = ss.recv(1024)
            if not d:
                raise Exception('WS握手断')
            buf += d
        if b'101' not in buf[:20]:
            raise Exception('非101')
        th = 'ifconfig.me'
        hdr = b'\x00' + U + b'\x00' + b'\x01' + struct.pack('!H', 80) + b'\x02' + bytes([len(th)]) + th.encode()
        payload = hdr + (f'GET /ip HTTP/1.1\r\nHost: ifconfig.me\r\nUser-Agent: curl/8\r\n'
                         'Accept: */*\r\nConnection: close\r\n\r\n').encode()
        mask = os.urandom(4)
        ln = len(payload)
        ss.sendall(bytes([0x82, 0x80 | 126]) + struct.pack('!H', ln) +
                   mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        ss.settimeout(10)
        out = b''
        t0 = time.time()
        while time.time() - t0 < 8:
            try:
                d = ss.recv(4096)
                if not d:
                    break
                out += d
            except socket.timeout:
                break
        ss.close()
        m = re.search(rb'\r\n\r\n([^\r\n]+)', out)
        if m:
            return True, m.group(1).decode()
        return False, None
    except Exception as e:
        return False, None

def main():
    test_mode = '--test' in sys.argv
    test_count = 0
    if test_mode:
        idx = sys.argv.index('--test')
        test_count = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 8

    print('=== 三源聚合家宽入口 IP 池刷新 ===')

    # 1. 拉取上游
    data = fetch_upstream()
    if not data:
        sys.exit('所有上游拉取失败')

    # 2. 合并源
    all_rows = merge_sources(data)
    total = sum(len(v) for v in all_rows.values())
    print(f'\n合并后共 {total} 个候选 IP')

    # 3. 选择节点
    selected = select_nodes(all_rows, quota_per_region=20)
    print(f'选中 {len(selected)} 个节点')

    # 4. 输出 RES候选池_六地区.txt
    res_lines = []
    for region in REGIONS:
        rows = all_rows.get(region, [])
        if not rows:
            continue
        res_lines.append(f'# {region} ({len(rows)} 个)')
        for r in rows:
            res_lines.append(f"{r['addr']}#{r['note']}")
        res_lines.append('')
    with open(os.path.join(HERE, 'RES候选池_六地区.txt'), 'w') as f:
        f.write('\n'.join(res_lines))
    print(f'输出 RES候选池_六地区.txt: {len(res_lines)} 行')

    # 5. 输出 优选池.txt
    pool_lines = []
    for r in selected:
        pool_lines.append(f"{r['addr']}#{r['note']}")
    with open(os.path.join(HERE, '优选池.txt'), 'w') as f:
        f.write('\n'.join(pool_lines))
    print(f'输出 优选池.txt: {len(pool_lines)} 行')

    # 6. 测试模式：端到端验证
    if test_mode and test_count > 0:
        print(f'\n=== 端到端测试 (每地区前 {test_count} 个) ===')
        verified = []
        for region in REGIONS:
            rows = [r for r in selected if r['addr'].startswith(region) or r['note'].startswith(region)]
            if not rows:
                continue
            print(f'\n{region}:')
            for r in rows[:test_count]:
                ok, exit_ip = vless_check(r['addr'])
                status = 'OK' if ok else 'FAIL'
                print(f'  {r["addr"]} -> {exit_ip or "timeout"} [{status}]')
                if ok:
                    verified.append((r['addr'], exit_ip))
                time.sleep(0.5)

        # 输出 家宽入口_已验证.txt
        ver_lines = []
        for addr, exit_ip in verified:
            ver_lines.append(f'{addr}#出口 {exit_ip}')
        with open(os.path.join(HERE, '家宽入口_已验证.txt'), 'w') as f:
            f.write('\n'.join(ver_lines))
        print(f'\n输出 家宽入口_已验证.txt: {len(ver_lines)} 行')

    print('\n=== 完成 ===')

if __name__ == '__main__':
    main()
