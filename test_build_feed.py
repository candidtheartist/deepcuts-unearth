import json
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
            ("the-native", "Review: Davido’s ‘5ive’", ("Davido", "5ive")),
            ("the-native", "Review: Ruger’s “BlownBoy Ru”", ("Ruger", "BlownBoy Ru")),
            ("the-native", "Review: Rigo Kamp’s ‘Rigo Kamp’ EP", ("Rigo Kamp", "Rigo Kamp")),
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
            ("nme", "Troye Sivan – ‘She’s the Best’ review: late-night revelations set to a ’90s alt-dance groove", ("Troye Sivan", "She’s the Best")),
            ("diy", "Gilla Band — Pugnello", ("Gilla Band", "Pugnello")),
            ("uncut", "Julia Jacklin’s The Gem reviewed: Australian road warrior discovers there’s no place like home", ("Julia Jacklin", "The Gem")),
            ("uncut", "Neil Young & The Chrome Hearts’ Second Song reviewed: an epic meditation on memory", ("Neil Young & The Chrome Hearts", "Second Song")),
            ("our-culture", "Album Review: Troye Sivan, ‘She’s the Best’", ("Troye Sivan", "She’s the Best")),
            ("spectrum-culture", "Jeremiah Chiu: Edge Conditions", ("Jeremiah Chiu", "Edge Conditions")),
            ("spill", "SPILL ALBUM REVIEW: TEENAGE FANCLUB – DON’T DARE TO DREAM", ("Teenage Fanclub", "Don’t Dare To Dream")),
            ("mxdwn", "Album Review: Meshell Ndegeocello – Synonym", ("Meshell Ndegeocello", "Synonym")),
            ("boomkat", "Olavi Louhivuori, Eivind Lønning & Kim Myhr - Nubes", ("Olavi Louhivuori, Eivind Lønning & Kim Myhr", "Nubes")),
            ("word-is-bond", "Jimmy B Fearless – ‘The Love is Real 2: Fortuna’ [Album Review]", ("Jimmy B Fearless", "The Love is Real 2: Fortuna")),
            ("inverted-audio-reviews", "Miles: Vault", ("Miles", "Vault")),
            ("r-hiphopheads", "[FRESH ALBUM] Johnny Venus (of EARTHGANG) - The 11th Hour", ("Johnny Venus", "The 11th Hour")),
            ("r-indieheads", "[FRESH ALBUM] Cameron Winter - Live at Carnegie Hall", ("Cameron Winter", "Live at Carnegie Hall")),
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
            ("the-native", "RAMPA LAGOS SERVES UP AN AFROHOUSE SPECTACLE"),
            ("trance-attack", "Group Therapy 696 (02.10.2026) with Above & Beyond and Mark Sherry"),
            ("ambientblog", "Liminal State: September 2026 overview"),
            ("musicomh", "LPO/Gardner review – a profoundly moving War Requiem opens the new season at the RFH"),
            ("muzikalia", "Placebo (Movistar Arena) Madrid 01/10/26"),
            ("americana-uk", "Live Review: Joe Martin + Maggie May Treanor, The Town Hall, Kirton in Lindsey – 26th September 2026"),
            ("grown-up-rap", "Cavalier – ‘Bring It Back’ (video)"),
            ("beehype", "Argentina: Juana Sallies – “Ancora”"),
            ("ban-ban-ton-ton", "Looking for The Balearic Beat / September 2026"),
            ("uncut", "Elvis Costello’s My Aim Is True reissued: the making of a new-wave masterpiece"),
            ("uncut", "Oasis: Don’t Look Back In Anger reviewed – the Gallaghers’ reunion as rock’n’roll psychodrama"),
            ("spectrum-culture", "Concert Review: Linda May Han Oh"),
            ("mxdwn", "Live Review: The Linda Lindas at Brooklyn Steel"),
            ("our-culture", "8 New Songs Out Today to Listen To: @, Dummy, and More"),
            ("word-is-bond", "Top submissions  EP2 Oct 2026"),
            ("r-indieheads", "[ALBUM DISCUSSION] Julia Jacklin - The Gem"),
        ]:
            self.assertIsNone(self.parse(source, title), title)

    def test_electronic_sound_only_takes_its_reviews(self):
        review = {"title": "Marina Herlop ‘Dja Dja’ (Bandcamp)", "link": "https://www.electronicsound.co.uk/reviews/marina-herlop-dja-dja/"}
        column = {"title": "Ron Hardy ‘Sensation’", "link": "https://www.electronicsound.co.uk/columns/needs-must/ron-hardy/"}
        self.assertEqual(b.electronic_sound(review), ("Marina Herlop", "Dja Dja"))
        self.assertIsNone(b.electronic_sound(column))

    def test_sources_that_share_a_feed_ask_once_and_take_their_own_threads(self):
        now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
        threads = [{"title": f"[FRESH ALBUM] {sub} Artist {i} - Album {i}", "link": f"https://x/{sub}/{i}", "date": now,
                    "image": None, "description": "", "categories": [sub]}
                   for i in range(20) for sub in ("hiphopheads", "indieheads")]
        asked = []
        real, b.read_feed = b.read_feed, lambda address: asked.append(address) or threads
        try:
            reddit = [s for s in b.SOURCES if s["feed"] == b.REDDIT_FRESH]
            mentions, status = b.blog_mentions(now, reddit)
        finally:
            b.read_feed = real
        self.assertEqual(asked, [b.REDDIT_FRESH])
        self.assertEqual(status, {"r-hiphopheads": "15 found", "r-indieheads": "15 found"})  # `top`: the 15 most upvoted
        self.assertTrue(all(m["artist"].startswith(m["source"][2:]) for m in mentions))

    def read(self, source_id, title, link, *tags):
        source = next(s for s in b.SOURCES if s["id"] == source_id)
        return source["parse"]({"title": title, "link": link, "categories": list(tags)})

    def test_a_sentence_headline_is_read_from_the_posts_tags_and_link(self):
        # The tag names the artist, and the link reads artist-album-review.
        self.assertEqual(self.read("rolling-stone", "Leon Bridges Shakes Up His Sound Once Again",
                                   "https://www.rollingstone.com/music/music-album-reviews/leon-bridges-happiness-anytime-album-review-1235629868/",
                                   "Album Reviews", "Music", "Jungle", "Leon Bridges"), ("Leon Bridges", "Happiness Anytime"))
        self.assertEqual(self.read("rolling-stone", "Fontaines D.C. Make Dystopian Rock That Hits Just Right for Dark Times",
                                   "https://www.rollingstone.com/music/music-album-reviews/fontaines-d-c-dopamine-chamber-album-review-1235637810/",
                                   "Album Reviews", "Music", "Fontaines D.C."), ("Fontaines D.C.", "Dopamine Chamber"))
        # What the headline quotes is the album as it's written, apostrophe and all.
        self.assertEqual(self.read("variety", "Troye Sivan Is at His Best on ‘She’s the Best,’ a Weird, Wild Exploration",
                                   "https://variety.com/2026/music/album-reviews/troye-sivan-shes-the-best-album-review-1236905931/",
                                   "Album Reviews", "News", "Reviews", "Troye Sivan"), ("Troye Sivan", "She’s the Best"))
        self.assertEqual(self.read("rolling-stone", "On ‘Cocoon’, Yeat Returns to Familiar Territory With Mostly Dull Results",
                                   "https://www.rollingstone.com/music/music-album-reviews/yeat-cocoon-new-music-drake-feature-1235630031/",
                                   "Album Reviews", "Music", "Hip-Hop", "Rap", "Yeat"), ("Yeat", "Cocoon"))
        # A tag in small letters is spelled as the headline has it.
        self.assertEqual(self.read("popmatters", "Ibibio Sound Machine Nuance the Dancefloor Superbly",
                                   "https://www.popmatters.com/ibibio-sound-machine-chopping-mountain",
                                   "Music", "Music Reviews", "Reviews", "afro-funk", "ibibio sound machine"), ("Ibibio Sound Machine", "Chopping Mountain"))

    def test_with_no_tags_the_quoted_album_says_where_the_links_artist_ends(self):
        self.assertEqual(self.read("albumism", "Cleo Sol’s ‘Gentlewoman’ Embraces Stasis Amidst Moments of Musical Revelation | Album Review",
                                   "https://albumism.com/reviews/cleo-sol-gentlewoman", "REVIEW"), ("Cleo Sol", "Gentlewoman"))
        self.assertEqual(self.read("albumism", "With Confidence and Charisma, Ego Ella May Transforms Her ‘Good Intentions’ into Great Songs | Album Review",
                                   "https://albumism.com/reviews/ego-ella-may-good-intentions", "REVIEW"), ("Ego Ella May", "Good Intentions"))
        # A long run of link words the headline doesn't spell out isn't a name.
        self.assertIsNone(self.read("albumism", "Rakim, Kurupt & Masta Killa Join Forces for Collaborative ‘The Godbody LP’ | Album Review",
                                    "https://albumism.com/reviews/rakim-kurupt-masta-killa-dpg-wu-tang-wu-tang-dpg-the-godbody-lp", "REVIEW"))

    def test_when_the_link_is_the_headline_again_the_artist_is_the_tag_it_mentions(self):
        self.assertEqual(self.read("underground-hip-hop-blog", "Souls of Mischief’s New EP “Clean Getaway” Isn’t as Great as “All Said & Done” (EP Review)",
                                   "https://undergroundhiphopblog.com/albums/souls-of-mischiefs-new-ep-clean-getaway-isnt-as-great/",
                                   "Albums", "Reviews", "A-Plus", "Hieroglyphics", "Souls of Mischief"), ("Souls of Mischief", "Clean Getaway"))
        self.assertIsNone(self.read("underground-hip-hop-blog", "GZA Delivers a Night of Liquid Swords in Orange County",
                                    "https://undergroundhiphopblog.com/news/gza-delivers-a-night-of-liquid-swords/", "GZA"))

    def test_tagged_leaves_out_what_it_cannot_place(self):
        self.assertIsNone(self.read("popmatters", "Ohana Festival 2026: Grooving in the Sun with Pearl Jam",
                                    "https://www.popmatters.com/ohana-festival-2026", "Events", "Music Reviews", "abe laboriel jr."))
        self.assertIsNone(self.read("variety", "Joy Division’s Massive ‘Eternal’ Boxed Set Documents the Band’s Brilliance",
                                    "https://variety.com/2026/music/album-reviews/joy-division-eternal-live-boxed-set-album-review-1236/",
                                    "Album Reviews", "Joy Division"))
        self.assertIsNone(self.read("rolling-stone", "Leon Bridges Shakes Up His Sound Once Again",
                                    "https://www.rollingstone.com/music/music-album-reviews/leon-bridges-album-review-1235629868/", "Leon Bridges"))

    def test_stereogums_tag_says_where_the_artist_ends(self):
        item = {"title": "Album Of The Week: Sophia Stel the greatest album of all time", "link": "", "categories": ["Album Of The Week", "Reviews", "Sophia Stel"]}
        self.assertEqual(b.stereogum_aotw(item), ("Sophia Stel", "the greatest album of all time"))
        self.assertEqual(b.stereogum_aotw({**item, "categories": []}), ("", "Sophia Stel the greatest album of all time"))

    NPR_NOTES = """Host Stephen Thompson breaks down the best albums, and critics offer their picks in the lightning round.<br><br>
The Starting 5<br><br>(00:39) Album No. 1<br>- Cameron Winter, 'Live at Carnegie Hall'<br><br>(07:30) Album No. 2<br>- Troye Sivan, 'She's the Best'<br><br>
(34:27 The Lightning Round<br><br>- Dawn Richard, 'creole culture'<br>- Allison de Groot &amp; Rich Ruth, 'Allison de Groot &amp; Rich Ruth'<br><br>
Sample the albums via our <a href="https://x">New Music Friday playlist</a>.<br>Credits:<br>- Host: Stephen Thompson"""

    def npr(self, title="New Music Friday: The best albums out Oct. 9"):
        import xml.sax.saxutils as x
        rss = f"<rss><channel><item><title>{title}</title><link>https://npr.org/e/1</link><pubDate>Fri, 09 Oct 2026 10:00:00 +0000</pubDate>" \
              f"<description>{x.escape(self.NPR_NOTES)}</description></item></channel></rss>"
        items = list(b.feed_items(rss.encode()))
        real, b.read_feed = b.read_feed, lambda address: items
        try:
            sources = [s for s in b.SOURCES if s.get("family") == "npr"]
            return b.blog_mentions(dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc), sources)[0]
        finally:
            b.read_feed = real

    def test_a_post_that_lists_records_gives_one_mention_each(self):
        found = [(m["source"], m["artist"], m["album"], m["weight"]) for m in self.npr()]
        self.assertEqual(found, [
            ("npr-new-music-friday", "Cameron Winter", "Live at Carnegie Hall", 3.0),
            ("npr-new-music-friday", "Troye Sivan", "She's the Best", 3.0),
            ("npr-critics-picks", "Dawn Richard", "creole culture", 2.0),
            ("npr-critics-picks", "Allison de Groot & Rich Ruth", "Allison de Groot & Rich Ruth", 2.0),
        ])
        self.assertEqual(self.npr("Alt.Latino: Tim, The Beatles and all of Brazil"), [])  # the feed's other shows list songs the same way

    def test_every_record_of_a_list_is_remembered(self):
        now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
        old = [{**m, "date": "2026-10-09T10:00:00Z"} for m in self.npr()]
        merged = b.remember([], old, b.SOURCES, now)
        self.assertEqual(len(merged), 4)   # four records from one post: its address alone doesn't tell them apart

    LOBF_PAGE = """<html><script type="application/ld+json">{"@context": "http://schema.org", "@type": "MusicAlbum",
        "name": "do you love the color of the sky?", "review": {"@type": "Review", "reviewBody": "Basking in the afterglow",
        "reviewRating": {"@type": "Rating", "ratingValue": "%s", "bestRating": "10", "worstRating": "0"}}}</script>
        <a href="https://www.thelineofbestfit.com/news">News</a>
        <div class="uppercase"> <a href="https://www.thelineofbestfit.com/artists/dummy">Dummy</a> </div></html>"""

    def lobf(self, score, pages, robots="User-agent: *\nDisallow: /cache/", budget=None):
        now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
        item = {"title": "Dummy offer up a pastel-hued escape fantasy", "link": "https://www.thelineofbestfit.com/albums/dummy-offer-up",
                "date": now, "image": None, "description": "", "text": "", "categories": ["Albums"]}
        asked = []

        def fetch(url, timeout=30, accept=None):
            asked.append(url)
            return (robots if url.endswith("/robots.txt") else self.LOBF_PAGE % score).encode()
        source = [s for s in b.SOURCES if s["id"] == "line-of-best-fit"]
        real = b.read_feed, b.fetch, b.time.sleep, b.MAX_PAGES
        b.read_feed, b.fetch, b.time.sleep = (lambda address: [item]), fetch, (lambda seconds: None)
        b.MAX_PAGES = real[3] if budget is None else budget
        b.ROBOTS.clear()
        try:
            return b.blog_mentions(now, source, pages)[0], [url for url in asked if not url.endswith("/robots.txt")]
        finally:
            b.read_feed, b.fetch, b.time.sleep, b.MAX_PAGES = real

    def test_a_review_page_says_which_album_and_its_score(self):
        pages = {}
        mentions, read = self.lobf(7, pages)
        self.assertEqual([(m["artist"], m["album"], m["weight"]) for m in mentions], [("Dummy", "do you love the color of the sky?", 1.5)])
        self.assertEqual(len(read), 1)
        self.assertNotIn("Basking", str(pages))                      # none of the review's words are kept
        mentions, read = self.lobf(7, pages)
        self.assertEqual((len(mentions), read), (1, []))             # the page is read once

    def test_a_score_changes_what_a_review_weighs(self):
        self.assertEqual(self.lobf(9, {})[0][0]["weight"], 3.0)      # 9/10: twice the weight
        self.assertEqual(self.lobf(8, {})[0][0]["weight"], 2.25)
        self.assertEqual(self.lobf(5, {})[0], [])                    # 5/10 isn't a recommendation
        now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
        great = [{**m, "date": "2026-10-13T00:00:00Z", "weight": None} for m in self.lobf(9, {})[0]]
        self.assertEqual(b.remember([], great, b.SOURCES, now)[0]["weight"], 3.0)   # and still does once it's only remembered

    def test_pages_are_left_alone_where_robots_txt_says_so_or_the_run_has_read_enough(self):
        self.assertEqual(self.lobf(7, {}, robots="User-agent: *\nDisallow: /albums/"), ([], []))
        self.assertEqual(self.lobf(7, {}, budget=0), ([], []))

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


