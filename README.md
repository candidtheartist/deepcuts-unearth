# Deep Cuts — Unearth feed

A once-a-day job that works out which albums and songs are getting attention from music blogs,
curators and listeners, and writes one ranked list (`feed.json`) for the Deep Cuts app.
Every entry is tagged with its genres, so the app can put music close to someone's taste first.
There are no accounts and no server: GitHub runs the job and hosts the file.

## Sources

| Kind of music | Sources |
|---|---|
| Everything | Pitchfork (reviews, Best New Music, tracks, Best New Track), Bandcamp Daily, Stereogum (Album of the Week, single-song posts), The Guardian, Treble (reviews, Album of the Week), The Quietus, Beats Per Minute, Clash, God Is in the TV, Dusted, Sun 13, Everything Is Noise, musicOMH, Jenesaispop and Muzikalia (Spanish-language), Aquarium Drunkard, The Needle Drop, Gorilla vs. Bear |
| Alternative & indie | Post-Trash, Various Small Flames, The Alternative |
| Folk | KLOF Mag, The Guardian Folk Album of the Month, Various Small Flames |
| Country & Americana | Saving Country Music, Americana Highways, Americana UK |
| Punk & emo | The Alternative, Dying Scene, Louder Than War, Chorus.fm (songs) |
| Metal & heavy | Angry Metal Guy, Last Rites, Heavy Blog Is Heavy, The Sleeping Shaman, Echoes and Dust |
| Hip-hop | RapReviews, Shatter the Standards, Grown Up Rap, Monkeyboxing |
| R&B, soul and funk | Shatter the Standards, Twisted Soul, Monkeyboxing |
| Jazz | The Guardian Jazz Album of the Month, Jazz Trail, The Free Jazz Collective, UK Vibe (4/5 and up), UK Jazz News |
| Electronic | First Floor, Igloo Magazine, DJ Mag, Hard Wax (the shop's new arrivals: techno, jungle, footwork, dubstep), Ban Ban Ton Ton (Balearic, downtempo), Acid Stag; songs: Inverted Audio, The Ransom Note, Bolting Bits (house), Trommel (minimal), Trance Attack (trance), Stereofox (beats, downtempo) |
| Ambient & experimental | A Closer Listen, Headphone Commute, Ambientblog |
| Classical | The Guardian Classical, I Care If You Listen, MusicWeb International, The Classic Review |
| Global | The NATIVE, beehype, Rhythm Passport (songs), World A Reggae (songs) |
| Rock | The Fire Note, At The Barrier, Louder Than War, Echoes and Dust, Raven Sings the Blues (songs) |
| Pop | The Singles Jukebox, The Bias List (K-pop), Scandipop (all songs) |
| Latin | none yet: the good outlets block feed readers or write headlines that don't name the record. Latin entries come from the general sources and Apple's genre. |
| Listeners | ListenBrainz: albums and songs played well above their monthly average this week |

Only posts whose title names one album or one song are used (a review, an "album of the day"): news,
interviews, lists and round-ups are skipped.

Blog feeds are RSS, which sites publish for other apps to read. ListenBrainz data is open.
Nothing here scrapes a web page.

## How albums are ranked

- Each mention counts by source (hand-picked "best" feeds count most) and loses half its weight every 10 days.
- A listener trend adds to the score in proportion to how fast the album is rising.
- Albums picked up by more than one source get a 25% boost per extra source.
- The very top of ListenBrainz's chart is skipped: those albums are already everywhere.
- The list is the top 120 albums and 60 songs, plus up to 25 more albums and 10 more songs for each genre
  a specialist outlet covers, so a metal or jazz fan has more than a handful to look through.

## Genres

Each entry's `genres` is Apple's genre for it. Only when Apple has none (the job couldn't find it there)
do the genres of the specialist outlets that covered it stand in. The table that sorts Apple's genre names into ours is `GENRES`; the app keeps the
same one in `UnearthGenre.swift` to read the genres in someone's library, so change both together.

## Memory

Many blogs keep only their last ten posts in the feed. `memory.json` remembers every mention for 30 days,
so a review still counts after it has scrolled away. The workflow commits it next to `feed.json`.

Change the numbers at the top of `build_feed.py`, or add a source to `SOURCES`: give it a `parse`
(usually `titled(...)` with a pattern for its titles), and `genres` if it sticks to one kind of music.

## Running it

```bash
python3 build_feed.py --verbose
```

Standard library only. The first run takes about a quarter of an hour because Apple's search allows about 20 lookups a minute;
results are remembered in `feed.json` so later runs are quick.

## Hosting

Put this folder in a **public** GitHub repository. The workflow in `.github/workflows/unearth.yml`
runs every hour and commits `feed.json` and `memory.json`. The app reads it from:

```
https://raw.githubusercontent.com/candidtheartist/deepcuts-unearth/main/feed.json
```

Deep Cuts uses that address by default. It can be changed under Settings › Discover › Feed address.
