"""Checks for the title parsers and helpers. Run with: python3 -m unittest test_build_feed"""
import datetime as dt
import unittest

import build_feed as b


class ParserTests(unittest.TestCase):
    def test_pitchfork_takes_artist_from_the_link(self):
        item = {"title": "Popstar", "link": "https://pitchfork.com/reviews/albums/tinashe-popstar/"}
        self.assertEqual(b.pitchfork(item), ("Tinashe", "Popstar"))

    def test_pitchfork_skips_when_the_link_does_not_end_with_the_album(self):
        self.assertIsNone(b.pitchfork({"title": "Popstar", "link": "https://pitchfork.com/reviews/albums/something-else/"}))

    def test_bandcamp_album_of_the_day(self):
        self.assertEqual(b.bandcamp_daily({"title": "Stef Chura, “Dancing Alone on the Concrete”"}),
                         ("Stef Chura", "Dancing Alone on the Concrete"))

    def test_bandcamp_lists_are_skipped(self):
        self.assertIsNone(b.bandcamp_daily({"title": "The Best Punk on Bandcamp, September 2026"}))

    def test_aquarium_drunkard(self):
        self.assertEqual(b.aquarium_drunkard({"title": "Andy Boay :: Doomed"}), ("Andy Boay", "Doomed"))
        self.assertIsNone(b.aquarium_drunkard({"title": "The Sagegazers :: A Mixtape"}))

    def test_needle_drop(self):
        self.assertEqual(b.needle_drop({"title": "Tinashe - Popstar ALBUM REVIEW"}), ("Tinashe", "Popstar"))
        self.assertEqual(b.needle_drop({"title": "Pain of Truth - Self-Titled ALBUM REVIEW"}), ("Pain of Truth", "Pain of Truth"))
        self.assertIsNone(b.needle_drop({"title": "THIS OR THAT: new albums"}))

    def test_beats_per_minute(self):
        self.assertEqual(b.beats_per_minute({"title": "Album Review: Protomartyr – Hotel Usona"}), ("Protomartyr", "Hotel Usona"))

    def test_stereogum_has_no_artist_until_matched(self):
        self.assertEqual(b.stereogum_aotw({"title": "Album Of The Week: Gilla Band Pugnello"}), ("", "Gilla Band Pugnello"))
        self.assertIsNone(b.stereogum_aotw({"title": "Something else"}))


class SongParserTests(unittest.TestCase):
    def test_pitchfork_track_strips_quotes(self):
        item = {"title": "“Free Byrd”", "link": "https://pitchfork.com/reviews/tracks/lily-konigsberg-free-byrd/"}
        self.assertEqual(b.pitchfork_track(item), ("Lily Konigsberg", "Free Byrd"))

    def test_gorilla_vs_bear(self):
        self.assertEqual(b.gorilla_vs_bear({"title": "Helena Deland – How Do You Like Me Now"}), ("Helena Deland", "How Do You Like Me Now"))
        self.assertIsNone(b.gorilla_vs_bear({"title": "Boyhood – Sparkle Dub / I’ll Dream Instead"}))

    def test_stereogum_song_needs_one_quoted_title(self):
        self.assertEqual(b.stereogum_song({"title": "Marissa Nadler – “Sky Burial”"}), ("Marissa Nadler", "Sky Burial"))
        self.assertIsNone(b.stereogum_song({"title": "Marissa Nadler & Stephen Brodsky – “Sky Burial” & “Drive It In”"}))
        self.assertIsNone(b.stereogum_song({"title": "Shane Parish Surprise Releases New Album"}))

    def test_pitchfork_feeds_are_one_family(self):
        self.assertEqual(b.family("pitchfork-bnt"), b.family("pitchfork-tracks"))
        self.assertNotEqual(b.family("stereogum-songs"), b.family("stereogum"))


