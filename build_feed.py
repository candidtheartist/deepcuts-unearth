#!/usr/bin/env python3
"""Builds feed.json for Deep Cuts' Unearth page.

Reads music blogs' RSS feeds, radio stations' playlists and ListenBrainz's open listening stats, works out which
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
# Radio: how many days of playlists count, how often the stations are asked (the job runs hourly, and
# a week of plays barely moves in an hour), and what it takes to count as "getting played":
# a few spins across more than one broadcast, or more than one station.
RADIO_DAYS = 7
RADIO_REFRESH_HOURS = 6
RADIO_MIN_SPINS = 3
RADIO_MIN_BROADCASTS = 2
# How many of NTS's latest shows are read (it has dozens a day), and how many songs a refresh may look up
# on Apple to find the album and year NTS doesn't give.
NTS_SHOWS = 150
RADIO_LOOKUPS = 400
# Bump when the rules for what counts change, so the stations are read again instead of reusing saved trends.
RADIO_RULES = 2
# Albums and songs only radio is behind: enough to notice, not enough to take the list over.
MAX_RADIO_ONLY = {"album": 30, "song": 20}
# The same for a station whose single plays count (NTS). Kept apart, because one play there
# never outscores a song the other stations have on repeat.
MAX_PICKS_ONLY = {"album": 15, "song": 15}

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
    """Same idea as the app's matchKey: ignore case, accents, punctuation, '(Deluxe)' and a leading 'The'.
    Letters of any alphabet are kept, and so is a bracket the title opens with: "(Don't Fear) The Reaper"."""
    def clean(s):
        s = unicodedata.normalize("NFKD", s).lower().strip()
        s = re.sub(r"(?<!^)[\(\[].*$", "", s)
        s = re.sub(r"^the\s+", "", s)
        return re.sub(r"[\W_]", "", s)
    return clean(album) + "|" + clean(artist)


ARTIST_JOINS = r",|&|\bfeat(?:uring)?\b\.?|\bft\b\.?|\bvs\b\.?|\bx\b|\band\b"


def artist_names(artist):
    """The separate names in "A, B & C feat. D", as written, first-named first."""
    return [name.strip() for name in re.split(ARTIST_JOINS, artist, flags=re.I) if key(name, "") != "|"]


# What tracklists put where a name isn't known.
NOBODY = {"", "unknown", "unknownartist", "various", "variousartists", "id", "tba"}


def nameless(artist, song):
    """A tracklist row with no real artist or title ("Unknown Artist", "(?)", "ID"): nothing to look up or count."""
    title, name = key(artist, song).split("|")
    return name in NOBODY or title in {"", "id", "unknown"}


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
# Radio: what DJs at listener-supported stations are playing
# ---------------------------------------------------------------------------------------------
#
# Each reader returns one row per song played: {artist, song, album, year, broadcast, at}.
# `broadcast` names one show on one day, so "played on four different shows" can be told
# from "one DJ played it four times". `year` is the release year when the station gives it.


def bare(title):
    """triple j adds notes in square brackets: "Green Honda [triple j live recording, …]"."""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", title or "").strip()


def kexp_plays(now):
    cutoff, plays = now - dt.timedelta(days=RADIO_DAYS), []
    url = "https://api.kexp.org/v2/plays/?limit=200"
    for _ in range(16):  # about a week at 200 a page
        page = json.loads(fetch(url, timeout=60))
        for row in page.get("results", []):
            at = parse_date(row.get("airdate"))
            if row.get("play_type") != "trackplay" or not at or not row.get("artist") or not row.get("song"):
                continue
            if at < cutoff:
                return plays
            plays.append({"artist": row["artist"], "song": row["song"], "album": row.get("album") or "",
                          "year": (row.get("release_date") or "")[:4] or None,
                          "broadcast": str(row.get("show")), "at": at})
        url = page.get("next")
        if not url:
            break
    return plays


def kcrw_plays(now):
    """Eclectic 24 (the music channel) and the DJs' live shows, a day at a time."""
    plays, failures = [], []
    today = now - dt.timedelta(hours=8)  # the station's day is Los Angeles's, and asking for tomorrow is an error
    for back in range(RADIO_DAYS + 1):
        day = today - dt.timedelta(days=back)
        for channel in ("Music", "Simulcast"):
            try:
                rows = json.loads(fetch(f"https://tracklist-api.kcrw.com/{channel}/date/{day:%Y/%m/%d}?page_size=500", timeout=60))
            except Exception as error:  # one missing day shouldn't lose the week
                failures.append(error)
                continue
            for row in rows:
                at = parse_date(row.get("datetime"))
                if not at or not row.get("title") or not row.get("artist") or row["artist"].startswith("["):  # "[BREAK]"
                    continue
                plays.append({"artist": row["artist"], "song": row["title"], "album": row.get("album") or "",
                              "year": row.get("year") or None,
                              "broadcast": f"{row.get('program_title')} {row.get('date')}", "at": at})
    if not plays and failures:
        raise failures[0]
    return plays


