#!/usr/bin/env python3
"""Builds feed.json for Deep Cuts' Unearth page.

Reads music blogs' RSS feeds and ListenBrainz's open listening stats, works out which
albums and songs are getting attention right now, and writes one ranked list, each entry tagged
with its genres so the app can lean towards what someone plays most. Runs once an hour
(see .github/workflows/unearth.yml). Standard library only, so there is nothing to install.

    python3 build_feed.py            # writes feed.json next to this file
    python3 build_feed.py --verbose  # also prints what each source returned
"""

import concurrent.futures
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
# Mentions seen on earlier runs. Many blogs only keep their last ten posts in the feed, so without this
# a review would drop out of Unearth a few days after it was written.
MEMORY = os.path.join(HERE, "memory.json")
USER_AGENT = "DeepCutsUnearth/1.0 (album feed reader; +https://github.com/)"
VERBOSE = "--verbose" in sys.argv

# How far back a blog post still counts, and how fast it fades (a post loses half its weight in HALF_LIFE days).
WINDOW_DAYS = 30
HALF_LIFE_DAYS = 10
# How many albums and songs make the list, and how many more each genre may add on top of that
# (so there's enough metal, jazz or country for the people who mostly play that).
MAX_ALBUMS = 120
MAX_SONGS = 60
EXTRA_PER_GENRE = {"album": 25, "song": 10}
# Albums that only listeners (no blog or curator) are behind: keep the list from filling up with them.
MAX_LISTENER_ONLY = 20

# ---------------------------------------------------------------------------------------------
# Parsers. Each turns a feed item into (artist, album), or None to skip it.
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


QUOTES = "“”‘’\"' "


