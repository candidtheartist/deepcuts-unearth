#!/usr/bin/env python3
"""Builds lists.json for Deep Cuts' Best of the Year page: the year-end album lists the music press publishes,
added up the way Album of the Year's aggregate does it.

Each publication's list gives an album points for where it placed it: 10 for #1, 8 for #2, 6 for #3, 5 for the
rest of the top 10, 3 for the top 25 and 1 below that. A list in no particular order gives every album on it the
same: 5 when it has 10 albums or fewer, 3 when it has 25 or fewer, 1 when it's longer. An album's score is its
points from every list, and the page ranks albums by it.

Where the lists come from:
- yearendlists.com keeps an index of the year's notable lists, each with a link to the publication's own page.
  It says which lists exist; the albums are read from the publication's page (`read_original`), and the index's
  copy of a list is used only when the page can't be read or doesn't agree with it.
- The feeds the Unearth job reads (build_feed.SOURCES) are searched for "best albums of the year" posts, so a
  list counts the day it's published, before the index has it.
One list per publication: its staff list when it has one, otherwise the longest list one of its writers made.

    python3 build_lists.py            # writes lists.json and lists_memory.json next to this file
    python3 build_lists.py --verbose  # also prints what each list gave
"""

import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse

import build_feed as feed

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "lists.json")
MEMORY = os.path.join(HERE, "lists_memory.json")
MIDYEAR = os.path.join(HERE, "midyear_lists.json")
INDEX = "https://www.yearendlists.com"

# Album of the Year's points.
RANKED_POINTS = [(1, 10), (2, 8), (3, 6), (10, 5), (25, 3)]
RANKED_OTHER = 1
UNRANKED_POINTS = [(10, 5), (25, 3)]
UNRANKED_OTHER = 1

MIN_ENTRIES = 5            # fewer than this isn't a list
MAX_ALBUMS = 300           # per year, in lists.json
MAX_PAGES = 150            # new pages one run may read (index and publications together), a second apart
MAX_LOOKUPS = 200          # new Apple look-ups one run may make (about 20 a minute)
RETRY_DAYS = 3             # a page that couldn't be read is tried again after this long
AGREEMENT = 0.6            # a publication's page has to find this share of the index's albums to be believed
VERSION = 3                # bump when reading changes, so saved pages are read again
YEAR_END_FROM = 10         # this year's year-end lists replace its mid-year ones once there are this many
SO_FAR_FROM = 3            # fewer mid-year lists than this isn't worth a page

YEAR_END = re.compile(r"\b(?:best|top|favou?rite|essential)\b[^:|]*\balbums?\b[^:|]*\b(20\d\d)\b"
                      r"|\balbums? of (?:the year )?(20\d\d)\b|\b(20\d\d)\b[^:|]*\balbums? of the year\b", re.I)
NOT_YEAR_END = re.compile(r"so far|mid-?year|first half|half-?way|\bsongs?\b|\btracks?\b|singles|reissues?|of all time|"
                          r"decade|ranked|most anticipated|upcoming|\bmonth\b|\bweek\b|january|february|march|april|may\b|"
                          r"june|july|august|september|october", re.I)

# "The 50 Best Albums of 2026 (So Far)", "Mid-Year Report: Best Albums of 2026".
SO_FAR = re.compile(r"so far|mid-?year|first half|half-?way", re.I)
NOT_SO_FAR = re.compile(r"\bsongs?\b|\btracks?\b|singles|reissues?|most anticipated|upcoming|\bmonth\b|\bweek\b|"
                        r"january|february|march|april|\bmay\b|june|july|august|september|october", re.I)