def triple_j_plays(now):
    start, plays = now - dt.timedelta(days=RADIO_DAYS), []
    for offset in range(0, 3000, 100):  # 100 is the most it gives at once
        url = ("https://music.abcradio.net.au/api/v1/plays/search.json?station=triplej&limit=100"
               f"&offset={offset}&from={start:%Y-%m-%dT%H:%M:%SZ}&to={now:%Y-%m-%dT%H:%M:%SZ}")
        page = json.loads(fetch(url, timeout=60))
        for row in page.get("items", []):
            recording, at = row.get("recording") or {}, parse_date(row.get("played_time"))
            artists = [a["name"] for a in recording.get("artists") or [] if a.get("name")]
            if not at or not artists or not recording.get("title"):
                continue
            release = row.get("release") or {}
            if re.search(r"like a version", release.get("title") or "", re.I):  # the station's own covers series
                release = {}
            # There's no show in the data, so a morning play and an evening play count as two broadcasts.
            plays.append({"artist": " & ".join(artists) if len(artists) < 3 else artists[0],
                          "song": bare(recording["title"]), "album": bare(release.get("title") or ""),
                          "year": release.get("release_year") or None,
                          "broadcast": f"{at:%Y-%m-%d} {at.hour // 6}", "at": at})
        if offset + 100 >= page.get("total", 0):
            break
    return plays


def nts_plays(now):
    """The tracklists of NTS's picked shows and its latest ones. NTS names the artist and song only:
    the album and the year come from Apple afterwards (`fill_in_from_apple`), which matters here
    because much of what NTS plays is old."""
    base = "https://www.nts.live/api/v2"
    episodes = {}
    for row in json.loads(fetch(f"{base}/collections/nts-picks")).get("results", []):
        if row.get("show_alias") and row.get("episode_alias"):
            episodes[f"/shows/{row['show_alias']}/episodes/{row['episode_alias']}"] = parse_date(row.get("broadcast"))
    for offset in range(0, NTS_SHOWS, 12):  # 12 is the most it gives at once
        for row in json.loads(fetch(f"{base}/search/episodes?offset={offset}&limit=12")).get("results", []):
            path = (row.get("article") or {}).get("path")
            if path:
                try:
                    day = dt.datetime.strptime(row.get("local_date") or "", "%d %b %Y").replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    day = None
                episodes.setdefault(path, day)

    def tracklist(path):
        try:
            return json.loads(fetch(f"{base}{path}/tracklist")).get("results", [])
        except Exception:  # some shows have no tracklist
            return []

    plays = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for (path, day), rows in zip(episodes.items(), pool.map(tracklist, episodes)):
            at = day or now
            if (now - at).days > RADIO_DAYS:
                continue
            for row in rows:
                if row.get("artist") and row.get("title") and not nameless(row["artist"], row["title"]):
                    plays.append({"artist": row["artist"], "song": row["title"], "album": "", "year": None,
                                  "broadcast": path, "at": at})
    return plays


RADIO = [
    {"id": "kexp", "name": "KEXP", "kind": "radio", "weight": 2.0,
     "site": "https://www.kexp.org/playlist/", "read": kexp_plays},
    {"id": "kcrw", "name": "KCRW", "kind": "radio", "weight": 2.0,
     "site": "https://www.kcrw.com/playlist", "read": kcrw_plays},
    # triple j plays its favourites far more often than the others, and they're closer to the charts.
    {"id": "triple-j", "name": "triple j", "kind": "radio", "weight": 1.5,
     "site": "https://www.abc.net.au/triplej/featured-music/recently-played", "read": triple_j_plays},
    # NTS's DJs almost never repeat each other, so two shows playing the same song already says something,
    # and one play of something new is a DJ's pick, much as a blog post is: `one_play` is what it weighs.
    {"id": "nts", "name": "NTS Radio", "kind": "radio", "weight": 2.0, "min_spins": 2, "full_at": 4, "one_play": 0.6,
     "site": "https://www.nts.live/latest", "read": nts_plays},
]
RADIO_IDS = {s["id"] for s in RADIO}
STATIONS = {s["id"]: s for s in RADIO}
SOURCE_KINDS = {s["id"]: s["kind"] for s in SOURCES + SONG_SOURCES + RADIO}