def strip_label(title):
    """'Album (Label)' and 'Album (Label, 2026)' → 'Album'."""
    return re.sub(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$", "", title).strip() or title


def titled(pattern, skip=None):
    """A parser made from a regular expression with `artist` and `album` groups.
    Titles matching `skip` (news, interviews, round-ups) are left out."""
    regex = re.compile(pattern, re.I)
    skip_regex = re.compile(skip, re.I) if skip else None

    def parse(item):
        title = item["title"]
        if skip_regex and skip_regex.search(title):
            return None
        m = regex.match(title)
        if not m:
            return None
        artist, album = m.group("artist").strip(QUOTES), strip_label(m.group("album").strip(QUOTES))
        return (artist, album) if artist and album and len(artist) <= 80 else None
    return parse


DASH = r"^(?P<artist>[^:]+?) [–—-] (?P<album>.+)$"
QUOTED = r"[“”‘’\"']"

# ---------------------------------------------------------------------------------------------
# Genres. The app keeps the same table (UnearthGenre.swift) to read the genres in your library,
# so change both together. A keyword counts when it starts a word: "rap" matches "Rap" but not "Trap".
# The order matters: "Alternative Rap" is hip-hop, "Indie Folk" is folk, "Post-Punk" is indie.
# ---------------------------------------------------------------------------------------------

GENRES = [
    ("latin", "Latin", ["latin", "mexicana", "urbano", "reggaeton", "salsa", "brazil", "mpb", "samba", "bossa", "tango", "flamenco", "cumbia"]),
    ("pop", "Pop", ["k-pop", "j-pop", "c-pop", "mandopop", "cantopop"]),
    ("hiphop", "Hip-Hop", ["hip-hop", "hip hop", "rap", "trap", "grime", "drill"]),
    ("jazz", "Jazz", ["jazz", "bop", "bebop", "big band", "swing"]),
    ("rnb", "R&B & Soul", ["r&b", "soul", "funk", "gospel", "disco", "motown"]),
    ("metal", "Metal", ["metal", "doom", "grindcore", "sludge"]),
    ("classical", "Classical", ["classical", "opera", "orchestral", "baroque", "chamber", "choral", "symphon", "minimalism",
                                "avant-garde", "early music", "medieval", "renaissance", "impressionist", "contemporary era", "romantic era"]),
    ("country", "Country", ["country", "americana", "bluegrass", "honky", "outlaw"]),
    ("folk", "Folk & Blues", ["folk", "singer/songwriter", "singer-songwriter", "blues", "celtic"]),
    ("indie", "Alternative & Indie", ["post-punk", "indie", "alternative", "shoegaze", "new wave", "dream pop", "college"]),
    ("punk", "Punk & Emo", ["punk", "hardcore", "emo", "ska"]),
    ("ambient", "Ambient & Experimental", ["ambient", "new age", "experimental", "drone", "noise", "meditation", "environmental", "modern composition"]),
    ("electronic", "Electronic", ["electro", "dance", "house", "techno", "idm", "dubstep", "jungle", "drum", "bass", "trance", "breakbeat", "downtempo", "edm", "club"]),
    ("global", "Global", ["world", "africa", "afro", "reggae", "dancehall", "dub", "caribbean", "arabic", "indian", "bollywood", "asia",
                          "turkish", "international", "highlife", "amapiano", "fado", "korean", "japan", "chinese"]),
    ("rock", "Rock", ["rock", "psychedel", "prog", "grunge", "surf", "jam band", "britpop"]),
    ("pop", "Pop", ["pop", "vocal", "easy listening", "adult contemporary", "teen", "schlager"]),
]


def genre_of(name):
    """Which of our genres one of Apple's genre names ('Hip-Hop/Rap', 'Singer/Songwriter') belongs to, or None."""
    name = (name or "").lower()
    for genre, _, keywords in GENRES:
        for keyword in keywords:
            if re.search(r"(?<![a-z])" + re.escape(keyword), name):
                return genre
    return None


# ---------------------------------------------------------------------------------------------
# Sources. `weight` is how much one mention counts: hand-picked "best of" feeds count the most.
# ---------------------------------------------------------------------------------------------

# `genres` is set for outlets that stick to one kind of music; general ones leave it out and
# the genre comes from Apple's catalog. `family` groups feeds from one outlet, so it counts once.
SOURCES = [
    # General
    {"id": "pitchfork-bnm", "name": "Pitchfork Best New Music", "kind": "blog", "weight": 3.0, "family": "pitchfork",
     "site": "https://pitchfork.com/reviews/best/albums/",
     "feed": "https://pitchfork.com/feed/reviews/best/albums/rss", "parse": pitchfork},
    {"id": "pitchfork", "name": "Pitchfork", "kind": "blog", "weight": 1.0, "family": "pitchfork",
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
    {"id": "guardian", "name": "The Guardian", "kind": "blog", "weight": 1.5, "family": "guardian",
     "site": "https://www.theguardian.com/music+tone/albumreview",
     "feed": "https://www.theguardian.com/music+tone/albumreview/rss",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+?) (?:album )?review\b", skip=r"^various artists")},
    {"id": "treble-aotw", "name": "Treble Album of the Week", "kind": "blog", "weight": 3.0, "family": "treble",
     "site": "https://www.treblezine.com/category/reviews/album-of-the-week/",
     "feed": "https://www.treblezine.com/category/reviews/album-of-the-week/feed/", "parse": titled(DASH)},
    {"id": "treble", "name": "Treble", "kind": "blog", "weight": 1.0, "family": "treble",
     "site": "https://www.treblezine.com",
     "feed": "https://www.treblezine.com/feed/", "parse": titled(r"^(?P<artist>[^:]+?) : (?P<album>.+)$")},
    {"id": "quietus", "name": "The Quietus", "kind": "blog", "weight": 2.0,
     "site": "https://thequietus.com",
     "feed": "https://thequietus.com/feed/",
     "parse": titled(r"^(?P<artist>[^:–]+?) – (?P<album>[^:]+)$", skip=r"interview|playlist|subscriber|festival")},
    {"id": "clash", "name": "Clash", "kind": "blog", "weight": 1.0,
     "site": "https://www.clashmusic.com/reviews/",
     "feed": "https://www.clashmusic.com/reviews/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+)$")},
    {"id": "god-is-in-the-tv", "name": "God Is in the TV", "kind": "blog", "weight": 1.0,
     "site": "https://www.godisinthetvzine.co.uk",
     "feed": "https://www.godisinthetvzine.co.uk/feed/",
     "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+ \([^)]+\))$", skip=r"in conversation|interview|live")},
    {"id": "dusted", "name": "Dusted", "kind": "curator", "weight": 2.0,
     "site": "https://dustedmagazine.tumblr.com",
     "feed": "https://dustedmagazine.tumblr.com/rss",
     "parse": titled(r"^(?P<artist>[^:]+?) — (?P<album>.+ \([^)]+\))$")},
    {"id": "sun-13", "name": "Sun 13", "kind": "curator", "weight": 1.5,
     "site": "https://sun-13.com",
     "feed": "https://sun-13.com/feed/",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>[^:]+)$", skip=r"interview|playlist|premiere|\blive\b|albums of|tracks of")},
    {"id": "musicomh", "name": "musicOMH", "kind": "blog", "weight": 1.5,
     "site": "https://www.musicomh.com/reviews/albums",
     "feed": "https://www.musicomh.com/feed",
     "parse": titled(r"^(?P<artist>[^:/]+?) – (?P<album>[^:]+)$", skip=r"review|\blive\b|festival|interview|prom \d")},
    {"id": "jenesaispop", "name": "Jenesaispop", "kind": "blog", "weight": 1.5,
     "site": "https://jenesaispop.com",  # Spanish-language pop and indie
     "feed": "https://jenesaispop.com/feed/", "parse": titled(r"^(?P<artist>[^/:]+?) / (?P<album>[^/:]+)$")},
    {"id": "muzikalia", "name": "Muzikalia", "kind": "blog", "weight": 1.0,
     "site": "https://muzikalia.com",  # Spanish-language reviews
     "feed": "https://muzikalia.com/feed/", "parse": titled(r"^(?P<artist>[^:(]+?) – (?P<album>.+ \([^)]+\))$", skip=r"\d\d/\d\d/\d\d")},

    # Rock
    {"id": "fire-note", "name": "The Fire Note", "kind": "blog", "weight": 1.5, "genres": ["rock", "indie"],
     "site": "https://thefirenote.com",
     "feed": "https://thefirenote.com/feed/", "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+?) \[Album Review\]$")},
    {"id": "at-the-barrier", "name": "At The Barrier", "kind": "blog", "weight": 1.5, "genres": ["rock", "folk"],
     "site": "https://atthebarrier.com",
     "feed": "https://atthebarrier.com/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+?): (?:Album|EP) Review$")},

    # Indie, folk and DIY
    {"id": "post-trash", "name": "Post-Trash", "kind": "curator", "weight": 2.0, "genres": ["indie"],
     "site": "https://post-trash.com",
     "feed": "https://post-trash.com/news?format=rss",
     "parse": titled(r"^(?P<artist>.+?) - " + QUOTED + r"(?P<album>.+?)" + QUOTED + r"(?: LP| EP)? \| Album Review")},
    {"id": "various-small-flames", "name": "Various Small Flames", "kind": "curator", "weight": 2.0, "genres": ["folk", "indie"],
     "site": "https://varioussmallflames.co.uk",
     "feed": "https://varioussmallflames.co.uk/feed/", "parse": titled(DASH, skip=r"weekly listening|interview|premiere")},
    {"id": "klof", "name": "KLOF Mag", "kind": "blog", "weight": 2.0, "genres": ["folk"],
     "site": "https://klofmag.com",
     "feed": "https://klofmag.com/feed/",
     "parse": titled(DASH, skip=r"KLOF No|announce|premiere|video|interview|\blive\b|tour|shares?\b")},
    {"id": "guardian-folk", "name": "The Guardian Folk Album of the Month", "kind": "blog", "weight": 3.0, "family": "guardian",
     "genres": ["folk"], "site": "https://www.theguardian.com/music/series/folk-album-of-the-month",
     "feed": "https://www.theguardian.com/music/series/folk-album-of-the-month/rss",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+?) (?:album )?review\b", skip=r"^various artists")},

    # Country and Americana
    {"id": "saving-country-music", "name": "Saving Country Music", "kind": "curator", "weight": 2.0, "genres": ["country"],
     "site": "https://savingcountrymusic.com",
     "feed": "https://savingcountrymusic.com/feed/",
     "parse": titled(r"^Album Review – (?P<artist>.+?)[’']s? [“\"](?P<album>.+?)[”\"]")},
    {"id": "americana-highways", "name": "Americana Highways", "kind": "blog", "weight": 1.5, "genres": ["country", "folk"],
     "site": "https://americanahighways.org",
     "feed": "https://americanahighways.org/feed/",
     "parse": titled(r"^REVIEW: (?P<artist>.+?) [“\"](?P<album>.+?)[”\"]")},
    {"id": "americana-uk", "name": "Americana UK", "kind": "blog", "weight": 1.5, "genres": ["country", "folk"],
     "site": "https://americana-uk.com/category/reviews",
     "feed": "https://americana-uk.com/category/reviews/feed",
     "parse": titled(r"^(?P<artist>[^:]+?) [“\"](?P<album>.+?)[”\"]$", skip=r"^live review|classic clips|video|track")},

    # Punk and emo
    {"id": "the-alternative", "name": "The Alternative", "kind": "blog", "weight": 2.0, "genres": ["punk", "indie"],
     "site": "https://www.getalternative.com",
     "feed": "https://www.getalternative.com/feed/",
     "parse": titled(r"^Review: (?P<artist>.+?) – " + QUOTED + r"(?P<album>.+?)" + QUOTED + r"$")},
    {"id": "dying-scene", "name": "Dying Scene", "kind": "blog", "weight": 1.5, "genres": ["punk"],
     "site": "https://dyingscene.com",
     "feed": "https://dyingscene.com/feed/",
     "parse": titled(r"^DS Record Review: (?P<artist>.+?) [–-] [“\"](?P<album>.+?)[”\"]")},
    {"id": "louder-than-war", "name": "Louder Than War", "kind": "blog", "weight": 1.0, "genres": ["punk", "rock"],
     "site": "https://louderthanwar.com",
     "feed": "https://louderthanwar.com/feed/",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+?) [–-] album review")},

    # Metal and heavy
    {"id": "angry-metal-guy", "name": "Angry Metal Guy", "kind": "blog", "weight": 1.5, "genres": ["metal"],
     "site": "https://www.angrymetalguy.com",
     "feed": "https://www.angrymetalguy.com/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+?) Review$")},
    {"id": "last-rites", "name": "Last Rites", "kind": "blog", "weight": 1.5, "genres": ["metal"],
     "site": "https://yourlastrites.com",
     "feed": "https://yourlastrites.com/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+?) Review$")},
    {"id": "heavy-blog", "name": "Heavy Blog Is Heavy", "kind": "blog", "weight": 1.5, "genres": ["metal"],
     "site": "https://www.heavyblogisheavy.com",
     "feed": "https://www.heavyblogisheavy.com/feed/",
     "parse": titled(r"^(?P<artist>[^:/]+?) - (?P<album>[^:]+)$", skip=r"premiere|roundup|reviews|picks|playlist")},
    {"id": "sleeping-shaman", "name": "The Sleeping Shaman", "kind": "blog", "weight": 1.5, "genres": ["metal"],
     "site": "https://www.thesleepingshaman.com",
     "feed": "https://www.thesleepingshaman.com/feed/",
     "parse": titled(r"^Review: (?P<artist>.+?) [‘'](?P<album>.+)[’']$")},
    {"id": "echoes-and-dust", "name": "Echoes and Dust", "kind": "blog", "weight": 1.0, "genres": ["metal", "rock"],
     "site": "https://echoesanddust.com",
     "feed": "https://echoesanddust.com/feed/", "parse": titled(DASH, skip=r" at |\blive\b|interview|premiere")},
    {"id": "everything-is-noise", "name": "Everything Is Noise", "kind": "blog", "weight": 1.5,
     "site": "https://everythingisnoise.net",
     "feed": "https://everythingisnoise.net/feed/",
     "parse": titled(r"^(?P<artist>[^:]+?) – " + QUOTED + r"(?P<album>.+?)" + QUOTED + r"$")},

    # Hip-hop and R&B
    {"id": "rapreviews", "name": "RapReviews", "kind": "blog", "weight": 1.5, "genres": ["hiphop"],
     "site": "https://www.rapreviews.com",
     "feed": "https://www.rapreviews.com/feed/", "parse": titled(r"^(?P<artist>.+?) :: (?P<album>.+)$")},
    {"id": "shatter-the-standards", "name": "Shatter the Standards", "kind": "curator", "weight": 2.0, "genres": ["hiphop", "rnb"],
     "site": "https://www.shatterthestandards.com",
     "feed": "https://www.shatterthestandards.com/feed",
     "parse": titled(r"^Album Review: (?P<album>.+) by (?P<artist>.+)$")},
    {"id": "grown-up-rap", "name": "Grown Up Rap", "kind": "curator", "weight": 2.0, "genres": ["hiphop"], "family": "grown-up-rap",
     "site": "https://grownuprap.com",
     "feed": "https://grownuprap.com/feed/",
     "parse": titled(r"^(?P<artist>.+?) – [‘'](?P<album>.+?)[’']$")},  # singles say "(video)" or "feat." after the title
    {"id": "monkeyboxing", "name": "Monkeyboxing", "kind": "curator", "weight": 1.5, "genres": ["rnb", "hiphop"],
     "site": "https://www.monkeyboxing.com",  # funk, soul, breaks and hip-hop
     "feed": "https://www.monkeyboxing.com/feed/", "parse": titled(r"^(?P<artist>[^:]+): +(?P<album>.+?) (?:LP|EP)$")},

    # Jazz
    {"id": "guardian-jazz", "name": "The Guardian Jazz Album of the Month", "kind": "blog", "weight": 3.0, "family": "guardian",
     "genres": ["jazz"], "site": "https://www.theguardian.com/music/series/jazz-album-of-the-month",
     "feed": "https://www.theguardian.com/music/series/jazz-album-of-the-month/rss",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+?) (?:album )?review\b", skip=r"^various artists")},
    {"id": "jazz-trail", "name": "Jazz Trail", "kind": "curator", "weight": 2.0, "genres": ["jazz"],
     "site": "https://jazztrail.net",
     "feed": "https://jazztrail.net/blog?format=rss", "parse": titled(r"^(?P<artist>.+?) - (?P<album>.+)$")},
    {"id": "free-jazz", "name": "The Free Jazz Collective", "kind": "curator", "weight": 2.0, "genres": ["jazz"],
     "site": "https://www.freejazzblog.org",
     "feed": "https://www.freejazzblog.org/feeds/posts/default?max-results=50",
     "parse": titled(r"^(?P<artist>[^:]+?) - (?P<album>.+ \([^)]*\d{4}\))")},
    {"id": "uk-vibe", "name": "UK Vibe", "kind": "blog", "weight": 2.0, "genres": ["jazz"],
     "site": "https://ukvibe.org",
     "feed": "https://ukvibe.org/feed/",  # only records it gave 4 or 5 out of 5
     "parse": titled(r"^(?P<artist>.+?) [‘'](?P<album>.+?)[’'] .*\b[45]/5\s*$")},
    {"id": "uk-jazz-news", "name": "UK Jazz News", "kind": "blog", "weight": 1.5, "genres": ["jazz"],
     "site": "https://ukjazznews.com",
     "feed": "https://ukjazznews.com/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – [‘'](?P<album>.+?)[’']$")},

    # Electronic, ambient and experimental
    {"id": "first-floor", "name": "First Floor", "kind": "curator", "weight": 2.0, "genres": ["electronic"],
     "site": "https://firstfloor.substack.com",
     "feed": "https://firstfloor.substack.com/feed", "parse": titled(DASH, skip=r"^First Floor #")},
    {"id": "igloo", "name": "Igloo Magazine", "kind": "blog", "weight": 1.5, "genres": ["electronic"],
     "site": "https://igloomag.com",
     "feed": "https://igloomag.com/feed", "parse": titled(r"^(?P<artist>.+?) :: (?P<album>.+)$", skip=r"^V/A|^various")},
    {"id": "dj-mag", "name": "DJ Mag", "kind": "blog", "weight": 1.5, "genres": ["electronic"],
     "site": "https://djmag.com/reviews",
     "feed": "https://djmag.com/rss.xml",
     "parse": titled(r"^(?P<artist>[^-:]+?) - (?P<album>[^:]+)$", skip=r"^V/A|^various|announce|shares?\b|listen|watch|tour")},
    {"id": "a-closer-listen", "name": "A Closer Listen", "kind": "curator", "weight": 2.0, "genres": ["ambient"],
     "site": "https://acloserlisten.com",
     "feed": "https://acloserlisten.com/feed/", "parse": titled(r"^(?P<artist>.+?) ~ (?P<album>.+)$", skip=r"^V/A|^various")},
    {"id": "headphone-commute", "name": "Headphone Commute", "kind": "curator", "weight": 2.0, "genres": ["ambient"],
     "site": "https://headphonecommute.com",
     "feed": "https://headphonecommute.com/feed/",
     "parse": titled(DASH, skip=r"interview|featured artist|\bmix\b|headphone commute|in the studio")},

    {"id": "hard-wax", "name": "Hard Wax", "kind": "curator", "weight": 1.0, "genres": ["electronic"],
     "site": "https://hardwax.com/this-week/",  # the Berlin shop's new arrivals: techno, jungle, footwork, dubstep, dub techno
     "feed": "https://hardwax.com/feeds/news/",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+)$", skip=r"^various|^V/A"),
     "skip_description": r"reggae|roots|dancehall|rocksteady|lovers rock|\bska\b|calypso|gospel"},  # its reggae racks aren't electronic
    {"id": "ban-ban-ton-ton", "name": "Ban Ban Ton Ton", "kind": "curator", "weight": 2.0, "genres": ["electronic"],
     "site": "https://banbantonton.com",  # Balearic and downtempo
     "feed": "https://banbantonton.com/feed/",
     "parse": titled(r"^(?P<artist>[^/:]+?) / (?P<album>[^/]+?) / .+$", skip=r"^looking for|interview|\bmix\b")},
    {"id": "acid-stag", "name": "Acid Stag", "kind": "blog", "weight": 1.5, "genres": ["electronic"], "family": "acid-stag",
     "site": "https://acidstag.com",
     "feed": "https://acidstag.com/feed/", "parse": titled(r"^(?P<artist>.+?) – (?P<album>.+?) \((?:LP|EP)\)$")},
    {"id": "ambientblog", "name": "Ambientblog", "kind": "curator", "weight": 2.0, "genres": ["ambient"],
     "site": "https://www.ambientblog.net",
     "feed": "https://www.ambientblog.net/blog/feed/",
     "parse": titled(DASH, skip=r"overview|dreamscenes|introducing|liminal state|\bmix\b")},

    # Soul and beats
    {"id": "twisted-soul", "name": "Twisted Soul", "kind": "curator", "weight": 2.0, "genres": ["rnb", "jazz"], "family": "twisted-soul",
     "site": "https://twistedsoulmusic.org",
     "feed": "https://twistedsoulmusic.org/feed", "parse": titled(r"^Album: (?P<artist>.+?) – (?P<album>.+)$")},

    # Classical
    {"id": "guardian-classical", "name": "The Guardian Classical", "kind": "blog", "weight": 2.0, "family": "guardian",
     "genres": ["classical"], "site": "https://www.theguardian.com/music/classical-music-and-opera+tone/albumreview",
     "feed": "https://www.theguardian.com/music/classical-music-and-opera+tone/albumreview/rss",
     "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+?) (?:album )?review\b", skip=r"^various artists")},
    {"id": "i-care-if-you-listen", "name": "I Care If You Listen", "kind": "blog", "weight": 1.5, "genres": ["classical"],
     "site": "https://icareifyoulisten.com",
     "feed": "https://icareifyoulisten.com/feed/",
     "parse": titled(r"^Review: (?P<artist>.+?) [“\"](?P<album>.+?)[”\"]$")},
    {"id": "musicweb", "name": "MusicWeb International", "kind": "blog", "weight": 1.5, "genres": ["classical"],
     "site": "https://musicwebinternational.com",
     "feed": "https://musicwebinternational.com/feed/", "parse": titled(r"^(?P<artist>[^:]+): (?P<album>.+ \([^)]+\))$")},
    {"id": "classic-review", "name": "The Classic Review", "kind": "blog", "weight": 1.5, "genres": ["classical"],
     "site": "https://theclassicreview.com",  # "Review: Composer – Work – Performers": the performers are who Apple lists it under
     "feed": "https://theclassicreview.com/feed/",
     "parse": titled(r"^Review: [^–]+ – (?P<album>.+?) – (?P<artist>[^,–]+)")},

    # Global
    {"id": "beehype", "name": "beehype", "kind": "curator", "weight": 2.0, "genres": ["global"], "family": "beehype",
     "site": "https://beehy.pe",  # the best new music from each country
     "feed": "https://beehy.pe/feed/", "parse": titled(r"^[^:]+: (?P<artist>.+?) – [“\"](?P<album>.+?)[”\"] (?:LP|EP)$")},
    {"id": "the-native", "name": "The NATIVE", "kind": "blog", "weight": 2.0, "genres": ["global"],
     "site": "https://thenativemag.com",
     "feed": "https://thenativemag.com/feed/",
     "parse": titled(r"^Review: [‘'](?P<album>.+)[’'] by (?P<artist>.+)$")},
]