# A list entry as publications write it. A rank may lead ("12. ", "#12 ", "No. 12: ").
RANK = r"^(?:#|No\.?\s*)?(?P<rank>\d{1,3})(?:\.|\)|:|\s*[-–—])?\s+"
ENTRY_FORMS = [
    r"[‘“\"'](?P<album>[^’”\"]+?)[’”\"']\s+[–—-]\s+(?P<artist>[^–—:]+?)",            # ‘Album’ – Artist (Time Out)
    r"(?P<artist>[^–—:‘“\"]+?)\s+[–—-]\s+[‘“\"']?(?P<album>[^’”\"]+?)[’”\"']?",  # Artist – Album
    r"(?P<artist>[^:‘“\"]+?):\s+[‘“\"']?(?P<album>[^’”\"]+?)[’”\"']?",          # Artist: Album
    r"(?P<artist>[^,‘“\"]+?),\s+[‘“\"'](?P<album>.+?)[’”\"']",                   # Artist, ‘Album’
    r"(?P<artist>[^,:‘“\"]+?),\s+(?P<album>[^,:‘“\"][^:]*?)",                       # Artist, Album (Billboard, Complex)
    r"(?P<artist>[^|‘“\"]+?)\s+\|\s+[‘“\"']?(?P<album>[^’”\"|]+?)[’”\"']?",       # Artist | Album (Gorilla vs. Bear)
    r"[‘“\"']?(?P<album>[^’”\"]+?)[’”\"']?\s+by\s+(?P<artist>.+?)",                    # Album by Artist (Atwood, The Line of Best Fit)
]
# The artist in bold and the album in italics, with or without anything between (Magnet's "<strong>7) Midlake</strong>
# <em>A Bridge To Far</em>", FLOOD's "<strong>Bill Callahan, <em>My Days of 58</em></strong>"): the markup says which is which.
MARKED = re.compile(r"^\s*(?:(?:#|No\.?\s*)?(?P<rank>\d{1,3})[.):]?\s*)?<(?P<b>b|strong)\b[^>]*>"
                    r"(?:(?P<artist>(?:(?!<(?:i|em)\b).)*?)</(?P=b)>\s*(?:[,|:–—-]\s*)?|(?P<inside>(?:(?!</?(?:i|em|b|strong)\b).)*?))"
                    r"<(?P<i>i|em)\b[^>]*>(?P<album>.*?)</(?P=i)>", re.S | re.I)
# Where one line of a page ends.
LINE_BREAKS = r"(?i)<br\s*/?>|</(?:p|li|div|h[1-6]|figcaption|td)>"
LOOSE_FORMS = (ENTRY_FORMS[2], ENTRY_FORMS[4], ENTRY_FORMS[6])
# An artist's name as one heading and the album as the next, smaller one (The Vinyl Factory).
HEADING_PAIR = re.compile(r"<h([2-4])\b[^>]*>(.*?)</h\1>\s*<h([3-5])\b[^>]*>(.*?)</h\3>", re.S | re.I)
TAIL = r"\s*(?:[\(\[][^\)\]]*[\)\]])?\s*$"  # "(Label)" or "[Label, 2025]"
ENTRY_TAGS = ("h2", "h3", "h4", "strong", "b", "li", "p")
# Headings that label something else ("Produced By: …", "Listen: …").
# Page furniture that reads like an entry: "Chris Published – June 26, 2026", "Words: FLOOD Staff", "Photo: Ran Liwen".
BYLINE = r"(?i)\b(?:published|updated|posted|words|photos?|images?|illustrations?|credit|staff|editor|contributors?)\b|^(?:by|join|sign|subscribe|follow|share|read|more)\b"
DATE = r"(?i)^(?:\d{1,2}\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|^\d{4}$"
LABELS = r"(?i)(?:listen|read|buy|stream|produced by|featuring|feat|label|genre|release(?:d| date)?|written by|for fans of|watch|order|pre-?order|out now|tracklist)\b"


def log(*args):
    feed.log(*args)


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# ---------------------------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------------------------


def points(rank, size):
    """What one list gives an album: by its place on a ranked list, or by the list's length on an unranked one."""
    if rank is not None:
        return next((p for top, p in RANKED_POINTS if rank <= top), RANKED_OTHER)
    return next((p for most, p in UNRANKED_POINTS if size <= most), UNRANKED_OTHER)


