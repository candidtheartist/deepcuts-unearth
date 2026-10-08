# Deep Cuts — Unearth feed

A job that works out which albums and songs are getting attention from music blogs,
curators, radio DJs and listeners, and writes one ranked list (`feed.json`) for the Deep Cuts app.
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
| Radio | KEXP, KCRW (Eclectic 24 and the DJs' shows), triple j, NTS (picked and latest shows): what was played over the last week |
| Listeners | ListenBrainz: albums and songs played well above their monthly average this week |

Only posts whose title names one album or one song are used (a review, an "album of the day"): news,
interviews, lists and round-ups are skipped.

Blog feeds are RSS, which sites publish for other apps to read. ListenBrainz data is open, and the
stations' playlists come from the JSON their own sites and apps read. Nothing here scrapes a web page.

## Radio

A song counts as "getting played" with 3 or more spins across at least 2 broadcasts, or a play on more
than one station. An album counts when DJs are playing more than one track from it. Anything a station
says came out before last year is left out, and a pick that only radio is behind has to be known to be new
(from the station or from Apple). Each station's mention carries its spins (`plays`), which the app shows
as "Played 14 times this week". The stations are asked every 6 hours, not on every run; the answer is kept
in `memory.json`.

NTS is read differently: about 150 of its latest shows plus the picked ones. Its DJs rarely repeat each other
(in a week of 2,600 songs, about a dozen are played by two shows), so it's treated more like a curator than a
station on rotation:

- Two shows playing the same song is enough.
- One play counts too, a little (0.6, where a blog post is 1 to 2), when the record is known to be new, or when
  a blog or curator is behind it as well. That's also how a single NTS play can back an album a blog reviewed.
- NTS names the artist and song only, so the album and year come from Apple: up to 400 songs each time the
  stations are asked, starting with artists the blogs are writing about, then artists another station is playing,
  then artists more than one show played, then the rest. Answers are kept in `memory.json`, so after the first
  day or two only new songs are asked about. Tracklists credit artists their own way ("A, B" for Apple's
  "B & A"), so the title has to match but one shared artist name is enough.
- Rows with no real name ("Unknown Artist", "ID") are skipped.

Bump `RADIO_RULES` when these rules change, so the next run reads the stations again instead of reusing saved trends.

To add a station, write a reader that returns one row per play and add it to `RADIO`.

## How albums are ranked

- Each mention counts by source (hand-picked "best" feeds count most) and loses half its weight every 10 days.
- A listener trend adds to the score in proportion to how fast the album is rising.
- A radio station counts by how often it played the record: about half its weight for a few spins, all of it for ten or more.
- Agreement is what lifts an album: 20% per extra outlet, and 40% for each different kind of source that agrees
  (blogs, curators, radio, listeners). An album one blog reviewed sits below one that a blog reviewed and DJs are playing.
- At most 30 albums and 20 songs that only radio is behind, and 15 more of each that only NTS is behind.
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

## Label releases

`build_labels.py` writes a second file, `labels.json`: what each record label in `labels_list.json` has put out
in the last year, according to [MusicBrainz](https://musicbrainz.org). Apple Music's own list for a label is
often years behind, and it can't be asked for everything on a label, so the app uses these records as a way in:
it finds them on Apple Music by barcode (or title and artist) and follows their artists from there.
The list is the same for everybody, so nothing about which labels anyone follows leaves their phone.

```bash
python3 build_labels.py --verbose     # writes labels.json (one request a second: a few minutes)
python3 build_labels.py --resolve     # finds the MusicBrainz id of any label on the list without one
```

To add a label, put `{"name": "…"}` in `labels_list.json` (with `"appleMusicID"` if it has a page on Apple Music)
and run `--resolve`. A label with no `"appleMusicID"` is one Apple Music has no page for (Warp, Columbia, Interscope):
the app makes a page for each of those itself, from the records listed here, once it has at least three.
`"aka"` lists other spellings of its name that albums carry (`"Universal-Island Records Ltd."` for Island Records).
For a label with no page on Apple Music the build also looks for a logo: MusicBrainz's link to Wikidata, then
Wikidata's logo image, written to `labels.json` as the address of a picture on Wikimedia Commons (`"logo"`).
About half have one. To give a label one by hand, add `"logo": "https://…"` to its entry in `labels_list.json`.
Labels it calls "unsure" share their name with others: look the right one up on
musicbrainz.org and add its id as `"mbid"` by hand. `.github/workflows/labels.yml` runs the build once a day.

## Hosting

Put this folder in a **public** GitHub repository. The workflow in `.github/workflows/unearth.yml`
runs every hour and commits `feed.json` and `memory.json`. The app reads it from:

```
https://raw.githubusercontent.com/candidtheartist/deepcuts-unearth/main/feed.json
```

Deep Cuts uses that address by default. It can be changed under Settings › Discover › Feed address.