SONG_SOURCES = [
    {"id": "pitchfork-bnt", "name": "Pitchfork Best New Track", "kind": "blog", "weight": 3.0, "family": "pitchfork",
     "site": "https://pitchfork.com/reviews/best/tracks/",
     "feed": "https://pitchfork.com/feed/reviews/best/tracks/rss", "parse": pitchfork_track},
    {"id": "pitchfork-tracks", "name": "Pitchfork Tracks", "kind": "blog", "weight": 1.5, "family": "pitchfork",
     "site": "https://pitchfork.com/reviews/tracks/",
     "feed": "https://pitchfork.com/feed/feed-track-reviews/rss", "parse": pitchfork_track},
    {"id": "gorilla-vs-bear", "name": "Gorilla vs. Bear", "kind": "curator", "weight": 2.5,
     "site": "https://www.gorillavsbear.net",
     "feed": "https://www.gorillavsbear.net/feed/", "parse": gorilla_vs_bear},
    {"id": "stereogum-songs", "name": "Stereogum", "kind": "blog", "weight": 1.5,
     "site": "https://www.stereogum.com/category/music/",
     "feed": "https://www.stereogum.com/category/music/feed/", "parse": stereogum_song},
    {"id": "raven-sings-the-blues", "name": "Raven Sings the Blues", "kind": "curator", "weight": 2.0, "genres": ["rock"],
     "site": "https://www.ravensingstheblues.com",
     "feed": "https://www.ravensingstheblues.com/feed/",
     "parse": titled(r"^(?P<artist>.+?) – [“\"](?P<album>.+?)[”\"]$")},
    {"id": "singles-jukebox", "name": "The Singles Jukebox", "kind": "curator", "weight": 2.0, "genres": ["pop"],
     "site": "https://www.thesinglesjukebox.com",
     "feed": "https://www.thesinglesjukebox.com/?feed=rss2", "parse": titled(r"^(?P<artist>.+?) – (?P<album>.+)$")},
    {"id": "bias-list", "name": "The Bias List", "kind": "curator", "weight": 1.5, "genres": ["pop"],
     "site": "https://thebiaslist.com",
     "feed": "https://thebiaslist.com/feed/",
     "parse": titled(r"^(?:Song Review|Buried Treasure): (?P<artist>.+?) – (?P<album>.+)$")},
    {"id": "chorus-fm", "name": "Chorus.fm", "kind": "blog", "weight": 1.0, "genres": ["punk"],
     "site": "https://chorus.fm",
     "feed": "https://chorus.fm/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – [“\"](?P<album>.+?)[”\"]")},
    {"id": "inverted-audio", "name": "Inverted Audio", "kind": "curator", "weight": 1.5, "genres": ["electronic"],
     "site": "https://inverted-audio.com",
     "feed": "https://inverted-audio.com/feed/", "parse": titled(r"^Premiere: (?P<artist>.+?) – (?P<album>.+)$")},
    {"id": "scandipop", "name": "Scandipop", "kind": "curator", "weight": 1.5, "genres": ["pop"],
     "site": "https://www.scandipop.co.uk",
     "feed": "https://www.scandipop.co.uk/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+)$", skip=r"interview|playlist|album")},
    {"id": "grown-up-rap-songs", "name": "Grown Up Rap", "kind": "curator", "weight": 1.5, "genres": ["hiphop"], "family": "grown-up-rap",
     "site": "https://grownuprap.com",
     "feed": "https://grownuprap.com/feed/",
     "parse": titled(r"^(?P<artist>.+?) – [‘'](?P<album>.+?)[’'] (?:\(video\)|feat\.)")},
    {"id": "beehype-songs", "name": "beehype", "kind": "curator", "weight": 2.0, "genres": ["global"], "family": "beehype",
     "site": "https://beehy.pe",
     "feed": "https://beehy.pe/feed/", "parse": titled(r"^[^:]+: (?P<artist>.+?) – [“\"](?P<album>.+?)[”\"]$")},
    {"id": "rhythm-passport", "name": "Rhythm Passport", "kind": "curator", "weight": 1.5, "genres": ["global"],
     "site": "https://rhythmpassport.com",
     "feed": "https://www.rhythmpassport.com/feed/", "parse": titled(r"^Daily Discovery: (?P<artist>.+?) – (?P<album>.+)$")},
    {"id": "world-a-reggae", "name": "World A Reggae", "kind": "blog", "weight": 1.5, "genres": ["global"],
     "site": "https://www.worldareggae.com",  # titles read "Song – Artist"
     "feed": "https://www.worldareggae.com/feed/",
     "parse": titled(r"^(?P<album>[^:]+?) – (?P<artist>[^(]+?)(?: \(.*\))?$", skip=r"interview|festival|album|riddim")},
    {"id": "ransom-note", "name": "The Ransom Note", "kind": "curator", "weight": 2.0, "genres": ["electronic"],
     "site": "https://www.theransomnote.com",
     "feed": "https://www.theransomnote.com/feed/", "parse": titled(r"^Premiere: (?P<artist>.+?) – (?P<album>.+)$")},
    {"id": "bolting-bits", "name": "Bolting Bits", "kind": "curator", "weight": 1.5, "genres": ["electronic"],
     "site": "https://boltingbits.com",  # house
     "feed": "https://boltingbits.com/feed/",
     "parse": titled(r"^(?P<artist>[^:]+?) ?[–-] ?(?P<album>.+ \[[^\]]+\])$", skip=r"times & tunes|\bmix\b|interview")},
    {"id": "trommel", "name": "Trommel", "kind": "curator", "weight": 1.5, "genres": ["electronic"],
     "site": "https://trommelmusic.com",  # minimal and house
     "feed": "https://trommelmusic.com/feed/",
     "parse": titled(r"^Premiere: (?:[A-D]\d – )?(?P<artist>.+?) – (?P<album>.+ \[[^\]]+\])$")},
    {"id": "trance-attack", "name": "Trance Attack", "kind": "blog", "weight": 1.5, "genres": ["electronic"],
     "site": "https://www.tranceattack.net",
     "feed": "https://www.tranceattack.net/feed/",
     "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+)$", skip=r"\(\d\d\.\d\d\.\d{4}\)|\bmix\b|interview")},  # radio shows carry a date
    {"id": "stereofox", "name": "Stereofox", "kind": "curator", "weight": 1.5, "genres": ["electronic"],
     "site": "https://www.stereofox.com",  # beats and downtempo
     "feed": "https://www.stereofox.com/feed/", "parse": titled(r"^(?P<artist>[^:]+?) – (?P<album>.+)$", skip=r"interview|playlist")},
    {"id": "acid-stag-songs", "name": "Acid Stag", "kind": "blog", "weight": 1.5, "genres": ["electronic"], "family": "acid-stag",
     "site": "https://acidstag.com",
     "feed": "https://acidstag.com/feed/", "parse": titled(r"^(?P<artist>.+?) – [‘'](?P<album>.+?)[’']$")},
    {"id": "twisted-soul-songs", "name": "Twisted Soul", "kind": "curator", "weight": 2.0, "genres": ["rnb", "jazz"], "family": "twisted-soul",
     "site": "https://twistedsoulmusic.org",
     "feed": "https://twistedsoulmusic.org/feed", "parse": titled(r"^(?P<artist>.+?) – (?P<album>.+?) \(TS Premiere\)$")},
]