class NewSourceTests(unittest.TestCase):
    """One real title per title style, read with the parser of the source it came from."""

    def parse(self, source_id, title):
        source = next(s for s in b.SOURCES + b.SONG_SOURCES if s["id"] == source_id)
        return source["parse"]({"title": title, "link": ""})

    def test_titles(self):
        cases = [
            ("guardian", "Taylor Swift: The Life of a Showgirl: The Encore review – flashes of humanity", ("Taylor Swift", "The Life of a Showgirl: The Encore")),
            ("guardian-classical", "Geneva Lewis: Barber, Zorn, Glass album review – virtuosity and warmth", ("Geneva Lewis", "Barber, Zorn, Glass")),
            ("treble", "Lily Seabird : Lightspheres On Their Way", ("Lily Seabird", "Lightspheres On Their Way")),
            ("quietus", "Susanne Sundfør – Revelations of Divine Love", ("Susanne Sundfør", "Revelations of Divine Love")),
            ("god-is-in-the-tv", "Tinashe – Popstar (Nice Life/Tinashe Music)", ("Tinashe", "Popstar")),
            ("dusted", "Brain Tourniquet — Sinking Deeper into Madness (Iron Lung)", ("Brain Tourniquet", "Sinking Deeper into Madness")),
            ("post-trash", 'Wishy - "Nature\'s Pill" | Album Review', ("Wishy", "Nature's Pill")),
            ("saving-country-music", "Album Review – Mac Cornish’s “Wayfaring Woman”", ("Mac Cornish", "Wayfaring Woman")),
            ("americana-highways", "REVIEW: Pieta Brown “Dreamin’ Of”", ("Pieta Brown", "Dreamin’ Of")),
            ("the-alternative", "Review: Smidley – ‘Murphy Horse’", ("Smidley", "Murphy Horse")),
            ("angry-metal-guy", "Hippotraktor – Annihilist Review", ("Hippotraktor", "Annihilist")),
            ("sleeping-shaman", "Review: Green Lung ‘Necropolitan’", ("Green Lung", "Necropolitan")),
            ("rapreviews", "midwxst :: Secrets", ("midwxst", "Secrets")),
            ("shatter-the-standards", "Album Review: Purdy by The Koreatown Oddity", ("The Koreatown Oddity", "Purdy")),
            ("free-jazz", "Ava Mendoza - Alive Alone, Alive Together (Burning Ambulance Music, 2026)", ("Ava Mendoza", "Alive Alone, Alive Together")),
            ("uk-vibe", "Him & Earl ‘Falling Backwards’ 2LP (Wah Wah 45s) 5/5", ("Him & Earl", "Falling Backwards")),
            ("a-closer-listen", "Blair Coron ~ Woven Ground", ("Blair Coron", "Woven Ground")),
            ("igloo", "SUAHN :: Distort Everything Forever (Self Released)", ("SUAHN", "Distort Everything Forever")),
            ("the-native", "Review: ‘Swaguu’ by Seyi Vibez", ("Seyi Vibez", "Swaguu")),
            ("raven-sings-the-blues", "Ryley Walker – “Carrier”", ("Ryley Walker", "Carrier")),
            ("bias-list", "Song Review: WAYF Boys – A4M", ("WAYF Boys", "A4M")),
            ("inverted-audio", "Premiere: Yayoba – Transparent Waves", ("Yayoba", "Transparent Waves")),
            ("musicomh", "Victoria Monét – Frequency Of Love", ("Victoria Monét", "Frequency Of Love")),
            ("jenesaispop", "Aiko el grupo / Señales Brutales", ("Aiko el grupo", "Señales Brutales")),
            ("muzikalia", "From – Furar Hondo (Humo Internacional)", ("From", "Furar Hondo")),
            ("fire-note", "Psychic Flowers: Columbarium Niches [Album Review]", ("Psychic Flowers", "Columbarium Niches")),
            ("at-the-barrier", "Martin Simpson – Some Kind Of Jubilee: Album Review", ("Martin Simpson", "Some Kind Of Jubilee")),
            ("americana-uk", "Jayne Pomplas “This Is How The World Ends”", ("Jayne Pomplas", "This Is How The World Ends")),
            ("grown-up-rap", "The Koreatown Oddity – ‘Purdy’", ("The Koreatown Oddity", "Purdy")),
            ("grown-up-rap-songs", "Cavalier – ‘Bring It Back’ (video)", ("Cavalier", "Bring It Back")),
            ("monkeyboxing", "ELEVEN 76: A Day Of Unrest LP", ("ELEVEN 76", "A Day Of Unrest")),
            ("musicweb", "Brian: Symphonies 11 and 15 (Naxos)", ("Brian", "Symphonies 11 and 15")),
            ("classic-review", "Review: Verdi – Opera Arias – Lise Davidsen, Edward Gardner", ("Lise Davidsen", "Opera Arias")),
            ("beehype", "Japan: んoon – “Zoo” LP", ("んoon", "Zoo")),
            ("beehype-songs", "Argentina: Juana Sallies – “Ancora”", ("Juana Sallies", "Ancora")),
            ("rhythm-passport", "Daily Discovery: Karen y los Remedios – Curandera", ("Karen y los Remedios", "Curandera")),
            ("world-a-reggae", "Love Overflow – Protoje (Music Video)", ("Protoje", "Love Overflow")),
            ("scandipop", "Raylee – Going Out Out", ("Raylee", "Going Out Out")),
            ("hard-wax", "The Hacker & Rein: We Come Alive", ("The Hacker & Rein", "We Come Alive")),
            ("ban-ban-ton-ton", "Black Meteoric Star / Wet / Ransom Note – By John Matthews", ("Black Meteoric Star", "Wet")),
            ("acid-stag", "Logic1000 – Confirmation! (LP)", ("Logic1000", "Confirmation!")),
            ("acid-stag-songs", "Lance Savali – ‘All I See’", ("Lance Savali", "All I See")),
            ("ambientblog", "Looper – Interior-Day", ("Looper", "Interior-Day")),
            ("twisted-soul", "Album: Hania Derej Trio – Don’t Look Behind, Always Forward!", ("Hania Derej Trio", "Don’t Look Behind, Always Forward!")),
            ("twisted-soul-songs", "Carmen Quill – Plaza (TS Premiere)", ("Carmen Quill", "Plaza")),
            ("bolting-bits", "Harrison BDP – This One’s For You [Jupiter’s Depth]", ("Harrison BDP", "This One’s For You")),
            ("trommel", "Premiere: A2 – DJ Bowlcut – Dig This [PBAVINYL002]", ("DJ Bowlcut", "Dig This")),
            ("trance-attack", "Factor B feat. Cat Martin – Crashing Over (Lost Minds Remix)", ("Factor B feat. Cat Martin", "Crashing Over")),
            ("stereofox", "edbl – A Moment (ft. JONES)", ("edbl", "A Moment")),
        ]
        for source, title, expected in cases:
            self.assertEqual(self.parse(source, title), expected, source)

    def test_news_interviews_and_low_marks_are_skipped(self):
        for source, title in [
            ("guardian", "Various artists: Asili ya Mama review – Tanzanian field recordings"),
            ("treble", "Carly Rae Jepsen announces 2027 tour dates"),
            ("quietus", "Reissue of the Week: Elastica’s Debut Album is “Untouchable Louche Perfection”"),
            ("sun-13", "Melanie Radford Interview: “I hope people can find comfort in it”"),
            ("americana-highways", "Show Review: Ringo Starr and His All Starr Band at MGM Music Hall"),
            ("angry-metal-guy", "Record(s) o’ the Month – June 2026"),
            ("first-floor", "First Floor #330 – Flying out the Door"),
            ("uk-vibe", "Rollo Doherty ‘The Jelly Man’ LP/Picture Disc/CD (Lewis Recordings) 3/5"),
            ("i-care-if-you-listen", "Review: Ultima Festival 2026"),
            ("dj-mag", "Listen to Sara Landry's new single, 'Awakening'"),
            ("trance-attack", "Group Therapy 696 (02.10.2026) with Above & Beyond and Mark Sherry"),
            ("ambientblog", "Liminal State: September 2026 overview"),
            ("musicomh", "LPO/Gardner review – a profoundly moving War Requiem opens the new season at the RFH"),
            ("muzikalia", "Placebo (Movistar Arena) Madrid 01/10/26"),
            ("americana-uk", "Live Review: Joe Martin + Maggie May Treanor, The Town Hall, Kirton in Lindsey – 26th September 2026"),
            ("grown-up-rap", "Cavalier – ‘Bring It Back’ (video)"),
            ("beehype", "Argentina: Juana Sallies – “Ancora”"),
            ("ban-ban-ton-ton", "Looking for The Balearic Beat / September 2026"),
        ]:
            self.assertIsNone(self.parse(source, title), title)

    def test_every_source_has_what_the_job_needs(self):
        genres = {g for g, _, _ in b.GENRES}
        ids = [s["id"] for s in b.SOURCES + b.SONG_SOURCES]
        self.assertEqual(len(ids), len(set(ids)))
        for source in b.SOURCES + b.SONG_SOURCES:
            self.assertTrue(source["feed"].startswith("https://"), source["id"])
            self.assertTrue(set(source.get("genres", [])) <= genres, source["id"])