class RadioTests(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)

    def play(self, artist, song, album="", year="2026", broadcast="a"):
        return {"artist": artist, "song": song, "album": album, "year": year, "broadcast": broadcast, "at": self.NOW}

    def test_a_song_needs_a_few_spins_on_more_than_one_show(self):
        plays = {"kexp": [self.play("A", "Hit", broadcast=b) for b in "xyz"]          # three shows: counts
                 + [self.play("B", "Pet", broadcast="x") for _ in range(5)]          # one DJ's favourite: doesn't
                 + [self.play("C", "Once")]}
        trends = b.radio_trends(plays, "song", self.NOW)
        self.assertEqual([(t["artist"], t["stations"]["kexp"]["plays"]) for t in trends], [("A", 3)])

    def test_two_stations_are_enough_on_their_own(self):
        plays = {"kexp": [self.play("A", "Hit")], "kcrw": [self.play("a", "HIT (Radio Edit)", year=None)]}
        trends = b.radio_trends(plays, "song", self.NOW)
        self.assertEqual(sorted(trends[0]["stations"]), ["kcrw", "kexp"])
        self.assertEqual(trends[0]["year"], "2026")

    def test_old_music_is_left_out(self):
        plays = {"kexp": [self.play("Sepultura", "Dead Embryonic Cells", year="1991", broadcast=b) for b in "xyz"]}
        self.assertEqual(b.radio_trends(plays, "song", self.NOW), [])

    def test_an_album_needs_more_than_one_of_its_tracks_played(self):
        plays = {"kexp": [self.play("A", "One", "LP", broadcast=b) for b in "xy"] + [self.play("A", "Two", "LP", broadcast="z")]
                 + [self.play("B", "Single", "Single", broadcast=b) for b in "xyz"]          # a single, not an album
                 + [self.play("C", "Only", "Other LP", broadcast=b) for b in "xyz"]}        # one track on repeat
        self.assertEqual([t["album"] for t in b.radio_trends(plays, "album", self.NOW)], ["LP"])

    def test_more_spins_weigh_more_up_to_the_stations_weight(self):
        def trend(n):
            return {"artist": "A", "album": "B", "year": "2026", "stations": {"kexp": {"plays": n, "last": "2026-10-03T00:00:00Z"}}}
        few, many, lots = (b.radio_mentions([trend(n)])[0] for n in (3, 10, 40))
        self.assertLess(few["weight"], many["weight"])
        self.assertEqual(many["weight"], lots["weight"])
        self.assertEqual(few["plays"], 3)

    def test_square_bracket_notes_are_dropped(self):
        self.assertEqual(b.bare("Green Honda [triple j live recording, Laneway Festival]"), "Green Honda")
        self.assertEqual(b.bare("Song (Remix)"), "Song (Remix)")

    def test_two_nts_shows_are_enough(self):
        plays = {"nts": [self.play("A", "Rare", year=None, broadcast=b) for b in "xy"],
                 "kexp": [self.play("B", "Twice", broadcast=b) for b in "xy"]}
        self.assertEqual([t["artist"] for t in b.radio_trends(plays, "song", self.NOW)], ["A"])
        mention = b.radio_mentions(b.radio_trends(plays, "song", self.NOW))[0]
        self.assertGreater(mention["weight"], 2.0 * 0.5)

    def test_apple_gives_nts_plays_their_album_and_year(self):
        asked = []

        def lookup(artist, title, cache, kind, loose=False):
            asked.append(title)
            return {"One": {"collection": "LP", "year": "2026"}, "Two": {"collection": "Two - Single", "year": "2026"},
                    "Old": {"collection": "Classic - EP", "year": "1999"}}.get(title)

        real, b.apple_lookup = b.apple_lookup, lookup
        try:
            plays = {"kexp": [self.play("A", "Three", "LP")],
                     "nts": [self.play("A", "One", year=None), self.play("A", "Two", year=None),          # A is on KEXP too
                             self.play("B", "Old", year=None, broadcast="x"), self.play("B", "Old", year=None, broadcast="y"),
                             self.play("C", "Stranger", year=None),                                        # one show, nobody else: asked last
                             self.play("D", "Known", year=None, broadcast="x"), self.play("D", "Gone", year=None, broadcast="y")]}
            resolved = b.fill_in_from_apple(plays, {b.key("D", "Known"): {"album": "Saved", "year": "2025"}}, {})
        finally:
            b.apple_lookup = real
        self.assertEqual(sorted(asked), ["Gone", "Old", "One", "Stranger", "Two"])
        self.assertEqual(asked[-1], "Stranger")
        self.assertEqual([(p["album"], p["year"]) for p in plays["nts"]],
                         [("LP", "2026"), ("", "2026"), ("Classic", "1999"), ("Classic", "1999"), ("", None), ("Saved", "2025"), ("", None)])
        self.assertIsNone(resolved[b.key("D", "Gone")])
        # With the album known, NTS backs the album KEXP is playing, and the 1999 record is left out.
        self.assertEqual([(t["album"], sorted(t["stations"])) for t in b.radio_trends(plays, "album", self.NOW)], [("LP", ["kexp", "nts"])])
        self.assertNotIn("B", [t["artist"] for t in b.radio_trends(plays, "song", self.NOW)])

    def test_one_nts_play_of_something_new_counts_a_little(self):
        plays = {"nts": [self.play("A", "New"), self.play("B", "Who Knows", year=None), self.play("C", "Old", year="1999")],
                 "kexp": [self.play("D", "Once")]}                                    # one play elsewhere still isn't enough
        trends = b.radio_trends(plays, "song", self.NOW)
        self.assertEqual([t["artist"] for t in trends], ["A"])
        one = b.radio_mentions(trends)[0]["weight"]
        two = b.radio_mentions(b.radio_trends({"nts": [self.play("A", "New", broadcast=x) for x in "xy"]}, "song", self.NOW))[0]["weight"]
        self.assertEqual(one, 0.6)
        self.assertGreater(two, one)

    def test_one_nts_play_joins_what_a_blog_is_behind(self):
        plays = {"nts": [self.play("A", "Track", "Reviewed", year=None), self.play("B", "Track", "Ignored")],
                 "kexp": [self.play("C", "Track", "Reviewed Too")]}
        backed = {b.key("A", "Reviewed"), b.key("C", "Reviewed Too")}
        self.assertEqual([t["album"] for t in b.radio_trends(plays, "album", self.NOW, backed)], ["Reviewed"])
        self.assertEqual(b.radio_trends(plays, "album", self.NOW), [])

    def test_artists_blogs_write_about_are_looked_up_first(self):
        asked = []

        def lookup(artist, title, cache, kind, loose=False):
            asked.append(artist)

        real, real_budget, b.apple_lookup, b.RADIO_LOOKUPS = b.apple_lookup, b.RADIO_LOOKUPS, lookup, 3
        try:
            plays = {"kexp": [self.play("Elsewhere", "Other", "LP")],
                     "nts": [self.play("Nobody", "One", year=None), self.play("Twice", "Two", year=None, broadcast="x"),
                             self.play("Twice", "Three", year=None, broadcast="y"), self.play("Elsewhere", "Four", year=None),
                             self.play("Reviewed", "Five", year=None), self.play("Saved", "Six", year=None)]}
            cache = {"song:" + b.key("Saved", "Six"): {}}                              # already answered: costs nothing
            b.fill_in_from_apple(plays, {}, cache, wanted={b.key("Reviewed", "")})
        finally:
            b.apple_lookup, b.RADIO_LOOKUPS = real, real_budget
        self.assertEqual(asked[:3], ["Reviewed", "Elsewhere", "Twice"])
        self.assertEqual(sorted(asked[3:]), ["Saved"])

    def test_tracklist_rows_without_a_name_are_skipped(self):
        self.assertTrue(b.nameless("Unknown Artist", "Castles in the Sky"))
        self.assertTrue(b.nameless("(?)", "Bonus Track"))
        self.assertTrue(b.nameless("Someone", "ID"))
        self.assertFalse(b.nameless("حميد الشاعرى", "تعرف ليه"))

    def test_a_tracklists_credit_matches_apples(self):
        hits = [{"artistName": "Someone Else", "trackName": "Mimoun Marhaba", "trackId": 1},
                {"artistName": "Floating Points & Maalem Mahmoud Ghania", "trackName": "Mimoun Marhaba", "trackId": 2,
                 "collectionName": "Promises", "releaseDate": "2026-03-01", "primaryGenreName": "Electronic"}]
        asked = []

        def fetch(url, **_):
            asked.append(url)
            return b.json.dumps({"results": hits if "Ghania" not in url else []})     # only the first name alone finds it

        real, real_sleep, b.fetch, b.time.sleep = b.fetch, b.time.sleep, fetch, lambda _: None
        try:
            credit = "Floating Points, Maalem Mahmoud Ghania"
            self.assertIsNone(b.apple_lookup(credit, "Mimoun Marhaba", {}, "song"))
            cache = {}
            hit = b.apple_lookup(credit, "Mimoun Marhaba", cache, "song", loose=True)
            self.assertEqual((hit["id"], hit["collection"], hit["year"]), ("2", "Promises", "2026"))
            self.assertEqual(len(asked), 3)
            self.assertIsNone(b.apple_lookup("Floating Points", "Another Song", cache, "song", loose=True))
            self.assertTrue(cache["song:" + b.key("Floating Points", "Another Song")]["loose"])
        finally:
            b.fetch, b.time.sleep = real, real_sleep

    def test_nts_only_picks_have_their_own_limit_and_need_a_year(self):
        def entry(source, score, year="2026"):
            return {"score": score, "mentions": [{"source": source, "year": year}]}
        entries = [entry("kexp", 5)] * 3 + [entry("nts", 1)] * 4 + [entry("nts", 2, year=None)]
        chosen = b.shortlist(entries, limit=2, extra_per_genre=0, radio_only_limit=2, picks_only_limit=3)   # they come on top of the list
        self.assertEqual([e["mentions"][0]["source"] for e in chosen], ["kexp"] * 2 + ["nts"] * 3)
        self.assertTrue(all(e["mentions"][0]["year"] for e in chosen))

    def test_stations_are_asked_only_every_few_hours(self):
        saved = {"at": "2026-10-03T22:00:00Z", "rules": b.RADIO_RULES, "status": {"kexp": "5 found"}, "album": [], "song": [{"artist": "A"}]}
        status = {}
        self.assertIs(b.read_radio(self.NOW, {"radio": saved}, status, {}), saved)
        self.assertEqual(status, {"kexp": "5 found"})


