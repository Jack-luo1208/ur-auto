#!/usr/bin/env python3
"""Build verified pinned nodes and render them as Clash YAML (and raw socks links)."""
import json
import subprocess
import time
from urllib.parse import urlparse

DC_HINTS = (
    "amazon", "aws", "google", "microsoft", "azure", "digitalocean", "ovh", "hetzner", "linode",
    "akamai", "cloudflare", "leaseweb", "vultr", "contabo", "oracle", "alibaba", "tencent", "choopa",
    "psychz", "coloc", "hosting", "data center", "datacenter", "vpn", "proxy", "m247", "gcore",
    "scaleway", "upcloud", "hostwinds", "cogent", "constant company", "cloudnium", "kamatera",
    "zenlayer", "ionos", "quadranet", "hivelocity", "serverhub", "hostpapa", "xirgo", "gocodeit",
    "octopus", "ipxo", "ztnet", "web solution", "vps", "cloud", "server", "dedicated", "colo",
    "colocation", "datacamp", "hostinger", "godaddy", "namecheap", "hosting solution",
    # VPS/租用品牌名录：这些名字本身就是机房，ip-api 常误判成住宅
    "zouter", "v.ps", "akile", "cloudie", "pq hosting", "pqhosting", "xtom", "dedipath",
    "bandwagon", "hostus", "sharktech", "servarica", "greencloud", "virmach", "racknerd",
    "colocrossing", "hostdare", "cloudcone", "alphavps", "layerstack", "hosthatch", "buyvm",
    "frantech", "naranja", "kuroit", "搬瓦工", "vultr", "linode", "digitalocean",
)

# a real consumer line usually wears one of these words somewhere in ISP/org/AS
RESIDENTIAL_HINTS = (
    "at&t", "att", "charter", "spectrum", "comcast", "xfinity", "verizon", "centurylink", "frontier",
    "cox", "optimum", "altice", "mediacom", "windstream", "brightspeed", "ziply", "sonic", "google fiber",
    "atlas networks", "burlington telecom", "cable", "broadband", "communications", "telecom", "fiber",
    "fibre", "wireless", "internet service", "telephone", "utilities", "pcs", "cellular",
)

# second-opinion sources that sell/lease datacenter IPs
BAD_DOMAINS = ("ipxo.com", "m247.com", "zenlayer.com", "hostwinds.com", "quadranet.com",
               "zouter.io", "v.ps", "akile.io", "cloudie.hk", "pq.hosting", "xtom.com",
               "dedipath.com", "racknerd.com", "colocrossing.com", "buyvm.net", "frantech.ca")

IP_FIELDS = "status,country,regionName,city,isp,org,as,proxy,hosting,query"

_DOH_CACHE = {}


def resolve_host(host):
    """Return an address usable for outgoing connections.

    Some hosts (Clash/TUN style local DNS) answer with 198.18.0.0/15 fake IPs; those are
    unreachable for a direct SOCKS dial, so fall back to public DoH and use the real IP.
    """
    if host in _DOH_CACHE:
        return _DOH_CACHE[host]
    addr = host
    try:
        import socket
        infos = socket.getaddrinfo(host, None)
        ips = [i[4][0] for i in infos]
        fake = [ip for ip in ips if ip.startswith("198.18.") or ip.startswith("198.19.")]
        if fake and len(fake) == len(ips):
            addr = _doh_a(host) or host
    except Exception:
        addr = _doh_a(host) or host
    _DOH_CACHE[host] = addr
    return addr


def _doh_a(host):
    import urllib.request
    for endpoint in ("https://dns.google/resolve?name=%s&type=A",
                     "https://cloudflare-dns.com/dns-query?name=%s&type=A"):
        try:
            req = urllib.request.Request(endpoint % host, headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            for ans in data.get("Answer", []):
                if ans.get("type") == 1 and ans.get("data"):
                    return ans["data"]
        except Exception:
            continue
    return None


def _curl(url, proxy, timeout=30):
    """Return (http_code, body)."""
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-o", "-", "-w", "\n@@%{http_code}",
           "--proxy", proxy, url]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout or ""
    if "\n@@" not in out:
        return 0, ""
    body, code = out.rsplit("\n@@", 1)
    try:
        return int(code.strip()), body
    except ValueError:
        return 0, body


def ip_info(proxy, tries=3):
    for _ in range(tries):
        code, body = _curl("http://ip-api.com/json/?fields=" + IP_FIELDS, proxy, 25)
        if code == 200:
            try:
                data = json.loads(body)
                if data.get("status") == "success":
                    return data
            except Exception:
                pass
        time.sleep(2)
    return {}