# ---------------------------------------------------------------------------------------------
# Reading a list
# ---------------------------------------------------------------------------------------------


def plausible(artist, album):
    """Whether a pair read off a page could be an artist and an album, not a byline, a date or a heading."""
    return (bool(artist and album) and len(artist) <= 80 and len(artist.split()) <= 7 and len(album) <= 120
            and not re.match(LABELS, artist) and not re.search(BYLINE, artist) and not re.match(DATE, album))


def entry(text, strict=False):
    """(rank, artist, album) from one heading of a list ("12. Geese – Getting Killed"), or None.
    `strict`, for lines of running text rather than headings: "Artist: Album" and "Artist, Album" count only with a place
    in front, since any sentence with a colon or a comma would read as one."""
    text = text.strip().strip("*")
    rank = None
    m = re.match(RANK, text)
    if m:
        rank, text = int(m.group("rank")), text[m.end():]
    for form in ENTRY_FORMS:
        if strict and rank is None and form in LOOSE_FORMS:
            continue
        m = re.match("^" + form + TAIL, text)
        if m:
            artist, album = m.group("artist").strip(feed.QUOTES), m.group("album").strip(feed.QUOTES + ".,")
            album = re.sub(r"(?i)^(?:stream|listen to)\s+", "", album)  # NPR's "Stream Lux" button, read with the title
            if plausible(artist, album):
                return rank, artist, album
    return None


def marked_entry(fragment):
    """(rank, artist, album) from one line of a page that sets the artist in bold and the album in italics, or None."""
    m = MARKED.match(fragment)
    if not m:
        return None
    artist, rank = text_of(m.group("artist") or m.group("inside") or ""), int(m.group("rank")) if m.group("rank") else None
    ranked = re.match(RANK, artist)
    if ranked:
        rank, artist = int(ranked.group("rank")), artist[ranked.end():]
    artist, album = artist.strip(feed.QUOTES + " ,|:–—-"), text_of(m.group("album")).strip(feed.QUOTES + ".,")
    return (rank, artist, album) if plausible(artist, album) else None


def line_entries(body):
    """Entries written one to a line rather than one to an element ("06 <b>Artist</b> | <i>Album</i><br>")."""
    found = []
    for line in re.split(LINE_BREAKS, body):
        line = re.sub(r"^(?:\s*<(?!/?(?:b|strong|i|em)\b)[^>]*>)*", "", line)  # the paragraph or link it starts with
        parsed = marked_entry(line) or entry(text_of(line), strict=True)
        if parsed:
            found.append(parsed)
    return found


def list_entries(page):
    """The albums a publication's list page names, in page order, each with its rank when the page gives one.
    Entries are whatever kind of element (h2, h3, strong…) most often reads as "Artist – Album"; a rank is in the
    entry ("12. …") or in an element just before it (Pitchfork's "12." above each heading)."""
    body = re.sub(r"<(script|style|nav|footer|header)\b[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    best = []
    for tag in ENTRY_TAGS:
        found = []
        for m in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}>", body, re.S | re.I):
            parsed = entry(text_of(m.group(1)))
            if not parsed:
                continue
            rank, artist, album = parsed
            if rank is None:
                before = re.findall(r">\s*(?:#|No\.?\s*)?(\d{1,3})\.?\s*<", body[max(0, m.start() - 600):m.start()])
                rank = int(before[-1]) if before else None
            found.append((rank, artist, album))
        if len(found) > len(best):
            best = found
    by_line = line_entries(body)
    if len(by_line) > len(best):
        best = by_line
    pairs = [(None, text_of(m.group(2)), text_of(m.group(4))) for m in HEADING_PAIR.finditer(body)
             if int(m.group(3)) > int(m.group(1)) and plausible(text_of(m.group(2)), text_of(m.group(4)))]
    if len(pairs) > len(best):
        best = pairs
    seen, unique = set(), []
    for rank, artist, album in best:
        if feed.key(artist, album) not in seen:
            seen.add(feed.key(artist, album))
            unique.append((rank, artist, album))
    ranks = [rank for rank, _, _ in unique if rank is not None]
    # Ranks count only when nearly every entry has one and none repeats; otherwise the numbers were something else.
    if len(ranks) < 0.8 * len(unique) or len(set(ranks)) != len(ranks):
        unique = [(None, artist, album) for _, artist, album in unique]
    return unique


