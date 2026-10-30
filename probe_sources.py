#!/usr/bin/env python3
"""Asks every source the jobs read how it answers, to find out why some turn the jobs away.

For each feed and list page it records the status, which firewall answered (Cloudflare, Sucuri, Wordfence…),
what robots.txt says about this job, and whether an official alternative answers instead: the site's Atom feed,
the WordPress JSON API, Substack's archive API, an AMP copy of a page. Nothing here pretends to be a browser:
it tries the job's User-Agent as it is and as it would be with the repository's address in it.

    python3 probe_sources.py                 # writes probe/report.json and probe/report.md
    python3 probe_sources.py --save-pages    # also keeps the list pages it read, in probe/pages/

Run it from GitHub's runners (.github/workflows/probe.yml): sites treat their addresses differently from a laptop's.
"""

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.robotparser

import build_feed as feed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe")
SAVE_PAGES = "--save-pages" in sys.argv

OLD_AGENT = "DeepCutsUnearth/1.0 (album feed reader; +https://github.com/)"
NEW_AGENT = feed.USER_AGENT if feed.USER_AGENT != OLD_AGENT else \
    "DeepCutsUnearth/1.1 (+https://github.com/candidtheartist/deepcuts-unearth)"
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.8"
PAGE_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"

# What a firewall's answer looks like, by header or by the page it sends.
FIREWALLS = [
    ("cloudflare challenge", lambda h, b: h.get("cf-mitigated") == "challenge" or b"challenge-platform" in b or b"Just a moment..." in b),
    ("cloudflare block", lambda h, b: b"Attention Required! | Cloudflare" in b or b"cf-error-details" in b),
    ("sucuri", lambda h, b: "x-sucuri-id" in h or b"Sucuri WebSite Firewall" in b),
    ("wordfence", lambda h, b: b"Wordfence" in b or b"wfls-" in b),
    ("datadome", lambda h, b: "x-datadome" in h or b"captcha-delivery.com" in b),
    ("perimeterx", lambda h, b: b"px-captcha" in b or b"_pxAppId" in b),
    ("imperva", lambda h, b: "x-iinfo" in h or b"_Incapsula_Resource" in b),
    ("akamai", lambda h, b: b"Reference&#32;&#35;" in b or (b"Access Denied" in b and "akamai" in h.get("server", "").lower())),
    ("vercel challenge", lambda h, b: h.get("x-vercel-mitigated") == "challenge"),
    ("ddos-guard", lambda h, b: "ddos-guard" in h.get("server", "").lower()),
    ("mod_security", lambda h, b: b"Mod_Security" in b or b"ModSecurity" in b),
]


def get(url, agent, accept, compressed=False, timeout=30):
    """{status, final, headers, size, kind, firewall, body} for one request, never raising."""
    with tempfile.NamedTemporaryFile() as head, tempfile.NamedTemporaryFile() as body:
        command = ["curl", "--silent", "--location", "--max-time", str(timeout), "--user-agent", agent,
                   "--header", f"Accept: {accept}", "--dump-header", head.name, "--output", body.name,
                   "--write-out", "%{http_code} %{url_effective}", url]
        if compressed:
            command.insert(1, "--compressed")
        result = subprocess.run(command, capture_output=True)
        out = result.stdout.decode(errors="replace").split(" ", 1)
        raw_headers = open(head.name, "rb").read().decode(errors="replace")
        data = open(body.name, "rb").read()
    # With --location the headers of every hop are there: keep the last.
    blocks = [b for b in re.split(r"\r?\n\r?\n", raw_headers) if b.strip()]
    headers = {}
    for line in (blocks[-1].splitlines()[1:] if blocks else []):
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    start = data.lstrip()[:200].lower()
    kind = ("feed" if start.startswith(b"<?xml") or b"<rss" in start or b"<feed" in start
            else "json" if start[:1] in (b"{", b"[")
            else "html" if b"<html" in start or b"<!doctype" in start
            else "empty" if not data else "other")
    return {
        "status": int(out[0]) if out and out[0].isdigit() else 0,
        "error": result.stderr.decode(errors="replace").strip() if result.returncode else None,
        "final": out[1] if len(out) > 1 else url,
        "server": headers.get("server"),
        "headers": {k: v for k, v in headers.items() if k in ("server", "cf-ray", "cf-mitigated", "retry-after", "x-sucuri-id",
                                                               "x-datadome", "x-vercel-mitigated", "content-type", "x-robots-tag")},
        "size": len(data),
        "kind": kind,
        "firewall": next((name for name, test in FIREWALLS if test(headers, data)), None),
        "title": (re.search(rb"<title[^>]*>(.*?)</title>", data, re.S | re.I) or [None, b""])[1].decode(errors="replace").strip()[:120],
        "body": data,
    }


def brief(answer):
    return {k: v for k, v in answer.items() if k != "body"}


ROBOTS = {}


def robots(url):
    """What the site's robots.txt says about this job, the way RFC 9309 reads it."""
    parts = urllib.parse.urlsplit(url)
    site = f"{parts.scheme}://{parts.netloc}"
    if site not in ROBOTS:
        answer = get(site + "/robots.txt", NEW_AGENT, "text/plain,*/*")
        rules = urllib.robotparser.RobotFileParser()
        text = answer["body"].decode(errors="replace")
        rules.parse(text.splitlines())
        ROBOTS[site] = {
            "status": answer["status"], "firewall": answer["firewall"], "rules": rules,
            "signals": sorted(set(re.findall(r"(?im)^\s*content-signal:\s*(.+)$", text))),
            "names_us": bool(re.search(r"(?im)^\s*user-agent:\s*deepcuts", text)),
        }
    entry = ROBOTS[site]
    status = entry["status"]
    if status == 200:
        verdict = "allowed" if entry["rules"].can_fetch(NEW_AGENT, url) else "disallowed"
    elif status in (401, 403):
        verdict = "robots.txt refused (treat as disallowed)"
    elif 400 <= status < 500:
        verdict = "no robots.txt (allowed)"
    else:
        verdict = f"robots.txt unreachable ({status or 'no answer'})"
    return {"robots": verdict, "robotsStatus": status, "robotsFirewall": entry["firewall"],
            "contentSignal": entry["signals"], "robotsNamesUs": entry["names_us"]}


