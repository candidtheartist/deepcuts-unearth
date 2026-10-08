#!/usr/bin/env python3
"""Builds labels.json: what each record label on the list has put out in the last year.

Apple Music can't be asked "what's new on this label": its own list for a label is often years
behind. MusicBrainz can, so once a day this asks it about every label in labels_list.json and
writes the answers to labels.json. The app reads that one file, finds the records on Apple Music
itself (by barcode, or by title and artist) and uses them as a way in to the label's newer artists.

Nothing here knows which labels anyone follows: the list is the same for everybody.

    python3 build_labels.py             # writes labels.json
    python3 build_labels.py --resolve   # finds the MusicBrainz id of any label on the list without one
"""

import datetime
import json
import os
import sys
import time
import urllib.parse

import build_feed
from build_feed import fetch, log

# MusicBrainz asks for a User-Agent that says who is calling and where to find them.
build_feed.USER_AGENT = "DeepCutsUnearth/1.0 (+https://github.com/candidtheartist/deepcuts-unearth)"

HERE = os.path.dirname(os.path.abspath(__file__))
LIST = os.path.join(HERE, "labels_list.json")
OUT = os.path.join(HERE, "labels.json")
API = "https://musicbrainz.org/ws/2/"
# MusicBrainz allows one request a second.
PAUSE = 1.1
# How far back releases are kept, and how far ahead (records announced but not out yet).
DAYS_BACK, DAYS_AHEAD = 365, 240
# A label with more than this many releases in a year is cut off at the newest.
MOST = 400
# Words on the end of a label's name that don't tell two labels apart.
ENDINGS = {"records", "record", "recordings", "recording", "recs", "music", "inc", "llc", "ltd", "co", "company", "group", "entertainment"}
# Where a label's logo is looked up, and how wide the picture the app shows is asked for.
WIKIDATA = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"
LOGO_WIDTH = 330
# What a label "is" on MusicBrainz. A publisher or holding company with the same name isn't the label.
NOT_LABELS = {"Publisher", "Holding", "Rights Society", "Manufacturer", "Bootleg Production"}


def ask(path, **query):
    query["fmt"] = "json"
    answer = json.loads(fetch(API + path + "?" + urllib.parse.urlencode(query)))
    time.sleep(PAUSE)
    return answer


def plain(name):
    """A label's name with case, punctuation and "Records" taken off: "Sub Pop Records" is "sub pop"."""
    words = "".join(c.lower() if c.isalnum() else " " for c in name).split()
    while len(words) > 1 and words[-1] in ENDINGS:
        words.pop()
    return " ".join(words)


def choose(name, candidates, activity=None):
    """The MusicBrainz label that is this one, from a search's answers.

    It has to have the same name ("Records" and the like aside). When several do ("Young", "Black Box"),
    `activity` (how many releases each had lately, by id) settles it: the one that's putting records out,
    if it's well ahead of the rest. A publisher or holding company only counts when nothing else has the name.
    None when it can't be told.
    """
    wanted = plain(name)
    same = [c for c in candidates if plain(c.get("name", "")) == wanted]
    labels = [c for c in same if c.get("type") not in NOT_LABELS] or same
    if len(labels) == 1:
        return labels[0]["id"]
    if len(labels) > 1 and activity is not None:
        ranked = sorted(labels, key=lambda c: activity.get(c["id"], 0), reverse=True)
        first, second = activity.get(ranked[0]["id"], 0), activity.get(ranked[1]["id"], 0)
        if first >= 3 and first >= 3 * second:
            return ranked[0]["id"]
    return None


def same_named(name, candidates):
    """The candidates `choose` would have to pick between."""
    wanted = plain(name)
    same = [c for c in candidates if plain(c.get("name", "")) == wanted]
    return [c for c in same if c.get("type") not in NOT_LABELS] or same


def credit(release):
    """The artist as MusicBrainz credits it: "Haley Heynderickx & Max García Conover"."""
    return "".join(part.get("name", "") + part.get("joinphrase", "") for part in release.get("artist-credit", [])).strip()