FAMILIES = {s["id"]: s.get("family", s["id"]) for s in SOURCES + SONG_SOURCES}
SOURCE_GENRES = {s["id"]: s.get("genres", []) for s in SOURCES + SONG_SOURCES}

LISTENERS = {"id": "listenbrainz", "name": "ListenBrainz listeners", "kind": "listeners",
             "site": "https://listenbrainz.org/statistics/"}

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def log(*args):
    if VERBOSE:
        print(*args, file=sys.stderr)


def fetch(url, timeout=30):
    """Downloads a URL, trying once more after a pause if it fails: with this many feeds,
    one of them hiccups on most days."""
    try:
        return fetch_once(url, timeout)
    except Exception:
        time.sleep(3)
        return fetch_once(url, timeout)


def fetch_once(url, timeout=30):
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
        item = {"title": "", "link": "", "date": None, "image": None, "description": ""}
        for child in element:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "title":
                item["title"] = html.unescape((child.text or "").strip())
            elif name == "link":
                item["link"] = child.attrib.get("href") or (child.text or "").strip() or item["link"]
            elif name in ("pubDate", "published", "updated", "date") and not item["date"]:
                item["date"] = parse_date((child.text or "").strip())
            elif name in ("description", "summary") and not item["description"]:
                item["description"] = re.sub(r"<[^>]+>", " ", html.unescape(child.text or ""))[:400]
            elif name == "thumbnail" and child.attrib.get("url"):
                item["image"] = child.attrib["url"]
        yield item