class ScoreTests(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)

    def mention(self, source, weight=1.5):
        return {"source": source, "weight": weight, "date": self.NOW}

    def test_a_different_kind_of_source_agreeing_counts_for_more_than_another_blog(self):
        two_blogs = b.score([self.mention("clash"), self.mention("treble")], None, self.NOW)
        blog_and_radio = b.score([self.mention("clash"), self.mention("kexp")], None, self.NOW)
        all_three = b.score([self.mention("clash"), self.mention("kexp")], {"momentum": 3.0, "listens": 500}, self.NOW)
        self.assertEqual(two_blogs, 3.6)
        self.assertGreater(blog_and_radio, two_blogs)
        self.assertGreater(all_three, blog_and_radio)

    def test_one_outlets_feeds_count_once(self):
        self.assertEqual(b.score([self.mention("pitchfork", 1.0), self.mention("pitchfork-bnm", 3.0)], None, self.NOW), 3.0)

    def test_radio_only_entries_are_capped(self):
        entries = [{"score": i, "mentions": [{"source": "kexp"}], "listeners": None} for i in range(30)]
        self.assertEqual(len(b.shortlist(entries, limit=500, extra_per_genre=0, radio_only_limit=20)), 20)


    def test_one_outlet_has_only_so_many_to_itself(self):
        def entry(i, *sources):
            return {"score": 100 - i, "mentions": [{"source": source} for source in sources], "listeners": None}
        alone = [entry(i, "musicweb") for i in range(20)]
        shared = [entry(30 + i, "musicweb", "guardian-classical") for i in range(5)]
        picks = [entry(40 + i, "bandcamp-daily") for i in range(12)]   # an outlet's hand-picked best isn't capped
        chosen = b.shortlist(alone + shared + picks, limit=500, extra_per_genre=0, solo_limit=8)
        self.assertEqual(len(chosen), 8 + 5 + 12)
        self.assertEqual([e["score"] for e in chosen[:8]], [100 - i for i in range(8)])
        self.assertEqual(b.lone_outlet(entry(0, "pitchfork", "pitchfork-bnm")), None)
        self.assertEqual(b.lone_outlet(entry(0, "treble", "treble")), "treble")

    def test_an_acclaimed_record_fades_more_slowly_and_stays_longer(self):
        def mention(source, days):
            return {"source": source, "weight": 1.0, "date": self.NOW - dt.timedelta(days=days)}
        self.assertEqual(b.score([mention("clash", 20)], None, self.NOW), 0.25)           # two half-lives of ten days
        self.assertEqual(b.score([mention("pitchfork-bnm", 20)], None, self.NOW), 0.5)    # one of twenty
        three = [mention("clash", 40), mention("treble", 40), mention("nme", 40)]
        self.assertTrue(b.acclaimed(three))
        self.assertEqual(len(b.still_counting(three, self.NOW)), 3)
        self.assertEqual(b.still_counting(three[:2], self.NOW), [])                       # two outlets: a month, as before
        self.assertEqual(len(b.still_counting([mention("stereogum", 55)], self.NOW)), 1)
        self.assertEqual(b.still_counting([mention("stereogum", 61)], self.NOW), [])
        self.assertFalse(b.acclaimed([mention("clash", 1), mention("kexp", 1), mention("kcrw", 1)]))  # stations aren't outlets