# Firewalls that turn the job away with a page asking for a browser, by what that page says.
CHALLENGE = re.compile(r"challenge-platform|Just a moment\.\.\.|Attention Required! \| Cloudflare|cf-error-details|"
                       r"Sucuri WebSite Firewall|captcha-delivery\.com|_Incapsula_Resource|px-captcha", re.I)
# Sites whose lists are for subscribers: never read from an archived copy, which would get round the paywall.
PAYWALLED = {"economist.com", "thetimes.com", "ft.com", "nytimes.com", "wsj.com", "newyorker.com", "theatlantic.com",
             "telegraph.co.uk", "washingtonpost.com", "ew.com"}
ARCHIVE = "https://archive.org/wayback/available?url="


def site_of(url):
    return re.sub(r"^www\.", "", urllib.parse.urlsplit(url).netloc.lower())


def wordpress_copy(url):
    """The same post from the site's WordPress API (`/wp-json/wp/v2/posts?slug=…`), which many publications answer
    when their firewall won't show the job the page itself: the post's own HTML, or None."""
    parts = urllib.parse.urlsplit(url)
    slug = ([p for p in parts.path.split("/") if p] or [""])[-1]
    if not slug or "." in slug:
        return None
    api = f"{parts.scheme}://{parts.netloc}/wp-json/wp/v2/posts?slug={urllib.parse.quote(slug)}&_fields=content"
    if not feed.allowed(api):
        return None
    try:
        posts = json.loads(feed.fetch(api, accept="application/json"))
        time.sleep(1)
    except Exception:
        return None
    if isinstance(posts, list) and posts and isinstance(posts[0].get("content"), dict):
        return posts[0]["content"].get("rendered") or None
    return None


def archived_copy(url):
    """The Internet Archive's latest copy of a page, as the site served it (`id_`: without the archive's own bar)."""
    try:
        closest = json.loads(feed.fetch(ARCHIVE + urllib.parse.quote(url, safe=""), accept="application/json"))
        snapshot = closest.get("archived_snapshots", {}).get("closest") or {}
        if not snapshot.get("available") or not snapshot.get("timestamp"):
            return None
        page = feed.fetch(f"https://web.archive.org/web/{snapshot['timestamp']}id_/{url}", accept=feed.PAGE_ACCEPT, timeout=60)
        time.sleep(1)
        return page.decode(errors="replace")
    except Exception:
        return None


def read_original(url, now):
    """A publication's list from its own page: (entries, how), entries None when it couldn't be read and `how` saying why.

    The page is read where robots.txt allows. When the site's firewall turns the job away (a Cloudflare check,
    which asks for a browser) or the page has its list somewhere the job can't see, two official copies are tried:
    the site's WordPress API, then the Internet Archive's copy, which is only used where robots.txt allows the
    page and the publication isn't behind a paywall."""
    if not feed.allowed(url):
        return None, "robots.txt"
    page, why = None, None
    try:
        page = feed.fetch(url, accept=feed.PAGE_ACCEPT).decode(errors="replace")
        time.sleep(1)
        if CHALLENGE.search(page[:20000]):
            page, why = None, "firewall"
    except feed.FetchError as error:
        why = "paywall" if error.status == 402 else "firewall" if error.status in (403, 429, 503) else f"http {error.status or 'error'}"
        log(f"  couldn't read {url}: {error}")
    except Exception as error:
        why = "error"
        log(f"  couldn't read {url}: {error}")
    entries = list_entries(page) if page else None
    if entries is not None and len(entries) >= MIN_ENTRIES:
        return entries, "page"
    if why in (None, "firewall"):
        copy = wordpress_copy(url)
        found = list_entries(copy) if copy else None
        if found and len(found) >= MIN_ENTRIES:
            return found, "wordpress"
    if why == "firewall" and site_of(url) not in PAYWALLED and not any(site_of(url).endswith("." + p) for p in PAYWALLED):
        copy = archived_copy(url)
        found = list_entries(copy) if copy and not CHALLENGE.search(copy[:20000]) else None
        if found and len(found) >= MIN_ENTRIES:
            return found, "archive"
    return entries, why or "no list found"