class GenreTests(unittest.TestCase):
    def test_apple_genre_names_are_sorted(self):
        """The same names and answers as UnearthTasteTests in the app: the two tables must agree."""
        expected = {
            "Alternative": "indie", "Hip-Hop/Rap": "hiphop", "Alternative Rap": "hiphop", "Trap": "hiphop",
            "R&B/Soul": "rnb", "Soul Jazz": "jazz", "Indie Folk": "folk", "Singer/Songwriter": "folk",
            "Post-Punk": "indie", "Pop Punk": "punk", "Death Metal/Black Metal": "metal", "Hard Rock": "rock",
            "K-Pop": "pop", "Pop Latino": "latin", "Afrobeats": "global", "Worldwide": "global",
            "Dance": "electronic", "Dubstep": "electronic", "Ambient": "ambient", "Americana": "country",
            "Classical Crossover": "classical", "Music": None, "Soundtrack": None,
        }
        for name, genre in expected.items():
            self.assertEqual(b.genre_of(name), genre, name)

    def test_apples_genre_wins_and_outlets_only_stand_in(self):
        mentions = [{"source": "angry-metal-guy"}, {"source": "pitchfork"}]
        self.assertEqual(b.entry_genres({"genre": "Rock"}, mentions), ["rock"])
        self.assertEqual(b.entry_genres(None, mentions), ["metal"])
        self.assertEqual(b.entry_genres({"genre": "Soundtrack"}, mentions), ["metal"])
        self.assertEqual(b.entry_genres({"genre": "Soundtrack"}, []), [])