def clean_exit(info, want_city=None, want_country=None, require_hint=False, strict_city=False):
    """Country-agnostic residential judgement: double false, no hosting signals.

    Country mismatch is a hard fail (a HK provider must exit in HK). City is only
    a soft signal: geolocation of a residential line often names a nearby town,
    so it rejects only when `strict_city` is on. The "residential wording" check
    likewise applies only when `require_hint` is set.
    """
    if not info:
        return False, ["no exit answer"]
    bad = []
    if info.get("proxy") is not False:
        bad.append("proxy=%s" % info.get("proxy"))
    if info.get("hosting") is not False:
        bad.append("hosting=%s" % info.get("hosting"))
    blob = ((info.get("isp") or "") + " " + (info.get("org") or "") + " " + (info.get("as") or "")).lower()
    hit = [h for h in DC_HINTS if h in blob]
    if hit:
        bad.append("dc-ish:" + ",".join(hit[:2]))
    elif require_hint and not any(h in blob for h in RESIDENTIAL_HINTS):
        bad.append("no residential evidence")
    if want_country and info.get("country"):
        got = info["country"].lower()
        want = want_country.lower()
        if got != want and want not in got and got not in want:
            bad.append("country=%s" % info["country"])
    if strict_city and want_city and info.get("city") and want_city.isascii():
        if info["city"].lower() != want_city.lower():
            bad.append("city=%s" % info["city"])
    return (not bad), bad


