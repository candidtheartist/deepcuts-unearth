#!/usr/bin/env python3
"""Builds feed.json for Deep Cuts' Unearth page.

Reads music blogs' RSS feeds and ListenBrainz's open listening stats, works out which
albums are getting attention right now, and writes one ranked list. Runs once a day
(see .github/workflows/unearth.yml). Standard library only, so there is nothing to install.

    python3 build_feed.py            # writes feed.json next to this file
    python3 build_feed.py --verbose  # also prints what each source returned
"""

import datetime as dt
import email.utils
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "feed.json")
USER_AGENT = "DeepCutsUnearth/1.0 (album feed reader; +https://github.com/)"
VERBOSE = "--verbose" in sys.argv

# How far back a blog post still counts, and how fast it fades (a post loses half its weight in HALF_LIFE days).
WINDOW_DAYS = 30
HALF_LIFE_DAYS = 10
MAX_ALBUMS = 80
# Albums that only listeners (no blog or curator) are behind: keep the list from filling up with them.
MAX_LISTENER_ONLY = 20

# ---------------------------------------------------------------------------------------------
# Sources. `weight` is how much one mention counts: hand-picked "best of" feeds count the most.
# `parse` turns a feed item into (artist, album), or None to skip it.
# ---------------------------------------------------------------------------------------------


def split_on(separators):
    """'Artist – Album' style titles."""
    def parse(item):
        for sep in separators:
            if sep in item["title"]:
                artist, album = item["title"].split(sep, 1)
                return artist.strip(), album.strip()
        return None
    return parse


def pitchfork(item):
    """Pitchfork titles are just the album. The artist is the start of the link's slug."""
    album = item["title"]
    slug = item["link"].rstrip("/").rsplit("/", 1)[-1]
    album_slug = slugify(album)
    if album_slug and slug.endswith(album_slug) and len(slug) > len(album_slug) + 1:
        artist = slug[: -len(album_slug)].strip("-").replace("-", " ")
        return artist.title(), album
    return None


def bandcamp_daily(item):
    """Album of the Day posts look like: Artist, “Album”. Lists and features don't."""
    m = re.match(r"^(.+?),\s*[“\"](.+?)[”\"]\s*$", item["title"])
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def aquarium_drunkard(item):
    parsed = split_on([" :: "])(item)
    if not parsed or re.search(r"mixtape|sirius|show\b|interview|session", item["title"], re.I):
        return None
    return parsed


def needle_drop(item):
    m = re.match(r"^(.+?)\s+-\s+(.+?)\s+ALBUM REVIEW\s*$", item["title"])
    if not m:
        return None
    artist, album = m.group(1).strip(), m.group(2).strip()
    return artist, (artist if album.lower() == "self-titled" else album)


def beats_per_minute(item):
    title = re.sub(r"^Album Review:\s*", "", item["title"])
    return split_on([" – ", " - "])({"title": title})


def stereogum_aotw(item):
    """'Album Of The Week: Artist Album' has no separator, so it can only be used
    when another source (or Apple's catalog) tells us where the artist ends."""
    text = re.sub(r"^Album Of The Week:\s*", "", item["title"])
    return ("", text) if text != item["title"] else None


def pitchfork_track(item):
    """Track titles come wrapped in quotes; the artist is the start of the link's slug, as for albums."""
    return pitchfork({"title": item["title"].strip("“”\"' "), "link": item["link"]})


def gorilla_vs_bear(item):
    parsed = split_on([" – ", " - "])(item)
    # "A / B" posts are two songs at once: skip them rather than guess.
    return None if not parsed or " / " in parsed[1] else parsed


def stereogum_song(item):
    """Single-song posts look like: Artist – “Title”. News posts and two-song posts don't."""
    m = re.match(r"^(.+?)\s+[–-]\s+[“\"]([^”\"]+)[”\"]\s*$", item["title"])
    return (m.group(1).strip(), m.group(2).strip()) if m else None