# ---------------------------------------------------------------------------------------------
# Collecting mentions
# ---------------------------------------------------------------------------------------------


def read_feed(source):
    """The items in one source's feed, or the error that stopped it."""
    try:
        data = fetch(source["feed"])
        if data.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
            raise RuntimeError("got a web page instead of a feed (the site may be turning this client away)")
        return list(feed_items(data.lstrip()))  # a few sites put a blank line before the XML
    except Exception as error:  # one broken feed shouldn't stop the others
        return error


def blog_mentions(now, sources):
    mentions, status = [], {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        feeds = list(pool.map(read_feed, sources))
    for source, items in zip(sources, feeds):
        if isinstance(items, Exception):
            status[source["id"]] = f"failed: {items}"
            log(f"  {source['id']}: FAILED {items}")
            continue
        found = 0
        for item in items:
            if not item["date"] or (now - item["date"]).days > WINDOW_DAYS:
                continue
            parsed = source["parse"](item)
            if not parsed or ("skip_description" in source and re.search(source["skip_description"], item["description"], re.I)):
                continue
            artist, album = parsed
            mentions.append({"source": source["id"], "artist": artist, "album": album, "url": item["link"],
                             "date": item["date"], "image": item["image"], "weight": source["weight"]})
            found += 1
        status[source["id"]] = f"{found} found"
        log(f"  {source['id']}: {found} found in {len(items)} items")
    return mentions, status


def remember(fresh, remembered, sources, now):
    """Adds mentions from earlier runs that have scrolled out of their feed but are still inside the window.
    A source that has been removed takes its old mentions with it; weights are today's."""
    weights = {s["id"]: s["weight"] for s in sources}
    seen = {(m["source"], m["url"]) for m in fresh}
    merged = list(fresh)
    for old in remembered:
        date = parse_date(old.get("date"))
        if old.get("source") not in weights or (old["source"], old.get("url")) in seen:
            continue
        if not date or (now - date).days > WINDOW_DAYS:
            continue
        seen.add((old["source"], old.get("url")))
        merged.append({"source": old["source"], "artist": old["artist"], "album": old["album"], "url": old.get("url"),
                       "date": date, "image": old.get("image"), "weight": weights[old["source"]]})
    return merged


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


# Lookups asked for on this run. Anything else in the saved cache is dropped, so feed.json doesn't grow forever.
USED_LOOKUPS = set()


def apple_lookup(artist, title, cache, kind="album"):
    """Finds the album or song in Apple's public search. Returns None when there's no confident match."""
    k = key(artist, title)
    cache_key = k if kind == "album" else "song:" + k
    USED_LOOKUPS.add(cache_key)
    if cache_key in cache:
        hit = cache[cache_key]
        if "missAt" not in hit and "genre" in hit:  # hits saved before genres were kept are looked up again
            return hit
        # A miss is trusted for two days, then tried again (new releases show up late).
        if "missAt" in hit and (dt.datetime.now(dt.timezone.utc) - parse_date(hit["missAt"])).days < 2:
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
                          "year": (hit.get("releaseDate") or "")[:4], "genre": hit.get("primaryGenreName", "")}
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
    """An outlet's 'best of' feed repeats reviews from its plain feed: they count, and show, once."""
    return FAMILIES.get(source_id, source_id)