class ListTests(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)

    def test_each_genre_can_add_a_few_past_the_limit(self):
        general = [{"score": 10 - i, "mentions": [{"source": "pitchfork"}]} for i in range(5)]
        metal = [{"score": 1 - i / 10, "mentions": [{"source": "angry-metal-guy"}]} for i in range(5)]
        chosen = b.shortlist(general + metal, limit=3, extra_per_genre=2)
        self.assertEqual([e["score"] for e in chosen], [10, 9, 8, 1.0, 0.9])

    def test_listener_only_entries_are_capped(self):
        entries = [{"score": i, "mentions": []} for i in range(b.MAX_LISTENER_ONLY + 10)]
        self.assertEqual(len(b.shortlist(entries, limit=500, extra_per_genre=0)), b.MAX_LISTENER_ONLY)

    def test_mentions_are_remembered_while_they_are_in_the_window(self):
        sources = [{"id": "clash", "weight": 1.0}]
        fresh = [{"source": "clash", "artist": "A", "album": "B", "url": "https://x/1", "date": self.NOW, "image": None, "weight": 1.0}]
        remembered = [
            {"source": "clash", "artist": "A", "album": "B", "url": "https://x/1", "date": "2026-10-04T00:00:00Z"},   # already there
            {"source": "clash", "artist": "C", "album": "D", "url": "https://x/2", "date": "2026-09-20T00:00:00Z"},   # kept
            {"source": "clash", "artist": "E", "album": "F", "url": "https://x/3", "date": "2026-08-01T00:00:00Z"},   # too old
            {"source": "gone", "artist": "G", "album": "H", "url": "https://x/4", "date": "2026-10-01T00:00:00Z"},    # source removed
        ]
        merged = b.remember(fresh, remembered, sources, self.NOW)
        self.assertEqual([m["artist"] for m in merged], ["A", "C"])
        self.assertEqual(merged[1]["weight"], 1.0)

    def test_feeds_from_one_outlet_are_one_family(self):
        self.assertEqual(b.family("guardian-jazz"), b.family("guardian"))
        self.assertEqual(b.family("treble-aotw"), b.family("treble"))
        self.assertEqual(b.family("clash"), "clash")


class HelperTests(unittest.TestCase):
    def test_key_ignores_case_editions_and_the(self):
        self.assertEqual(b.key("The Strokes", "Is This It (Deluxe)"), b.key("strokes", "IS THIS IT"))
        self.assertNotEqual(b.key("Cleo Sol", "Gold"), b.key("Alabaster DePlume", "Gold"))

    def test_dates(self):
        self.assertEqual(b.parse_date("Thu, 01 Oct 2026 04:03:00 +0000"), dt.datetime(2026, 10, 1, 4, 3, tzinfo=dt.timezone.utc))
        self.assertEqual(b.parse_date("2026-10-01T04:03:00Z"), dt.datetime(2026, 10, 1, 4, 3, tzinfo=dt.timezone.utc))
        self.assertIsNone(b.parse_date("not a date"))

    def test_feed_items_reads_rss_and_atom(self):
        rss = b"<rss><channel><item><title>A &amp; B</title><link>https://x/y</link><pubDate>Thu, 01 Oct 2026 04:03:00 +0000</pubDate></item></channel></rss>"
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link href="https://x/z"/><published>2026-10-01T04:03:00+00:00</published></entry></feed>'
        self.assertEqual([i["title"] for i in b.feed_items(rss)], ["A & B"])
        self.assertEqual([i["link"] for i in b.feed_items(atom)], ["https://x/z"])


if __name__ == "__main__":
    unittest.main()