def radio_trends(plays_by_station, kind, now, backed=frozenset()):
    """What's getting played: albums (or songs) with a few spins across more than one broadcast,
    or on more than one station. Each comes back with its spins per station.

    At a station whose single plays count (NTS), one play is enough when the record is known to be
    new, or when a blog or curator is behind it too (`backed`, their keys).

    An album counts when DJs are playing more than one track from it: one song on repeat is a
    single doing well, and that belongs in the songs list.
    Anything a station says came out before last year is left out: this is about new music."""
    found = {}
    for station, plays in plays_by_station.items():
        for play in plays:
            title = play["song"] if kind == "song" else play["album"]
            if not title or (kind == "album" and key("", title) == key("", play["song"])):
                continue
            item = found.setdefault(key(play["artist"], title), {"artist": play["artist"], "album": title,
                                                                 "year": None, "stations": {}, "songs": set()})
            item["songs"].add(key("", play["song"]))
            if play.get("year"):
                item["year"] = max(item["year"] or "", str(play["year"]))
            spins = item["stations"].setdefault(station, {"plays": 0, "broadcasts": set(), "last": play["at"]})
            spins["plays"] += 1
            spins["broadcasts"].add(play["broadcast"])
            spins["last"] = max(spins["last"], play["at"])
    trends = []
    for k, item in found.items():
        plays = sum(s["plays"] for s in item["stations"].values())
        broadcasts = sum(len(s["broadcasts"]) for s in item["stations"].values())
        picked = any("one_play" in STATIONS.get(station, {}) for station in item["stations"])
        if kind == "album" and len(item["songs"]) < 2 and not (picked and k in backed):
            continue
        if item["year"] and item["year"] < str(now.year - 1):
            continue
        enough = min(STATIONS.get(station, {}).get("min_spins", RADIO_MIN_SPINS) for station in item["stations"])
        if len(item["stations"]) < 2 and (plays < enough or broadcasts < RADIO_MIN_BROADCASTS):
            if not (picked and (item["year"] or k in backed)):  # a year that got this far is a new one
                continue
        trends.append({"artist": item["artist"], "album": item["album"], "year": item["year"],
                       "stations": {station: {"plays": s["plays"], "last": s["last"].strftime("%Y-%m-%dT%H:%M:%SZ")}
                                    for station, s in item["stations"].items()}})
    trends.sort(key=lambda t: -sum(s["plays"] for s in t["stations"].values()))
    return trends


def fill_in_from_apple(plays_by_station, known, apple_cache, wanted=frozenset()):
    """Gives plays that came without an album (NTS's) the album and year Apple files the song under,
    so they can back an album and old music can be told from new.

    Looking up every song at once would take hours, so each refresh asks about `RADIO_LOOKUPS` of them,
    the promising ones first: artists a blog or curator is writing about (`wanted`, their keys), then
    artists another station is playing, then artists more than one show played, then the rest, newest
    first. Answers are returned (and kept in memory.json) by song, so each is asked once for as long as
    the song stays on the air; a song Apple didn't have is asked about again after two days."""
    elsewhere, shows = set(), {}
    for plays in plays_by_station.values():
        for play in plays:
            artist = key(play["artist"], "")
            if play["album"]:
                elsewhere.add(artist)
            else:
                shows.setdefault(artist, set()).add(play["broadcast"])

    def order(play):
        artist = key(play["artist"], "")
        tier = 0 if artist in wanted else 1 if artist in elsewhere else 2 if len(shows[artist]) > 1 else 3
        return tier, -len(shows[artist]), -play["at"].timestamp()

    resolved, budget = {}, RADIO_LOOKUPS
    bare_plays = [p for plays in plays_by_station.values() for p in plays if not p["album"]]
    for play in sorted(bare_plays, key=order):
        song = key(play["artist"], play["song"])
        if song not in resolved:
            if known.get(song):
                resolved[song] = known[song]
            else:
                if "song:" + song not in apple_cache:  # an answer already saved costs nothing to read again
                    if budget <= 0:
                        continue
                    budget -= 1
                hit = apple_lookup(play["artist"], play["song"], apple_cache, "song", loose=True)
                # "Song - Single" isn't an album; "Name - EP" is, without the label.
                album = re.sub(r" - EP$", "", hit.get("collection") or "") if hit else ""
                resolved[song] = {"album": "" if album.endswith(" - Single") else album, "year": hit["year"]} if hit else None
        if resolved[song]:
            play["album"], play["year"] = resolved[song]["album"], resolved[song]["year"] or None
    return resolved