def index_years_lists(year):
    """The index's lists of albums for one year: [{index, outlet, title}], from its category pages."""
    found, page = {}, 1
    while True:
        url = f"{INDEX}/category/{year}-albums" + ("" if page == 1 else f"/page/{page}")
        try:
            listing = feed.fetch(url).decode(errors="replace")
        except Exception as error:
            log(f"  couldn't read {url}: {error}")
            break
        listing = re.sub(r'<a class="main-nav-post-link".*?</a>', " ", listing, flags=re.S)  # "Trending" films and books
        new = 0
        for path in re.findall(rf'href="(/{year}/[^"]+)"', listing):
            if path not in found:
                found[path] = {"index": path}
                new += 1
        if not new or page >= 30:
            break
        page += 1
        time.sleep(1)
    return list(found.values())


def read_index_list(path):
    """One list as the index has it: {outlet, writer, title, url, ranked, entries}."""
    page = feed.fetch(INDEX + path).decode(errors="replace")
    time.sleep(1)
    publisher = re.search(r'<a href="/list_publishers/[^"]+">(.*?)</a>\s*:\s*<a href="([^"]+)"[^>]*class="post_title"[^>]*>(.*?)</a>', page, re.S)
    heading = re.search(r"<h2>(.*?)</h2>", page, re.S)
    if not publisher:
        return None
    name, url, title = text_of(publisher.group(1)), html.unescape(publisher.group(2)), text_of(publisher.group(3))
    if re.search(r"(?i)\b(?:books?|films?|movies?|tv|television|podcasts?|poems?|poetry|songs?|tracks?|singles?|shows?)\b", title):
        return None  # the index's music section has the odd list of something else
    # The heading says "Writer: Publication: Title" for a writer's own list, "Publication: Title" for the staff's.
    parts = [p.strip() for p in text_of(heading.group(1)).split(":")] if heading else []
    writer = parts[0] if len(parts) >= 3 else None
    ranked = bool(re.search(r"<ol\b", page))
    entries = []
    for li in re.findall(r"<li\b([^>]*)>(.*?)</li>", page, re.S):
        artist = re.search(r'class="album-artist"[^>]*>(.*?)</div>', li[1], re.S)
        album = re.search(r'class="album-title"[^>]*>(.*?)</div>', li[1], re.S)
        if artist and album:
            value = re.search(r'value="(\d+)"', li[0])
            entries.append((int(value.group(1)) if ranked and value else None, text_of(artist.group(1)), text_of(album.group(1))))
    return {"outlet": name, "writer": writer, "title": title, "url": url, "ranked": ranked, "entries": entries}


def merged(page_entries, index_entries):
    """A publication's page, checked against the index's copy of the same list. The page's places stand when they
    match the index's; where they don't (a number on the page that wasn't the place) the index's are used, and
    albums the page didn't give (a list split over several pages) come from the index."""
    if not index_entries:
        return page_entries
    places = {feed.key(a, b): r for r, a, b in index_entries}
    shared = [(r, places[feed.key(a, b)]) for r, a, b in page_entries if feed.key(a, b) in places]
    if any(r is not None for r, _, _ in index_entries) and sum(1 for ours, theirs in shared if ours == theirs) < 0.7 * len(shared):
        page_entries = [(places.get(feed.key(a, b)), a, b) for _, a, b in page_entries]
    ours = {feed.key(a, b) for _, a, b in page_entries}
    combined = page_entries + [e for e in index_entries if feed.key(e[1], e[2]) not in ours]
    return sorted(combined, key=lambda e: (e[0] is None, e[0] or 0)) if any(r is not None for r, _, _ in combined) else combined