def alternatives(url, is_feed):
    """Official other ways in to the same thing."""
    parts = urllib.parse.urlsplit(url)
    site = f"{parts.scheme}://{parts.netloc}"
    found = []
    if is_feed:
        if parts.netloc.endswith("substack.com"):
            found.append(site + "/api/v1/archive?sort=new&limit=12")
        base = url.rstrip("/")
        if base.endswith("/feed"):
            found.append(base + "/atom/")
            found.append(base + "/rss2/")
        found.append(site + "/?feed=rss2")
        found.append(site + "/wp-json/wp/v2/posts?per_page=10&_fields=date,link,title")
    else:
        slug = [p for p in parts.path.split("/") if p][-1:] or [""]
        found.append(f"{site}/wp-json/wp/v2/posts?slug={slug[0]}&_fields=link,title,content")
        found.append(url.rstrip("/") + "/amp/")
        found.append(f"{site}/wp-json/oembed/1.0/embed?url={urllib.parse.quote(url, safe='')}")
    return found


def probe(target):
    url, is_feed = target["url"], target["feed"]
    accept = FEED_ACCEPT if is_feed else PAGE_ACCEPT
    now = get(url, OLD_AGENT, "*/*")
    better = get(url, NEW_AGENT, accept, compressed=True)
    report = {**target, **robots(url), "asIs": brief(now), "withContact": brief(better)}
    good = lambda a: a["status"] == 200 and not a["firewall"] and (a["kind"] in ("feed", "json") if is_feed else a["kind"] == "html")
    if not good(now) or not good(better) or (not is_feed and target.get("entries", 99) < 5):
        report["alternatives"] = []
        for alt in alternatives(url, is_feed):
            answer = get(alt, NEW_AGENT, "application/json, " + accept, compressed=True)
            note = brief(answer)
            if answer["kind"] == "json":
                try:
                    data = json.loads(answer["body"])
                    note["items"] = len(data) if isinstance(data, list) else len(data.get("posts", data)) if isinstance(data, dict) else 0
                except ValueError:
                    pass
            report["alternatives"].append({"url": alt, **note})
            if SAVE_PAGES and not is_feed and answer["status"] == 200 and answer["size"] > 500:
                save(alt, answer["body"])
    if SAVE_PAGES and not is_feed:
        save(url, better["body"] if better["size"] >= now["size"] else now["body"])
    return report


def save(url, data):
    os.makedirs(os.path.join(OUT, "pages"), exist_ok=True)
    name = feed.slugify(re.sub(r"^https?://(www\.)?", "", url))[:120]
    with open(os.path.join(OUT, "pages", name + ".html"), "wb") as f:
        f.write(data[:3_000_000])


def targets():
    found, seen = [], set()

    def add(url, name, is_feed, **extra):
        if url and url not in seen:
            seen.add(url)
            found.append({"url": url, "name": name, "feed": is_feed, **extra})

    for source in feed.SOURCES + feed.SONG_SOURCES:
        add(source.get("feed"), source["name"], True)
    add(feed.REDDIT_FRESH, "Reddit", True)
    add("https://api.kexp.org/v2/plays/?limit=5", "KEXP", True)
    add("https://www.yearendlists.com/category/2025-albums", "yearendlists.com", False)
    try:
        import build_lists
        memory = json.load(open(build_lists.MEMORY))
        for url, page in memory.get("pages", {}).items():
            if len(page.get("entries") or []) < 5:
                add(url, urllib.parse.urlsplit(url).netloc, False, entries=len(page.get("entries") or []))
        for lists in json.load(open(build_lists.MIDYEAR)).values():
            if isinstance(lists, list):
                for item in lists:
                    add(item["url"], item["outlet"], False, entries=len((memory["pages"].get(item["url"]) or {}).get("entries") or []))
    except (OSError, ValueError, KeyError) as error:
        print(f"lists left out: {error}", file=sys.stderr)
    return found


def summary(report):
    lines = ["| Source | robots.txt | As is | With contact | Firewall | Alternatives that answer |", "|---|---|---|---|---|---|"]
    for r in report:
        a, b = r["asIs"], r["withContact"]
        alts = [f"{x['url'].split('/', 3)[-1][:40]} ({x['status']} {x['kind']}{', ' + str(x['items']) + ' items' if 'items' in x else ''})"
                for x in r.get("alternatives", []) if x["status"] == 200 and x["kind"] in ("json", "feed", "html")]
        lines.append(f"| {r['name']} ({'feed' if r['feed'] else 'page'}) | {r['robots']} | {a['status']} {a['kind']} {a['size']} | "
                     f"{b['status']} {b['kind']} {b['size']} | {a['firewall'] or b['firewall'] or a['server'] or ''} | {'; '.join(alts)} |")
    return "\n".join(lines)


def main():
    work = targets()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        report = list(pool.map(probe, work))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    table = summary(report)
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write(table + "\n")
    trouble = [r for r in report if r["asIs"]["status"] != 200 or r["asIs"]["firewall"] or r["withContact"]["status"] != 200
               or (not r["feed"] and r.get("entries", 99) < 5)]
    print(summary(trouble))


if __name__ == "__main__":
    main()