def entry_genres(apple, mentions):
    """Apple's genre for it. Only when Apple has none (it isn't there, or it's filed under something
    like "Soundtrack") do the genres of the specialist outlets that covered it stand in: an outlet's
    genres are a guess about everything it writes about, and Apple's is about this one record."""
    apple_genre = genre_of(apple.get("genre")) if apple else None
    if apple_genre:
        return [apple_genre]
    genres = [g for m in mentions for g in SOURCE_GENRES.get(m["source"], [])]
    return list(dict.fromkeys(genres))


def shortlist(entries, limit, extra_per_genre):
    """The top `limit` by score, plus up to `extra_per_genre` more for each genre a specialist outlet covers.
    Listener-only entries are capped so they don't crowd out what people chose to write about."""
    chosen, listener_only, extras = [], 0, {}
    for entry in sorted(entries, key=lambda e: -e["score"]):
        if not entry["mentions"]:
            listener_only += 1
            if listener_only > MAX_LISTENER_ONLY:
                continue
        if len(chosen) - sum(extras.values()) < limit:
            chosen.append(entry)
            continue
        genres = [g for g in entry_genres(None, entry["mentions"]) if extras.get(g, 0) < extra_per_genre]
        if genres:
            extras[genres[0]] = extras.get(genres[0], 0) + 1
            chosen.append(entry)
    return chosen