def agrees(page_entries, index_entries):
    """Whether what a publication's page gave is the same list the index has: enough of the same albums."""
    if not index_entries:
        return len(page_entries) >= 10
    theirs = {feed.key(a, b) for _, a, b in index_entries}
    ours = {feed.key(a, b) for _, a, b in page_entries}
    return len(theirs & ours) >= AGREEMENT * len(theirs)


# ---------------------------------------------------------------------------------------------
# Finding the year's lists
# ---------------------------------------------------------------------------------------------


def year_of_midyear_list(title):
    """The year a mid-year list ("… of 2026 so far") is for, from its headline, or None when it isn't one."""
    if not SO_FAR.search(title) or NOT_SO_FAR.search(title) or not re.search(r"(?i)\balbums?\b", title):
        return None
    m = re.search(r"\b(20\d\d)\b", title)
    return int(m.group(1)) if m else None


def year_of_list(title):
    """The year a year-end list is for, from its headline, or None when it isn't one."""
    if NOT_YEAR_END.search(title):
        return None
    m = YEAR_END.search(title)
    return int(next(g for g in m.groups() if g)) if m else None


def publication(source_name):
    """The publication behind one of the feed job's sources: "NPR New Music Friday (critics’ picks)" is NPR."""
    name, previous = source_name, None
    while name != previous:
        previous = name
        name = re.sub(r"\s*(?:\([^)]*\)|New Music Friday|Best New Music|Best New Track|Album of the (?:Week|Month)|"
                      r"Picks|Tracks|Reviews?|Classical|Jazz|Folk)\s*$", "", name).strip()
    return name or source_name


def lists_in_feeds(years):
    """Year-end and mid-year lists posted in the feeds the Unearth job reads: [{url, outlet, title, year, soFar}]."""
    found = []
    sources = [s for s in feed.SOURCES + feed.SONG_SOURCES if s.get("kind") != "listeners"]
    addresses = {}
    for source in sources:
        addresses.setdefault(source["feed"], source)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        feeds = dict(zip(addresses, pool.map(feed.read_feed, addresses)))
    for address, source in addresses.items():
        items = feeds[address]
        if isinstance(items, Exception):
            continue
        for item in items:
            year, so_far = year_of_list(item["title"]), False
            if year is None:
                year, so_far = year_of_midyear_list(item["title"]), True
            if year in years and item["link"]:
                outlet = publication(source["name"])
                found.append({"url": item["link"], "outlet": outlet, "title": item["title"], "year": year, "soFar": so_far})
    return found


def outlet_key(name):
    """'The Guardian' and 'Guardian', 'Clash Magazine' and 'Clash' are one publication."""
    name = re.sub(r"(?i)^the\s+|\s+(?:magazine|mag|music|online)$", "", name.strip())
    return feed.slugify(name)


def one_per_outlet(lists):
    """A publication's staff list when it has one, otherwise the longest one of its writers made."""
    chosen = {}
    for item in lists:
        k = outlet_key(item["outlet"])
        held = chosen.get(k)
        better = (held is None
                  or (held.get("writer") and not item.get("writer"))
                  or (bool(held.get("writer")) == bool(item.get("writer")) and len(item["entries"]) > len(held["entries"])))
        if better:
            chosen[k] = item
    return list(chosen.values())


# ---------------------------------------------------------------------------------------------
# Adding up
# ---------------------------------------------------------------------------------------------


