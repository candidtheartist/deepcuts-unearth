# Deep Cuts — Unearth feed

A once-a-day job that works out which albums and songs are getting attention from music blogs,
curators and listeners, and writes one ranked list (`feed.json`) for the Deep Cuts app.
There are no accounts and no server: GitHub runs the job and hosts the file.

## Sources

| Source | Kind | What counts |
|---|---|---|
| Pitchfork Best New Music | blog | every album in the feed |
| Pitchfork | blog | album reviews |
| Bandcamp Daily | blog | Album of the Day posts |
| Stereogum | blog | Album of the Week |
| Beats Per Minute | blog | album reviews |
| Aquarium Drunkard | curator | album posts |
| The Needle Drop | curator | album reviews |
| ListenBrainz | listeners | albums and songs played well above their monthly average this week |
| Pitchfork Best New Track, Pitchfork Tracks | blog | track reviews (songs) |
| Gorilla vs. Bear | curator | single-song posts |
| Stereogum | blog | single-song posts |

Blog feeds are RSS, which sites publish for other apps to read. ListenBrainz data is open.
Nothing here scrapes a web page.

## How albums are ranked

- Each mention counts by source (hand-picked "best" feeds count most) and loses half its weight every 10 days.
- A listener trend adds to the score in proportion to how fast the album is rising.
- Albums picked up by more than one source get a 25% boost per extra source.
- The very top of ListenBrainz's chart is skipped: those albums are already everywhere.

Change the numbers at the top of `build_feed.py`, or add a source to `SOURCES`.

## Running it

```bash
python3 build_feed.py --verbose
```

Standard library only. It takes a few minutes because Apple's album search allows about 20 lookups a minute;
results are remembered in `feed.json` so later runs are quick.

## Hosting

Put this folder in a **public** GitHub repository. The workflow in `.github/workflows/unearth.yml`
runs daily and commits `feed.json`. The app reads it from:

```
https://raw.githubusercontent.com/candidtheartist/deepcuts-unearth/main/feed.json
```

Deep Cuts uses that address by default. It can be changed under Settings › Discover › Feed address.