SOURCES = [
    {"id": "pitchfork-bnm", "name": "Pitchfork Best New Music", "kind": "blog", "weight": 3.0,
     "site": "https://pitchfork.com/reviews/best/albums/",
     "feed": "https://pitchfork.com/feed/reviews/best/albums/rss", "parse": pitchfork},
    {"id": "pitchfork", "name": "Pitchfork", "kind": "blog", "weight": 1.0,
     "site": "https://pitchfork.com/reviews/albums/",
     "feed": "https://pitchfork.com/feed/feed-album-reviews/rss", "parse": pitchfork},
    {"id": "bandcamp-daily", "name": "Bandcamp Daily", "kind": "blog", "weight": 3.0,
     "site": "https://daily.bandcamp.com/album-of-the-day",
     "feed": "https://daily.bandcamp.com/feed", "parse": bandcamp_daily},
    {"id": "stereogum", "name": "Stereogum Album of the Week", "kind": "blog", "weight": 3.0,
     "site": "https://www.stereogum.com/category/reviews/album-of-the-week/",
     "feed": "https://www.stereogum.com/category/reviews/album-of-the-week/feed/", "parse": stereogum_aotw},
    {"id": "aquarium-drunkard", "name": "Aquarium Drunkard", "kind": "curator", "weight": 2.5,
     "site": "https://aquariumdrunkard.com",
     "feed": "https://aquariumdrunkard.com/feed/", "parse": aquarium_drunkard},
    {"id": "needle-drop", "name": "The Needle Drop", "kind": "curator", "weight": 1.5,
     "site": "https://www.youtube.com/@theneedledrop",
     "feed": "https://www.youtube.com/feeds/videos.xml?channel_id=UCt7fwAhXDy3oNFTAzF2o8Pw", "parse": needle_drop},
    {"id": "beats-per-minute", "name": "Beats Per Minute", "kind": "blog", "weight": 1.5,
     "site": "https://beatsperminute.com/category/reviews/album-reviews/",
     "feed": "https://beatsperminute.com/category/reviews/album-reviews/feed/", "parse": beats_per_minute},
]

SONG_SOURCES = [
    {"id": "pitchfork-bnt", "name": "Pitchfork Best New Track", "kind": "blog", "weight": 3.0,
     "site": "https://pitchfork.com/reviews/best/tracks/",
     "feed": "https://pitchfork.com/feed/reviews/best/tracks/rss", "parse": pitchfork_track},
    {"id": "pitchfork-tracks", "name": "Pitchfork Tracks", "kind": "blog", "weight": 1.5,
     "site": "https://pitchfork.com/reviews/tracks/",
     "feed": "https://pitchfork.com/feed/feed-track-reviews/rss", "parse": pitchfork_track},
    {"id": "gorilla-vs-bear", "name": "Gorilla vs. Bear", "kind": "curator", "weight": 2.5,
     "site": "https://www.gorillavsbear.net",
     "feed": "https://www.gorillavsbear.net/feed/", "parse": gorilla_vs_bear},
    {"id": "stereogum-songs", "name": "Stereogum", "kind": "blog", "weight": 1.5,
     "site": "https://www.stereogum.com/category/music/",
     "feed": "https://www.stereogum.com/category/music/feed/", "parse": stereogum_song},
]

LISTENERS = {"id": "listenbrainz", "name": "ListenBrainz listeners", "kind": "listeners",
             "site": "https://listenbrainz.org/statistics/"}

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def log(*args):
    if VERBOSE:
        print(*args, file=sys.stderr)


def fetch(url, timeout=30):
    """Downloads a URL with curl, falling back to Python's own client if curl isn't installed.

    curl is used on purpose: Bandcamp's bot filter serves an HTML check page (not the feed) to
    Python's built-in client from cloud servers, but serves the feed normally to curl.
    Either way the job says who it is in the User-Agent and never pretends to be a browser.
    """
    if shutil.which("curl"):
        result = subprocess.run(
            ["curl", "--silent", "--show-error", "--fail", "--location", "--max-time", str(timeout),
             "--user-agent", USER_AGENT, "--header", "Accept: */*", url],
            capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"curl exit {result.returncode}")
        return result.stdout
    return fetch_with_urllib(url, timeout)