def records(releases):
    """One entry per record, newest first. MusicBrainz lists every edition of a record (the LP, the CD,
    the download) as its own release; they're gathered under the record, with all their barcodes."""
    groups = {}
    for release in releases:
        group = (release.get("release-group") or {}).get("id") or release.get("id")
        groups.setdefault(group, []).append(release)
    found = []
    for editions in groups.values():
        dates = sorted(e["date"] for e in editions if e.get("date"))
        first = editions[0]
        found.append({
            "title": (first.get("release-group") or {}).get("title") or first.get("title", ""),
            "artist": credit(first),
            "date": dates[0] if dates else None,
            "upcs": sorted({e["barcode"] for e in editions if e.get("barcode")}),
        })
    found = [r for r in found if r["title"] and r["artist"]]
    found.sort(key=lambda r: (r["date"] or "", r["title"]), reverse=True)
    return found[:MOST]


def releases_of(mbid, today):
    """Everything MusicBrainz has on the label from the last year, and what's announced."""
    return between(mbid, today - datetime.timedelta(days=DAYS_BACK), today + datetime.timedelta(days=DAYS_AHEAD))


# MusicBrainz refuses to page far into a long list of answers, so a stretch with more than this is asked for in two halves.
MOST_PER_ASK = 400


def between(mbid, since, until):
    """The label's releases dated from `since` to `until`, both included."""
    found, offset = [], 0
    while True:
        answer = ask("release", query=f"laid:{mbid} AND date:[{since.isoformat()} TO {until.isoformat()}]", limit=100, offset=offset)
        count = answer.get("count", 0)
        if count > MOST_PER_ASK and until > since:
            middle = since + (until - since) // 2
            return between(mbid, since, middle) + between(mbid, middle + datetime.timedelta(days=1), until)
        found += answer.get("releases", [])
        offset += 100
        if offset >= count or offset >= MOST_PER_ASK:
            return found


def resolve(labels, today):
    """Fills in the MusicBrainz id of each label that has none. Ones that can't be told apart are left for a person."""
    since = (today - datetime.timedelta(days=DAYS_BACK)).isoformat()
    until = (today + datetime.timedelta(days=DAYS_AHEAD)).isoformat()
    unsure = []
    for label in labels:
        if label.get("mbid"):
            continue
        try:
            # Searched without "Records" on the end: MusicBrainz often files "Epitaph Records" as plain "Epitaph".
            candidates = ask("label", query='label:"%s"' % plain(label["name"]), limit=25).get("labels", [])
            mbid = choose(label["name"], candidates)
            rivals = same_named(label["name"], candidates)
            if not mbid and len(rivals) > 1:
                activity = {c["id"]: ask("release", query=f'laid:{c["id"]} AND date:[{since} TO {until}]', limit=1).get("count", 0)
                            for c in rivals[:6]}
                mbid = choose(label["name"], candidates, activity)
        except Exception as error:
            unsure.append(f'{label["name"]}: {error}')
            continue
        if mbid:
            label["mbid"] = mbid
        else:
            names = ", ".join(f'{c.get("name")} [{c.get("type")}] {c.get("id")}' for c in (rivals or candidates)[:4])
            unsure.append(f'{label["name"]}: {names or "nothing found"}')
    return unsure


def wikidata_id(label):
    """The label's Wikidata id ("Q1312934") from MusicBrainz's links for it, or "" when it has none."""
    for relation in label.get("relations", []):
        if relation.get("type") == "wikidata":
            return (relation.get("url") or {}).get("resource", "").rstrip("/").rsplit("/", 1)[-1]
    return ""