def read_radio(now, memory, status, apple_cache):
    """This week's radio trends, {"album": […], "song": […]}. The stations are asked every few hours;
    in between, the last answer (kept in memory.json) is used again."""
    saved = memory.get("radio") or {}
    asked = parse_date(saved.get("at"))
    if asked and saved.get("rules") == RADIO_RULES and (now - asked).total_seconds() < RADIO_REFRESH_HOURS * 3600:
        status.update(saved.get("status", {}))
        return saved

    def read(station):
        try:
            return station["read"](now)
        except Exception as error:  # one station being down shouldn't stop the others
            return error

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(RADIO)) as pool:
        answers = list(pool.map(read, RADIO))
    plays, radio_status = {}, {}
    for station, answer in zip(RADIO, answers):
        if isinstance(answer, Exception):
            radio_status[station["id"]] = f"failed: {answer}"
        else:
            plays[station["id"]] = answer
            radio_status[station["id"]] = f"{len(answer)} found"
        log(f"  {station['id']}: {radio_status[station['id']]}")
    # What the blogs and curators had on the last run: their artists are looked up first, and one play of
    # a record they're behind is enough to count.
    written = {kind: [m for m in memory.get(kind, []) if m.get("artist")] for kind in ("album", "song")}
    wanted = {key(m["artist"], "") for mentions in written.values() for m in mentions}
    backed = {kind: {key(m["artist"], m["album"]) for m in mentions} for kind, mentions in written.items()}
    resolved = fill_in_from_apple(plays, saved.get("resolved", {}), apple_cache, wanted)
    log(f"  {sum(1 for r in resolved.values() if r)} of {len(resolved)} songs without an album found on Apple")
    fresh = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rules": RADIO_RULES, "status": radio_status, "resolved": resolved,
             "album": radio_trends(plays, "album", now, backed["album"]),
             "song": radio_trends(plays, "song", now, backed["song"])}
    # A station that failed keeps what it had last time, so one bad afternoon doesn't empty its picks.
    failed = {station for station, result in radio_status.items() if result.startswith("failed")}
    for kind in ("album", "song"):
        known = {key(t["artist"], t["album"]): t for t in fresh[kind]}
        for old in saved.get(kind, []):
            kept = {station: spins for station, spins in old["stations"].items() if station in failed}
            if kept:
                known.setdefault(key(old["artist"], old["album"]), {**old, "stations": {}})["stations"].update(kept)
        fresh[kind] = list(known.values())
    memory["radio"] = fresh
    status.update(radio_status)
    return fresh


def radio_mentions(trends):
    """One mention per station that's playing it. A handful of spins counts for about half
    a station's weight, ten or more for all of it (fewer at a station that rarely repeats itself).
    A single play counts only where the station says what it's worth (`one_play`)."""
    weights = STATIONS
    mentions = []
    for trend in trends:
        for station, spins in trend["stations"].items():
            if station not in weights:
                continue
            weight = weights[station]["weight"] * min(1.0, math.sqrt(spins["plays"] / weights[station].get("full_at", 10)))
            if spins["plays"] == 1:
                weight = weights[station].get("one_play", weight)
            mentions.append({"source": station, "artist": trend["artist"], "album": trend["album"],
                             "url": weights[station]["site"], "date": parse_date(spins["last"]), "image": None,
                             "weight": round(weight, 3),
                             "plays": spins["plays"], "year": trend.get("year")})
    return mentions


def only_radio(entry):
    return bool(entry["mentions"]) and all(m["source"] in RADIO_IDS for m in entry["mentions"]) and not entry.get("listeners")