def fetch_with_urllib(url, timeout=30, redirects=5):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        # Older Pythons don't follow 307/308 redirects on their own.
        target = error.headers.get("Location")
        if error.code in (307, 308) and target and redirects > 0:
            return fetch_with_urllib(urllib.parse.urljoin(url, target), timeout, redirects - 1)
        raise


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def key(artist, album):
    """Same idea as the app's matchKey: ignore case, punctuation, '(Deluxe)' and a leading 'The'."""
    def clean(s):
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
        s = re.sub(r"[\(\[].*$", "", s)
        s = re.sub(r"^the\s+", "", s)
        return re.sub(r"[^a-z0-9]", "", s)
    return clean(album) + "|" + clean(artist)


def parse_date(text):
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def feed_items(xml_bytes):
    """Yields {title, link, date, image} for RSS and Atom feeds."""
    root = ET.fromstring(xml_bytes)
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        item = {"title": "", "link": "", "date": None, "image": None}
        for child in element:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "title":
                item["title"] = html.unescape((child.text or "").strip())
            elif name == "link":
                item["link"] = child.attrib.get("href") or (child.text or "").strip() or item["link"]
            elif name in ("pubDate", "published", "updated", "date") and not item["date"]:
                item["date"] = parse_date((child.text or "").strip())
            elif name == "thumbnail" and child.attrib.get("url"):
                item["image"] = child.attrib["url"]
        yield item


# ---------------------------------------------------------------------------------------------
# Collecting mentions
# ---------------------------------------------------------------------------------------------


def blog_mentions(now, sources):
    mentions, status = [], {}
    for source in sources:
        try:
            data = fetch(source["feed"])
            if data.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
                raise RuntimeError("got a web page instead of a feed (the site may be turning this client away)")
            items = list(feed_items(data))
        except Exception as error:  # one broken feed shouldn't stop the others
            status[source["id"]] = f"failed: {error}"
            log(f"  {source['id']}: FAILED {error}")
            continue
        found = 0
        for item in items:
            if not item["date"] or (now - item["date"]).days > WINDOW_DAYS:
                continue
            parsed = source["parse"](item)
            if not parsed:
                continue
            artist, album = parsed
            mentions.append({"source": source["id"], "artist": artist, "album": album, "url": item["link"],
                             "date": item["date"], "image": item["image"], "weight": source["weight"]})
            found += 1
        status[source["id"]] = f"{found} found"
        log(f"  {source['id']}: {found} found in {len(items)} items")
    return mentions, status


def listener_trends(kind):
    """Albums (or songs) whose listening last week is well above their monthly average.
    Skips the very top (already on every chart), small audiences, and anything that isn't
    in the monthly list (without a baseline there's no way to tell it's rising)."""
    entity, name_field = ("release-groups", "release_group_name") if kind == "album" else ("recordings", "track_name")

    def top(range_name, count):
        url = f"https://api.listenbrainz.org/1/stats/sitewide/{entity}?range={range_name}&count={count}"
        rows = json.loads(fetch(url, timeout=60))["payload"][entity.replace("-", "_")]
        merged = {}
        for row in rows:  # the same album can appear more than once, under different editions
            if not row.get("artist_name") or not row.get(name_field):
                continue
            k = key(row["artist_name"], row[name_field])
            merged.setdefault(k, {"artist": row["artist_name"], "album": row[name_field], "listens": 0})
            merged[k]["listens"] += row["listen_count"]
        return merged

    week, month = top("week", 1000), top("month", 1000)
    ranked = sorted(week.items(), key=lambda kv: -kv[1]["listens"])
    trends = []
    for rank, (k, item) in enumerate(ranked):
        monthly = month.get(k, {}).get("listens", 0)
        if rank < 40 or item["listens"] < 300 or not monthly:
            continue
        momentum = item["listens"] / max(monthly / 4.3, 1)
        if momentum >= 1.4:
            trends.append({**item, "momentum": round(min(momentum, 4.3), 2)})
    trends.sort(key=lambda a: -(a["momentum"] * math.log10(a["listens"])))
    return trends[:60]