class AppleTests(unittest.TestCase):
    def test_an_album_search_misses_is_found_on_its_artists_page(self):
        answers = {
            "entity=album&limit=5&term=Geese+Getting+Killed": {"results": []},
            "entity=musicArtist": {"results": [{"artistName": "Geese", "artistId": 1}, {"artistName": "The Geese", "artistId": 2}]},
            "lookup?id=1": {"results": [{"wrapperType": "artist"},
                                        {"wrapperType": "collection", "collectionName": "Getting Killed - Single", "collectionId": 9, "artistName": "Geese"},
                                        {"wrapperType": "collection", "collectionName": "Getting Killed", "collectionId": 7, "artistName": "Geese",
                                         "releaseDate": "2025-09-26T07:00:00Z", "primaryGenreName": "Alternative", "artworkUrl100": "https://x/100x100bb.jpg"}]},
        }
        asked = []

        def fetch(url, timeout=30, accept=None):
            asked.append(url)
            return json.dumps(next(v for k, v in answers.items() if k in url)).encode()
        real = b.fetch, b.time.sleep
        b.fetch, b.time.sleep = fetch, (lambda s: None)
        try:
            found = b.apple_lookup("Geese", "Getting Killed", {})
        finally:
            b.fetch, b.time.sleep = real
        self.assertEqual((found["id"], found["album"], found["year"], found["artwork"]), ("7", "Getting Killed", "2025", "https://x/600x600bb.jpg"))
        self.assertFalse(any("lookup?id=2" in url for url in asked))   # "The Geese" isn't Geese