def aggregate(lists):
    """Albums by points, as Album of the Year's aggregate ranks them: points, then how many lists, then the best place."""
    albums = {}
    for item in lists:
        size = len(item["entries"])
        for rank, artist, album in item["entries"]:
            k = feed.key(artist, album)
            entry = albums.setdefault(k, {"key": k, "artist": artist, "title": album, "points": 0, "lists": 0,
                                          "top10": 0, "firsts": 0, "placements": []})
            if any(p["list"] == item["id"] for p in entry["placements"]):
                continue
            entry["points"] += points(rank, size)
            entry["lists"] += 1
            entry["top10"] += 1 if rank is not None and rank <= 10 else 0
            entry["firsts"] += 1 if rank == 1 else 0
            entry["placements"].append({"list": item["id"], **({"rank": rank} if rank is not None else {})})
    def order(e):
        best = min([p["rank"] for p in e["placements"] if "rank" in p] or [999])
        return (-e["points"], -e["lists"], best, e["artist"].lower())
    ranked = sorted(albums.values(), key=order)
    for e in ranked:
        e["placements"].sort(key=lambda p: (p.get("rank", 999), p["list"]))
    return ranked


# ---------------------------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------------------------


def midyear_lists(year):
    """The mid-year lists kept by hand in midyear_lists.json for one year: [{outlet, url}]."""
    try:
        return json.load(open(MIDYEAR)).get(str(year), [])
    except (OSError, ValueError):
        return []


def gather(year, now, memory, budget, discovered, so_far=False):
    """Every list for one year, each read once and kept in memory: [{id, outlet, title, url, ranked, entries, from}].
    `so_far`: the year's mid-year lists instead, from midyear_lists.json and the feeds (the index doesn't keep them)."""
    pages = memory.setdefault("pages", {})
    index = memory.setdefault("index", {})

    def can_read():
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True

    lists = []
    for found in ([] if so_far else index_years_lists(year)):
        path = found["index"]
        if path not in index and can_read():
            try:
                index[path] = read_index_list(path) or {"skip": True}
            except Exception as error:
                log(f"  couldn't read {path}: {error}")
                continue
        item = index.get(path)
        if not item or item.get("skip") or not item.get("url"):
            continue
        lists.append({**item, "index": path})
    if so_far:
        lists += [{"outlet": m["outlet"], "title": m.get("title", f"Best albums of {year} so far"), "url": m["url"],
                   "ranked": None, "entries": [], "writer": None} for m in midyear_lists(year)]
    known = {l["url"] for l in lists}
    lists += [{"outlet": d["outlet"], "title": d["title"], "url": d["url"], "ranked": None, "entries": [], "writer": None}
              for d in discovered if d["year"] == year and d.get("soFar", False) == so_far and d["url"] not in known]

    result = []
    for item in lists:
        saved = pages.get(item["url"])
        tried = feed.parse_date(saved.get("at")) if saved else None
        if (saved is None or saved.get("version") != VERSION
                or (not saved.get("entries") and tried and (now - tried).days >= RETRY_DAYS)):
            if can_read():
                entries, how = read_original(item["url"], now)
                if saved and len(saved.get("entries") or []) > len(entries or []):
                    # A page read well before, now behind a firewall: what it said then still stands.
                    entries, how = saved["entries"], saved.get("how") or "page"
                saved = pages[item["url"]] = {"entries": entries or [], "how": how, "at": stamp(now), "version": VERSION}
        page_entries = [tuple(e) for e in (saved or {}).get("entries", [])]
        index_entries = [tuple(e) for e in item.get("entries", [])]
        if len(page_entries) >= MIN_ENTRIES and agrees(page_entries, index_entries):
            entries, source = merged(page_entries, index_entries), "page"
        elif len(index_entries) >= MIN_ENTRIES:
            entries, source = index_entries, "index"
        else:
            log(f"  {year} {item['outlet']}: nothing readable ({(saved or {}).get('how') or 'not read yet'})")
            continue
        result.append({"id": feed.slugify(f"{item['outlet']} {item.get('writer') or ''} {year}"), "outlet": item["outlet"],
                       "writer": item.get("writer"), "title": item["title"], "url": item["url"],
                       "ranked": any(r is not None for r, _, _ in entries), "entries": entries, "from": source})
        log(f"  {year} {item['outlet']}{' (' + item['writer'] + ')' if item.get('writer') else ''}: {len(entries)} albums from the {source}")
    return one_per_outlet(result)