# ---------------------------------------------------------------------------------------------
# Matching to Apple Music (best effort: the app retries anything left unmatched)
# ---------------------------------------------------------------------------------------------


def apple_lookup(artist, title, cache, kind="album"):
    """Finds the album or song in Apple's public search. Returns None when there's no confident match."""
    k = key(artist, title)
    cache_key = k if kind == "album" else "song:" + k
    if cache_key in cache:
        hit = cache[cache_key]
        if "missAt" not in hit:
            return hit
        # A miss is trusted for two days, then tried again (new releases show up late).
        if (dt.datetime.now(dt.timezone.utc) - parse_date(hit["missAt"])).days < 2:
            return None
    name_field, id_field = ("collectionName", "collectionId") if kind == "album" else ("trackName", "trackId")
    term = urllib.parse.quote_plus(f"{artist} {title}".strip())
    url = f"https://itunes.apple.com/search?media=music&entity={kind}&limit=5&term={term}"
    result = None
    try:
        for hit in json.loads(fetch(url)).get("results", []):
            if artist:
                matches = key(hit["artistName"], hit[name_field]) == k
            else:  # Stereogum: "Artist Album" with no separator
                squashed = re.sub(r"[^a-z0-9]", "", slugify(title))
                matches = squashed == re.sub(r"[^a-z0-9]", "", slugify(hit["artistName"] + " " + hit[name_field]))
            if matches:
                result = {"id": str(hit[id_field]), "artist": hit["artistName"], "album": hit[name_field],
                          "artwork": hit.get("artworkUrl100", "").replace("100x100", "600x600"),
                          "year": (hit.get("releaseDate") or "")[:4]}
                break
    except Exception as error:
        log(f"  apple lookup failed for {artist} / {title}: {error}")
        return None  # don't cache failures
    cache[cache_key] = result or {"missAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    time.sleep(3.2)  # Apple's search allows roughly 20 requests a minute
    return result


# ---------------------------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------------------------


def family(source_id):
    """Pitchfork's 'best' feeds repeat reviews from its plain feeds: they count, and show, once."""
    return "pitchfork" if source_id.startswith("pitchfork") else source_id


def build_list(kind, sources, now, apple_cache, status):
    """Collects, scores, ranks and matches one list: 'album' or 'song'."""
    log(f"Reading {kind} feeds…")
    mentions, feed_status = blog_mentions(now, sources)
    status.update(feed_status)

    log(f"Reading ListenBrainz {kind}s…")
    listener_key = "listenbrainz" if kind == "album" else "listenbrainz-songs"
    try:
        trends = listener_trends(kind)
        status[listener_key] = f"{len(trends)} found"
    except Exception as error:
        trends = []
        status[listener_key] = f"failed: {error}"
    log(f"  {listener_key}: {status[listener_key]}")

    # Group mentions by album/song. Entries with no artist (Stereogum's Album of the Week) join one from
    # another source when "artist + title" reads the same, e.g. "Gilla Band Pugnello".
    entries = {}
    for m in (m for m in mentions if m["artist"]):
        entry = entries.setdefault(key(m["artist"], m["album"]),
                                   {"artist": m["artist"], "album": m["album"], "mentions": [], "listeners": None})
        entry["mentions"].append(m)
    squashed = {re.sub(r"[^a-z0-9]", "", slugify(e["artist"] + " " + e["album"])): k for k, e in entries.items()}
    orphans = []
    for m in (m for m in mentions if not m["artist"]):
        k = squashed.get(re.sub(r"[^a-z0-9]", "", slugify(m["album"])))
        if k:
            entries[k]["mentions"].append(m)
        else:
            orphans.append(m)
    for trend in trends:
        entry = entries.setdefault(key(trend["artist"], trend["album"]),
                                   {"artist": trend["artist"], "album": trend["album"], "mentions": [], "listeners": None})
        entry["listeners"] = {"listens": trend["listens"], "momentum": trend["momentum"]}

    # Score: each mention fades with age; one mention per source family; agreement between sources is rewarded.
    for entry in entries.values():
        best = {}
        for m in entry["mentions"]:
            age = max(0.0, (now - m["date"]).total_seconds() / 86400)
            value = m["weight"] * 0.5 ** (age / HALF_LIFE_DAYS)
            best[family(m["source"])] = max(best.get(family(m["source"]), 0), value)
        score = sum(best.values())
        if entry["listeners"]:
            best["listenbrainz"] = min(entry["listeners"]["momentum"], 3.0) * 0.5
            score += best["listenbrainz"]
        entry["score"] = round(score * (1 + 0.25 * (len(best) - 1)), 3)

    ranked, listener_only = [], 0
    for entry in sorted(entries.values(), key=lambda e: -e["score"]):
        if not entry["mentions"]:
            listener_only += 1
            if listener_only > MAX_LISTENER_ONLY:
                continue
        ranked.append(entry)
    ranked = ranked[:MAX_ALBUMS]

    def stamp(date):
        return date.strftime("%Y-%m-%dT%H:%M:%SZ")

    log(f"Matching {len(ranked)} {kind}s (+{len(orphans)} without an artist) to Apple Music…")
    output = []
    for entry in ranked:
        apple = apple_lookup(entry["artist"], entry["album"], apple_cache, kind)
        image = next((m["image"] for m in entry["mentions"] if m["image"]), None)
        seen, out_mentions = set(), []
        for m in sorted(entry["mentions"], key=lambda m: -m["weight"]):  # the heaviest feed in a family is the one shown
            if family(m["source"]) in seen:
                continue
            seen.add(family(m["source"]))
            out_mentions.append({"source": m["source"], "url": m["url"], "date": stamp(m["date"])})
        output.append({
            "id": ("" if kind == "album" else "song:") + key(entry["artist"], entry["album"]),
            "type": kind,
            "title": apple["album"] if apple else entry["album"],
            "artist": apple["artist"] if apple else entry["artist"],
            "year": apple["year"] if apple else None,
            "appleMusicID": apple["id"] if apple else None,
            "artworkURL": (apple["artwork"] if apple else None) or image,
            "score": entry["score"],
            "mentions": out_mentions,
            "listeners": entry["listeners"],
        })
    for m in orphans:  # only usable if Apple's catalog can split "Artist Album"
        apple = apple_lookup("", m["album"], apple_cache, kind)
        if apple:
            output.append({
                "id": ("" if kind == "album" else "song:") + key(apple["artist"], apple["album"]), "type": kind,
                "title": apple["album"], "artist": apple["artist"],
                "year": apple["year"], "appleMusicID": apple["id"], "artworkURL": apple["artwork"] or m["image"],
                "score": round(m["weight"] * 0.5 ** (max(0, (now - m["date"]).days) / HALF_LIFE_DAYS), 3),
                "mentions": [{"source": m["source"], "url": m["url"], "date": stamp(m["date"])}],
                "listeners": None,
            })
    output.sort(key=lambda e: -e["score"])
    return output


def main():
    now = dt.datetime.now(dt.timezone.utc)
    previous = {}
    if os.path.exists(OUTPUT):
        try:
            previous = json.load(open(OUTPUT))
        except ValueError:
            pass
    apple_cache = dict(previous.get("appleCache", {}))
    status = {}

    albums = build_list("album", SOURCES, now, apple_cache, status)
    songs = build_list("song", SONG_SOURCES, now, apple_cache, status)

    feed = {
        "version": 2,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [{k: s[k] for k in ("id", "name", "kind", "site")} for s in SOURCES + SONG_SOURCES] + [LISTENERS],
        "status": status,
        "albums": albums,
        "songs": songs,
        # Remembered between runs so the same album or song isn't looked up every day.
        "appleCache": apple_cache,
    }
    with open(OUTPUT, "w") as f:
        json.dump(feed, f, indent=1, ensure_ascii=False)
    for name, items in (("albums", albums), ("songs", songs)):
        matched = sum(1 for item in items if item["appleMusicID"])
        print(f"Wrote {len(items)} {name} ({matched} matched to Apple Music)")
    for source, result in status.items():
        print(f"  {source}: {result}")
    if not albums and not songs:
        sys.exit("Nothing found: every source failed.")


if __name__ == "__main__":
    main()