def logo_file(entity):
    """The file Wikidata gives as the label's logo ("Warp Records logo.svg"), or None."""
    for claim in (entity.get("claims") or {}).get("P154", []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value:
            return value
    return None


def logo_url(file):
    """Where Wikimedia Commons serves that file as a picture this wide (a PNG, whatever the file is)."""
    return COMMONS + urllib.parse.quote(file.replace(" ", "_")) + f"?width={LOGO_WIDTH}"


def add_logos(out, labels, kept):
    """Gives each label Apple Music has no page for its logo, when Wikidata has one. (A label with a page has
    Apple Music's own picture.) A logo set by hand in labels_list.json wins. The Wikidata id is looked up once
    and kept in labels.json; the logo itself is asked for each day, since they get added and replaced.
    A label that can't be asked about keeps what it had."""
    by_hand = {label.get("mbid"): label["logo"] for label in labels if label.get("logo")}
    wanted = []
    for entry in out:
        before = kept.get(entry["mbid"]) or {}
        if before.get("logo"):
            entry["logo"] = before["logo"]
        if entry["mbid"] in by_hand:
            entry["logo"] = by_hand[entry["mbid"]]
            continue
        if entry.get("appleMusicID"):
            entry.pop("logo", None)
            continue
        if "wikidata" in before:
            entry["wikidata"] = before["wikidata"]
        else:
            try:
                entry["wikidata"] = wikidata_id(ask("label/" + entry["mbid"], inc="url-rels"))
            except Exception as error:
                log(f'  {entry["name"]}: no Wikidata id ({error})')
                continue
        if entry["wikidata"]:
            wanted.append(entry)
    for start in range(0, len(wanted), 50):
        batch = wanted[start:start + 50]
        query = urllib.parse.urlencode({"action": "wbgetentities", "props": "claims", "format": "json",
                                        "ids": "|".join(entry["wikidata"] for entry in batch)})
        try:
            entities = json.loads(fetch(WIKIDATA + "?" + query)).get("entities", {})
        except Exception as error:
            log(f"  Wikidata: {error}")
            continue
        for entry in batch:
            file = logo_file(entities.get(entry["wikidata"]) or {})
            if file:
                entry["logo"] = logo_url(file)
            else:
                entry.pop("logo", None)
    log(f'{sum(1 for entry in out if entry.get("logo"))} logos')


def build(labels, before, today):
    """labels.json's contents. A label MusicBrainz didn't answer for keeps what it had last time."""
    kept = {entry.get("mbid"): entry for entry in before.get("labels", [])}
    out, failed = [], 0
    for label in labels:
        if not label.get("mbid"):
            continue
        entry = {"name": label["name"], "mbid": label["mbid"], "releases": []}
        if label.get("appleMusicID"):
            entry["appleMusicID"] = label["appleMusicID"]
        # Other spellings of its name that albums carry. The app goes by these for a label with no page on Apple Music.
        if label.get("aka"):
            entry["aka"] = label["aka"]
        try:
            entry["releases"] = records(releases_of(label["mbid"], today))
        except Exception as error:
            failed += 1
            log(f'  {label["name"]}: {error}')
            entry["releases"] = (kept.get(label["mbid"]) or {}).get("releases", [])
        log(f'{label["name"]}: {len(entry["releases"])}')
        out.append(entry)
    add_logos(out, labels, kept)
    return out, failed


def main():
    with open(LIST) as file:
        labels = json.load(file)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    if "--resolve" in sys.argv:
        unsure = resolve(labels, today)
        with open(LIST, "w") as file:
            json.dump(labels, file, indent=1, ensure_ascii=False)
            file.write("\n")
        print(f"{sum(1 for l in labels if l.get('mbid'))} of {len(labels)} labels have a MusicBrainz id.")
        for line in unsure:
            print("  unsure:", line)
        return
    before = {}
    if os.path.exists(OUT):
        with open(OUT) as file:
            before = json.load(file)
    out, failed = build(labels, before, today)
    # MusicBrainz down for the day: leave yesterday's file as it is.
    if not out or failed > len(out) / 2:
        sys.exit(f"MusicBrainz didn't answer for {failed} of {len(out)} labels. labels.json left alone.")
    result = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "MusicBrainz",
        "labels": out,
    }
    with open(OUT, "w") as file:
        json.dump(result, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")
    print(f"{len(out)} labels, {sum(len(l['releases']) for l in out)} records, {failed} unanswered.")


if __name__ == "__main__":
    main()