def build_list(kind, sources, now, apple_cache, status, memory):
    """Collects, scores, ranks and matches one list: 'album' or 'song'."""
    log(f"Reading {kind} feeds…")
    mentions, feed_status = blog_mentions(now, sources)
    status.update(feed_status)
    mentions = remember(mentions, memory.get(kind, []), sources, now)
    memory[kind] = [{**m, "date": m["date"].strftime("%Y-%m-%dT%H:%M:%SZ"), "weight": None} for m in mentions]

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

    ranked = shortlist(entries.values(), MAX_ALBUMS if kind == "album" else MAX_SONGS, EXTRA_PER_GENRE[kind])

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
            "genres": entry_genres(apple, entry["mentions"]),
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
                "genres": entry_genres(apple, [m]),
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
    memory = {}
    if os.path.exists(MEMORY):
        try:
            memory = json.load(open(MEMORY))
        except ValueError:
            pass
    status = {}

    albums = build_list("album", SOURCES, now, apple_cache, status, memory)
    songs = build_list("song", SONG_SOURCES, now, apple_cache, status, memory)

    feed = {
        "version": 3,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [{**{k: s[k] for k in ("id", "name", "kind", "site")}, "genres": s.get("genres", [])}
                    for s in SOURCES + SONG_SOURCES] + [LISTENERS],
        "genres": [{"id": g, "name": name} for g, name in dict((g, name) for g, name, _ in GENRES).items()],
        "status": status,
        "albums": albums,
        "songs": songs,
        # Remembered between runs so the same album or song isn't looked up every run.
        "appleCache": {k: v for k, v in apple_cache.items() if k in USED_LOOKUPS},
    }
    with open(OUTPUT, "w") as f:
        json.dump(feed, f, indent=1, ensure_ascii=False)
    with open(MEMORY, "w") as f:
        json.dump({kind: [{k: v for k, v in m.items() if k != "weight"} for m in items] for kind, items in memory.items()},
                  f, indent=1, ensure_ascii=False)
    for name, items in (("albums", albums), ("songs", songs)):
        matched = sum(1 for item in items if item["appleMusicID"])
        print(f"Wrote {len(items)} {name} ({matched} matched to Apple Music)")
    for source, result in status.items():
        print(f"  {source}: {result}")
    for genre, name in dict((g, name) for g, name, _ in GENRES).items():
        print(f"  {name}: {sum(genre in a['genres'] for a in albums)} albums, {sum(genre in a['genres'] for a in songs)} songs")
    if not albums and not songs:
        sys.exit("Nothing found: every source failed.")


if __name__ == "__main__":
    main()