def second_opinion(ip):
    """Cross-check the exit on ipwho.is: leased/VPS ranges usually leak their broker domain."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://ipwho.is/" + ip, timeout=20) as resp:
            d = json.loads(resp.read())
    except Exception:
        return {}
    conn = d.get("connection") or {}
    sec = d.get("security") or {}
    dom = (conn.get("domain") or "").lower()
    out = {"isp": conn.get("isp"), "org": conn.get("org"), "domain": dom,
           "hosting": sec.get("hosting"), "proxy": sec.get("proxy"), "vpn": sec.get("vpn")}
    out["bad"] = bool(dom and any(dom.endswith(b) for b in BAD_DOMAINS)) or out.get("hosting") is True
    return out


def discover_locations(api, cfg, log=print):
    """Return city locations ordered by country priority, worldwide.

    `country_priority` decides the order (nearby countries first for latency),
    `exclude_countries` drops ones the caller never wants, and every remaining
    country with providers is appended so the pool never starves.
    """
    try:
        all_locs = api.find_locations("")
    except Exception as exc:
        log("  discovery failed: %s" % str(exc)[:80])
        return []
    countries = {}
    for loc in all_locs:
        if loc.get("location_type") != "country":
            continue
        cc = (loc.get("country_code") or "").upper()
        if not cc or (loc.get("provider_count") or 0) <= 0:
            continue
        countries[cc] = loc

    exclude = {c.upper() for c in cfg.get("exclude_countries", [])}
    priority = [c.upper() for c in cfg.get("country_priority", [])]
    ordered = [cc for cc in priority if cc in countries and cc not in exclude]
    rest = [cc for cc, _ in sorted(countries.items(), key=lambda kv: -(kv[1].get("provider_count") or 0))
            if cc not in ordered and cc not in exclude]

    plan = []
    max_countries = int(cfg.get("max_countries_per_run", 8))
    min_city = int(cfg.get("min_city_providers", 1))
    per_country = int(cfg.get("cities_per_country", 2))
    for cc in (ordered + rest)[:max_countries]:
        country = countries[cc]
        try:
            locs = api.find_locations(country.get("name") or cc)
        except Exception:
            locs = []
        cities = [l for l in locs
                  if l.get("location_type") == "city" and (l.get("provider_count") or 0) >= min_city]
        cities.sort(key=lambda l: (l.get("stable") is True, l.get("provider_count") or 0), reverse=True)
        for city in cities[:per_country]:
            city["_cc"] = cc
            city["_country"] = country.get("name") or cc
            plan.append(city)
    log("  plan: %d city locations across %d countries" % (len(plan), len({p["_cc"] for p in plan})))
    return plan


def mint_node(api, api_key, location, rank=0, attempts=3, log=print, require_hint=False, strict_city=False):
    """Pin one provider in a location and return a verified node dict, or None.

    `location` is a location dict from discovery (preferred) or a text query.
    """
    if isinstance(location, str):
        locs = [l for l in api.find_locations(location)
                if l.get("location_type") in ("city", "region", "country")]
        if not locs:
            log("  no location for %s" % location)
            return None
        loc = locs[0]
        loc["_cc"] = (loc.get("country_code") or "").upper()
        loc["_country"] = loc.get("country")
    else:
        loc = dict(location)

    label = "%s/%s" % (loc.get("_cc") or loc.get("country_code") or "??", loc.get("city") or loc.get("name"))
    providers = api.find_providers(loc["location_id"], count=8)
    providers.sort(key=lambda p: p.get("estimated_bytes_per_second") or 0, reverse=True)
    for prov in providers[:attempts]:
        pt = api.auth_client(api_key, prov["client_id"])
        if not pt.get("auth_token"):
            continue
        host = pt.get("proxy_host") or "crisp.bringyour.com"
        s_port = pt.get("socks_proxy_port")
        h_port = pt.get("http_proxy_port") or (int(s_port) + 1)
        dial = resolve_host(host)
        socks = "socks5h://%s:@%s:%s" % (pt["auth_token"], dial, s_port)
        httpp = "http://%s:@%s:%s" % (pt["auth_token"], dial, h_port)
        time.sleep(3)
        info = ip_info(httpp)
        ok, why = clean_exit(info, loc.get("city"), want_country=loc.get("_country"),
                             require_hint=require_hint, strict_city=strict_city)
        if ok:
            op = second_opinion(info.get("query") or "")
            if op.get("bad"):
                ok, why = False, ["second opinion: %s/%s" % (op.get("org") or op.get("isp"), op.get("domain"))]
        if not ok:
            log("  reject %s: %s" % (label, "; ".join(why)))
            api.remove_client(api_key, pt["client_id"])
            continue
        g_code = 0
        for _ in range(3):
            g_code, _ = _curl("https://www.google.com/generate_204", socks, 35)
            if g_code == 204:
                break
            time.sleep(3)
        x_code = 0
        for _ in range(3):
            x_code, _ = _curl("https://x.com/", socks, 40)
            if x_code == 200:
                break
            time.sleep(2)
        if g_code != 204 or x_code != 200:
            log("  reject %s: checks g204=%s x=%s" % (label, g_code, x_code))
            api.remove_client(api_key, pt["client_id"])
            continue
        cc = (loc.get("_cc") or "XX").upper()
        city = info.get("city") or loc.get("city") or "City"
        isp = (info.get("isp") or "ISP").split()[0]
        node = {
            "name": "UR-%s-%s-%s-%02d" % (cc, city.replace(" ", ""), isp, rank + 1),
            "type": "socks5",
            "server": host,
            "port": int(s_port),
            "username": pt["auth_token"],
            "password": "",
            "udp": True,
            "ip": info.get("query"),
            "country": info.get("country"),
            "cc": cc,
            "city": city,
            "isp": info.get("isp"),
            "as": info.get("as"),
            "g204": g_code,
            "xcom": x_code,
            "client_id": pt["client_id"],
        }
        log("  node %s %s %s %s g204=%s x=%s" % (info.get("query"), cc, city, info.get("isp"), g_code, x_code))
        return node
    return None


def yq(value):
    return json.dumps(str(value), ensure_ascii=False)


def to_clash(nodes, mixed_port=7890):
    """Render a Clash/Mihomo config. Manual emitter: JSON strings are valid YAML scalars."""
    lines = [
        "# URnetwork free-tier residential pool — generated by ur-auto",
        "mixed-port: %d" % mixed_port,
        "allow-lan: false",
        "mode: rule",
        "log-level: info",
        "ipv6: false",
        "",
        "proxies:",
    ]
    if not nodes:
        lines.append("  []")
    for n in nodes:
        lines += [
            "  - name: %s" % yq(n["name"]),
            "    type: socks5",
            "    server: %s" % yq(n["server"]),
            "    port: %d" % n["port"],
            "    username: %s" % yq(n["username"]),
            "    password: %s" % yq(n.get("password", "")),
            "    udp: %s" % ("true" if n.get("udp") else "false"),
            "    skip-cert-verify: false",
        ]
    names = [n["name"] for n in nodes]
    lines += [
        "",
        "proxy-groups:",
        "  - name: PROXY",
        "    type: select",
        "    proxies:",
    ]
    for nm in names:
        lines.append("      - %s" % yq(nm))
    lines += ["      - AUTO", "      - DIRECT"]
    if names:
        lines += [
            "  - name: AUTO",
            "    type: url-test",
            "    url: http://www.gstatic.com/generate_204",
            "    interval: 300",
            "    tolerance: 100",
            "    proxies:",
        ]
        for nm in names:
            lines.append("      - %s" % yq(nm))
    lines += [
        "",
        "rules:",
        "  - GEOIP,LAN,DIRECT,no-resolve",
        "  - GEOIP,CN,DIRECT",
        "  - MATCH,PROXY",
        "",
    ]
    return "\n".join(lines)


def to_links(nodes):
    out = []
    for n in nodes:
        out.append("socks://%s:@%s:%d#%s" % (n["username"], n["server"], n["port"], n["name"]))
    return "\n".join(out) + ("\n" if out else "")


def is_clash(text):
    """True when the subscription already is a Clash/Mihomo config."""
    low = (text or "").lower()
    return "proxies:" in low and "proxy-groups:" in low and "rules:" in low


def ensure_clash(text, nodes, mixed_port=7890):
    """Return Clash YAML: pass through if the source already is Clash, else convert."""
    if is_clash(text):
        return text, "passthrough"
    return to_clash(nodes, mixed_port=mixed_port), "converted"