class HelperTests(unittest.TestCase):
    def test_key_ignores_case_editions_and_the(self):
        self.assertEqual(b.key("The Strokes", "Is This It (Deluxe)"), b.key("strokes", "IS THIS IT"))
        self.assertNotEqual(b.key("Cleo Sol", "Gold"), b.key("Alabaster DePlume", "Gold"))
        self.assertEqual(b.key("Blue Öyster Cult", "(Don't Fear) The Reaper"), "dontfearthereaper|blueoystercult")
        self.assertEqual(b.key("A", "(Intro) Song (Remix)"), b.key("A", "(Intro) Song"))
        self.assertNotEqual(b.key("حميد الشاعرى", "تعرف ليه"), b.key("Lu Hu", "看 (Look)"))
        self.assertNotIn("|", (b.key("Πύργος Αθηνών", "Αλφα Κενταυρου")[0], b.key("Πύργος Αθηνών", "Αλφα Κενταυρου")[-1]))

    def test_dates(self):
        self.assertEqual(b.parse_date("Thu, 01 Oct 2026 04:03:00 +0000"), dt.datetime(2026, 10, 1, 4, 3, tzinfo=dt.timezone.utc))
        self.assertEqual(b.parse_date("2026-10-01T04:03:00Z"), dt.datetime(2026, 10, 1, 4, 3, tzinfo=dt.timezone.utc))
        self.assertEqual(b.parse_date("Wed, 23 Sept 2026 08:01:00 +0100"), dt.datetime(2026, 9, 23, 7, 1, tzinfo=dt.timezone.utc))
        self.assertIsNone(b.parse_date("not a date"))

    def test_feed_items_carry_their_categories(self):
        rss = b"<rss><channel><item><title>A</title><category>Album Reviews</category><category>Reviews</category></item></channel></rss>"
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title><category term="indieheads" label="r/indieheads"/></entry></feed>'
        self.assertEqual(next(b.feed_items(rss))["categories"], ["Album Reviews", "Reviews"])
        self.assertEqual(next(b.feed_items(atom))["categories"], ["indieheads"])

    def test_feed_items_reads_web_page_entities(self):
        rss = b"<rss><channel><item><title>Orville Peck &mdash; Cowboy Heart&nbsp;</title></item></channel></rss>"
        self.assertEqual(next(b.feed_items(rss))["title"], "Orville Peck — Cowboy Heart")

    def test_feed_items_reads_rss_and_atom(self):
        rss = b"<rss><channel><item><title>A &amp; B</title><link>https://x/y</link><pubDate>Thu, 01 Oct 2026 04:03:00 +0000</pubDate></item></channel></rss>"
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title><link href="https://x/z"/><published>2026-10-01T04:03:00+00:00</published></entry></feed>'
        self.assertEqual([i["title"] for i in b.feed_items(rss)], ["A & B"])
        self.assertEqual([i["link"] for i in b.feed_items(atom)], ["https://x/z"])


if __name__ == "__main__":
    unittest.main()