def only_picks(entry):
    """Radio-only, and every station behind it is one whose single plays count (NTS)."""
    return only_radio(entry) and all("one_play" in STATIONS[m["source"]] for m in entry["mentions"])


# ---------------------------------------------------------------------------------------------
# Matching to Apple Music (best effort: the app retries anything left unmatched)
# ---------------------------------------------------------------------------------------------


# Lookups asked for on this run. Anything else in the saved cache is dropped, so feed.json doesn't grow forever.
USED_LOOKUPS = set()


def apple_lookup(artist, title, cache, kind="album", loose=False):
    """Finds the album or song in Apple's public search. Returns None when there's no confident match.

    `loose` is for tracklists, which credit artists their own way ("A, B" for Apple's "B & A", or one
    name of the two): the title still has to match, but one shared artist name is enough, and when the
    full credit finds nothing the first-named artist is tried alone."""
    k = key(artist, title)
    cache_key = k if kind == "album" else "song:" + k
    USED_LOOKUPS.add(cache_key)
    if cache_key in cache:
        hit = cache[cache_key]
        if "missAt" not in hit and "genre" in hit:  # hits saved before genres were kept are looked up again
            return hit
        # A miss is trusted for two days, then tried again (new releases show up late).
        # A loose search doesn't trust a miss from a strict one.
        if ("missAt" in hit and (not loose or hit.get("loose"))
                and (dt.datetime.now(dt.timezone.utc) - parse_date(hit["missAt"])).days < 2):
            return None
    name_field, id_field = ("collectionName", "collectionId") if kind == "album" else ("trackName", "trackId")
    names = artist_names(artist) if loose else []
    credits = [artist] + ([names[0]] if len(names) > 1 else [])
    result = None
    try:
        for credit in credits:
            term = urllib.parse.quote_plus(f"{credit} {title}".strip())
            hits = json.loads(fetch(f"https://itunes.apple.com/search?media=music&entity={kind}&limit=5&term={term}")).get("results", [])
            time.sleep(3.2)  # Apple's search allows roughly 20 requests a minute
            found = None
            for hit in hits:
                if artist:
                    if key(hit["artistName"], hit[name_field]) == k:
                        found = hit
                        break
                    shared = {key(n, "") for n in names} & {key(n, "") for n in artist_names(hit["artistName"])}
                    if not found and shared and key("", hit[name_field]) == key("", title):
                        found = hit  # kept in case no later result matches exactly
                else:  # Stereogum: "Artist Album" with no separator
                    squashed = re.sub(r"[^a-z0-9]", "", slugify(title))
                    if squashed == re.sub(r"[^a-z0-9]", "", slugify(hit["artistName"] + " " + hit[name_field])):
                        found = hit
                        break
            if found:
                result = {"id": str(found[id_field]), "artist": found["artistName"], "album": found[name_field],
                          "artwork": found.get("artworkUrl100", "").replace("100x100", "600x600"),
                          "year": (found.get("releaseDate") or "")[:4], "genre": found.get("primaryGenreName", ""),
                          **({"collection": found.get("collectionName", "")} if kind == "song" else {})}
                break
    except Exception as error:
        log(f"  apple lookup failed for {artist} / {title}: {error}")
        return None  # don't cache failures
    cache[cache_key] = result or {"missAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  **({"loose": True} if loose else {})}
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


def shortlist(entries, limit, extra_per_genre, radio_only_limit=MAX_RADIO_ONLY["album"], picks_only_limit=MAX_PICKS_ONLY["album"]):
    """The top `limit` by score, plus up to `extra_per_genre` more for each genre a specialist outlet covers.
    Listener-only and radio-only entries are capped so they don't crowd out what people chose to write about."""
    chosen, listener_only, radio_only, picks_only, extras = [], 0, 0, 0, {}
    for entry in sorted(entries, key=lambda e: -e["score"]):
        if not entry["mentions"]:
            listener_only += 1
            if listener_only > MAX_LISTENER_ONLY:
                continue
        elif only_picks(entry):
            # Without a year it can't be shown to be new, and would be dropped later: don't spend a place on it.
            if not any(m.get("year") for m in entry["mentions"]):
                continue
            if picks_only < picks_only_limit:  # on top of the list, as the genres' extras are: one play never makes the cut
                picks_only += 1
                chosen.append(entry)
            continue
        elif only_radio(entry):
            radio_only += 1
            if radio_only > radio_only_limit:
                continue
        if len(chosen) - sum(extras.values()) - picks_only < limit:
            chosen.append(entry)
            continue
        genres = [g for g in entry_genres(None, entry["mentions"]) if extras.get(g, 0) < extra_per_genre]
        if genres:
            extras[genres[0]] = extras.get(genres[0], 0) + 1
            chosen.append(entry)
    return chosen


def kind_of(source_id):
    """What sort of source this is: a blog, a radio station, listeners…"""
    return SOURCE_KINDS.get(source_id, "blog")


def score(mentions, listeners, now):
    """Each mention fades with age and an outlet counts once, however many of its feeds picked it up.
    Agreement is rewarded: a little for each extra outlet, and a lot when a different kind of source
    agrees (a blog reviewed it and DJs are playing it and listeners are rising)."""
    best = {}
    for m in mentions:
        age = max(0.0, (now - m["date"]).total_seconds() / 86400)
        value = m["weight"] * 0.5 ** (age / HALF_LIFE_DAYS)
        best[family(m["source"])] = max(best.get(family(m["source"]), 0), value)
    kinds = {kind_of(m["source"]) for m in mentions}
    if listeners:
        best["listenbrainz"] = min(listeners["momentum"], 3.0) * 0.5
        kinds.add("listeners")
    return round(sum(best.values()) * (1 + 0.2 * (len(best) - 1)) * (1 + 0.4 * (len(kinds) - 1)), 3)


def build_list(kind, sources, now, apple_cache, status, memory, radio=()):
    """Collects, scores, ranks and matches one list: 'album' or 'song'."""
    log(f"Reading {kind} feeds…")
    mentions, feed_status = blog_mentions(now, sources)
    status.update(feed_status)
    mentions = remember(mentions, memory.get(kind, []), sources, now)
    memory[kind] = [{**m, "date": m["date"].strftime("%Y-%m-%dT%H:%M:%SZ"), "weight": None} for m in mentions]
    mentions += radio_mentions(radio)  # not remembered with the blogs' posts: radio is read fresh, a week at a time

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

    for entry in entries.values():
        entry["score"] = score(entry["mentions"], entry["listeners"], now)

    ranked = shortlist(entries.values(), MAX_ALBUMS if kind == "album" else MAX_SONGS, EXTRA_PER_GENRE[kind], MAX_RADIO_ONLY[kind], MAX_PICKS_ONLY[kind])

    def stamp(date):
        return date.strftime("%Y-%m-%dT%H:%M:%SZ")

    log(f"Matching {len(ranked)} {kind}s (+{len(orphans)} without an artist) to Apple Music…")
    output = []
    for entry in ranked:
        apple = apple_lookup(entry["artist"], entry["album"], apple_cache, kind)
        if only_radio(entry):
            # Stations play old favourites too. With nobody writing about it, it has to be known to be new.
            year = max([m.get("year") or "" for m in entry["mentions"]] + [apple["year"] if apple else ""])
            if year < str(now.year - 1):
                continue
        image = next((m["image"] for m in entry["mentions"] if m["image"]), None)
        seen, out_mentions = set(), []
        for m in sorted(entry["mentions"], key=lambda m: -m["weight"]):  # the heaviest feed in a family is the one shown
            if family(m["source"]) in seen:
                continue
            seen.add(family(m["source"]))
            out_mentions.append({"source": m["source"], "url": m["url"], "date": stamp(m["date"]),
                                 **({"plays": m["plays"]} if m.get("plays") else {})})
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

    log("Reading radio playlists…")
    radio = read_radio(now, memory, status, apple_cache)
    albums = build_list("album", SOURCES, now, apple_cache, status, memory, radio.get("album", []))
    songs = build_list("song", SONG_SOURCES, now, apple_cache, status, memory, radio.get("song", []))

    feed = {
        "version": 3,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [{**{k: s[k] for k in ("id", "name", "kind", "site")}, "genres": s.get("genres", [])}
                    for s in SOURCES + SONG_SOURCES + RADIO] + [LISTENERS],
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
        json.dump({kind: [{k: v for k, v in m.items() if k != "weight"} for m in items] if kind != "radio" else items
                   for kind, items in memory.items()}, f, indent=1, ensure_ascii=False)
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