def main():
    now = dt.datetime.now(dt.timezone.utc)
    memory = {}
    if os.path.exists(MEMORY):
        try:
            memory = json.load(open(MEMORY))
        except ValueError:
            pass
    previous = {}
    if os.path.exists(OUTPUT):
        try:
            previous = json.load(open(OUTPUT))
        except ValueError:
            pass
    apple_cache = memory.setdefault("appleCache", {})
    # Last year's lists until the end of February (a few come late), and this year's from November. Until this
    # year's year-end lists are out, its mid-year ones make a "so far" page.
    years = sorted({now.year - 1} | ({now.year} if now.month >= 11 else set()))
    budget, lookups = [MAX_PAGES], [MAX_LOOKUPS]
    log("Looking for year-end and mid-year lists in the feeds…")
    discovered = lists_in_feeds(set(years) | {now.year})

    def build(year, so_far):
        log(f"Reading the {'mid-year ' if so_far else ''}lists of {year}…")
        lists = gather(year, now, memory, budget, discovered, so_far)
        ranked = aggregate(lists)[:MAX_ALBUMS]
        albums = []
        for e in ranked:
            apple = None
            cached = apple_cache.get(e["key"])
            if cached is not None or lookups[0] > 0:
                if cached is None:
                    lookups[0] -= 1
                started = time.time()
                apple = feed.apple_lookup(e["artist"], e["title"], apple_cache, "album")
                if cached is None:
                    log(f"  looked up {e['artist']} – {e['title']}: {'found' if apple else 'not found'} ({time.time() - started:.0f}s)")
            albums.append({
                "id": f"{year}:{e['key']}", "title": apple["album"] if apple else e["title"],
                "artist": apple["artist"] if apple else e["artist"],
                "year": apple["year"] if apple else str(year),
                "appleMusicID": apple["id"] if apple else None, "artworkURL": apple["artwork"] if apple else None,
                "genres": [g for g in [feed.genre_of(apple.get("genre")) if apple else None] if g],
                "points": e["points"], "lists": e["lists"], "top10": e["top10"], "firsts": e["firsts"],
                "placements": e["placements"],
            })
        print(f"{year}{' so far' if so_far else ''}: {len(lists)} lists, {len(albums)} albums "
              f"({sum(1 for a in albums if a['appleMusicID'])} found on Apple Music)")
        for a in albums[:10]:
            print(f"  {a['points']:4} pts  {a['lists']:3} lists  {a['artist']} – {a['title']}")
        return {
            "year": year, **({"soFar": True} if so_far else {}),
            "lists": [{"id": l["id"], "outlet": l["outlet"], **({"writer": l["writer"]} if l.get("writer") else {}),
                       "title": l["title"], "url": l["url"], "ranked": l["ranked"], "size": len(l["entries"])}
                      for l in sorted(lists, key=lambda l: l["outlet"].lower())],
            "albums": albums,
        }

    built = {y["year"]: y for y in previous.get("years", [])}
    for year in years:
        made = build(year, so_far=False)
        if year < now.year or len(made["lists"]) >= YEAR_END_FROM:
            built[year] = made
    if now.year not in built or built[now.year].get("soFar"):
        made = build(now.year, so_far=True)
        if len(made["lists"]) >= SO_FAR_FROM:
            built[now.year] = made

    out = {
        "version": 1,
        "generatedAt": stamp(now),
        "points": {"ranked": {"1": 10, "2": 8, "3": 6, "top10": 5, "top25": 3, "other": 1},
                   "unranked": {"upTo10": 5, "upTo25": 3, "longer": 1}},
        "years": [built[y] for y in sorted(built, reverse=True)],
    }
    with open(OUTPUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    with open(MEMORY, "w") as f:
        json.dump(memory, f, indent=1, ensure_ascii=False)
    if not any(y["albums"] for y in out["years"]):
        sys.exit("No lists could be read.")


if __name__ == "__main__":
    main()
