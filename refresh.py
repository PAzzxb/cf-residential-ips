#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""家宽入口 IP 池刷新工具
用法:
  python3 refresh.py            # 拉最新上游清单 → 筛六地区住宅候选 → 更新 RES候选池_六地区.txt
  python3 refresh.py --test 8   # 同上，并对每地区前 8 个做 VLESS 端到端实测 → 更新 家宽入口_已验证.txt
                                  （小样本探测，不会触发批量风控）
依赖: 仅 Python 标准库（--test 需能直连网络）
上游: Xiaobei09/proxyip 仓库，每 2 小时 CI 自动验证一次
"""
import base64, collections, os, re, socket, ssl, struct, sys, time, uuid as uuidmod
import urllib.request

UPSTREAM = 'https://raw.githubusercontent.com/Xiaobei09/proxyip/main/data/valid/all.txt'
# 你的节点信息（改这里）
PAGES_HOST = 'static-assets-6bm.pages.dev'
VLESS_UUID = '66d6b88c-1956-42ee-a412-a528f98205d9'
REGIONS = ('TW', 'HK', 'SG', 'US', 'JP', 'KR')
HERE = os.path.dirname(os.path.abspath(__file__))

# 已验证清单：优先仓库根的 家宽入口_已验证*.txt，没有就落在根目录
VERIFIED_GLOB = '家宽入口_已验证*.txt'

def fetch_upstream():
    for i in range(3):
        try:
            req = urllib.request.Request(UPSTREAM, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            print(f'拉取重试{i}: {e}'); time.sleep(4)
    sys.exit('上游拉取失败')

def parse(txt):
    rows = collections.defaultdict(list)
    for line in txt.splitlines():
        line = line.strip()
        if '#' not in line: continue
        addr, note = line.split('#', 1)
        if ':' not in addr: continue
        m = re.match(r'^[\U0001F1E6-\U0001F1FF]{2}([A-Z]{2})', note)
        if not m or m.group(1) not in REGIONS: continue
        if 'RES' not in note.split('-'): continue
        lat = re.search(r'-(\d+)ms', note); spd = re.search(r'-([\d.]+)MB/s', note)
        up = re.search(r'-U(\d+)', note)
        rows[m.group(1)].append((int(up.group(1)) if up else 0,
                                 float(spd.group(1)) if spd else 0.0,
                                 addr, note))
    return rows

def vless_check(addr):
    """端到端：WS 升级 + VLESS 请求 ifconfig.me:80，返回 (ok, 出口IP)"""
    ip, port = addr.rsplit(':', 1); port = int(port)
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
            if not d: raise Exception('WS握手断')
            buf += d
        if b'101' not in buf[:20]: raise Exception('非101')
        th = 'ifconfig.me'
        hdr = b'\x00' + U + b'\x00' + b'\x01' + struct.pack('!H', 80) + b'\x02' + bytes([len(th)]) + th.encode()
        payload = hdr + (f'GET /ip HTTP/1.1\r\nHost: ifconfig.me\r\nUser-Agent: curl/8\r\n'
                         'Accept: */*\r\nConnection: close\r\n\r\n').encode()
        mask = os.urandom(4); ln = len(payload)
        ss.sendall(bytes([0x82, 0x80 | 126]) + struct.pack('!H', ln) +
                   mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        ss.settimeout(10)
        out = b''; t0 = time.time()
        while time.time() - t0 < 8:
            try:
                d = ss.recv(4096)
                if not d: break
                out += d
            except socket.timeout: continue
            except Exception: break
        ss.close()
        frames = []; pos = 0
        while pos + 2 <= len(out):
            b2 = out[pos + 1]; off = pos + 2; l = b2 & 0x7F
            if l == 126: l = struct.unpack('!H', out[pos+2:pos+4])[0]; off = pos + 4
            elif l == 127: off = pos + 10
            if b2 & 0x80: off += 4
            frames.append(out[off:off + l]); pos = off + l
        body = b''.join(frames).decode('latin1', 'replace')
        ok = 'HTTP/1.1 200' in body
        m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]{20,45})\s*$', body.strip())
        return ok, (m.group(1)[:40] if m else '')
    except Exception as e:
        return False, type(e).__name__

if __name__ == '__main__':
    topn = 0
    if '--test' in sys.argv:
        i = sys.argv.index('--test')
        topn = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 8
    txt = fetch_upstream()
    rows = parse(txt)
    print('上游时间:', time.strftime('%Y-%m-%d %H:%M'), '| 六地区住宅候选:',
          {k: len(v) for k, v in rows.items()})
    names = {'TW': '台湾', 'HK': '香港', 'SG': '新加坡', 'US': '美国', 'JP': '日本', 'KR': '韩国'}
    out = []
    for cc in REGIONS:
        lst = sorted(rows[cc], key=lambda x: (-x[0], -x[1]))
        out.append(f'# ===== {cc} {names[cc]} 住宅候选 {len(lst)} 个（按存活↓速度↓）=====')
        for up, spd, addr, note in lst:
            out.append(f'{addr}#{note}')
        out.append('')
    cand_path = os.path.join(HERE, 'RES候选池_六地区.txt')
    open(cand_path, 'w').write('\n'.join(out) + '\n')
    print('已更新:', cand_path)

    # ===== 生成「优选池.txt」：干净、可直接喂 KV ADD.txt =====
    # 规则：每地区取「最快的 N 个」(保证地理覆盖，不卡死速度门槛导致整区消失)；
    #      纯 IP:PORT#🇺🇸美国01 格式（无垃圾/无注释行）
    flag = {'TW':'🇹🇼 台湾','HK':'🇭🇰 香港','SG':'🇸🇬 新加坡','US':'🇺🇸 美国','JP':'🇯🇵 日本','KR':'🇰🇷 韩国'}
    quota = {'HK':8,'TW':8,'SG':8,'JP':10,'KR':8,'US':10}  # 就近优先，US 兜底
    clean = []
    stat = {}
    for cc in REGIONS:
        # rows[cc] = (up, spd, addr, note)；优先 fast，再按速度降序
        cand = sorted(rows[cc], key=lambda x: (0 if 'fast' in x[3] else 1, -x[1]))
        cand = cand[:quota.get(cc, 8)]
        stat[cc] = len(cand)
        for i, (up, spd, addr, note) in enumerate(cand, 1):
            clean.append(f'{addr}#{flag[cc]} {i:02d} ({spd:.0f}MB/s)')
    pool_path = os.path.join(HERE, '优选池.txt')
    open(pool_path, 'w').write('\n'.join(clean) + '\n')
    print('已更新:', pool_path, '| 各地区取用数:', stat, '| 合计', len(clean))
    if topn:
        verified = []
        for cc in REGIONS:
            lst = sorted(rows[cc], key=lambda x: (-x[0], -x[1]))[:topn]
            for up, spd, addr, note in lst:
                ok, egress = vless_check(addr)
                print(f'  {cc} {addr:22s} {"✓ " + egress if ok else "✗ " + egress}')
                if ok: verified.append(f'{addr}#{cc} 家宽')
                time.sleep(0.3)
        vp = os.path.join(HERE, '家宽入口_已验证.txt')
        open(vp, 'w').write('\n'.join(verified) + '\n')
        print(f'\n端到端验证 {len(verified)} 个可用 → {vp}')
        print('提示: 把该文件内容追加到 KV ADD.txt 即可上线节点')

