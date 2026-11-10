import unittest

import build_feed as feed
import build_lists as b


class PointsTests(unittest.TestCase):
    def test_album_of_the_years_points(self):
        self.assertEqual([b.points(r, 50) for r in (1, 2, 3, 4, 10, 11, 25, 26, 50)], [10, 8, 6, 5, 5, 3, 3, 1, 1])
        # A list in no particular order: by how long it is.
        self.assertEqual([b.points(None, n) for n in (10, 11, 25, 26)], [5, 3, 3, 1])


class EntryTests(unittest.TestCase):
    def test_the_ways_publications_write_an_entry(self):
        self.assertEqual(b.entry("12. Geese – Getting Killed"), (12, "Geese", "Getting Killed"))
        self.assertEqual(b.entry("Perfume Genius: Glory"), (None, "Perfume Genius", "Glory"))
        self.assertEqual(b.entry("Bon Iver, ‘Sable, Fable’"), (None, "Bon Iver", "Sable, Fable"))
        self.assertEqual(b.entry("#3 Rosalía — Lux (Columbia)"), (3, "Rosalía", "Lux"))
        self.assertEqual(b.entry("Dawn Richard: Creole Culture [Merge]"), (None, "Dawn Richard", "Creole Culture"))
        self.assertEqual(b.entry("Bruno Mars, The Romantic"), (None, "Bruno Mars", "The Romantic"))
        self.assertEqual(b.entry("‘Westside Trapped’ – heavensouls"), (None, "heavensouls", "Westside Trapped"))

    def test_labels_and_prose_are_not_entries(self):
        for text in ("Produced By: DJ Toomp", "Listen/Buy: Bandcamp", "Featuring: Quavo", "A year of great records"):
            self.assertIsNone(b.entry(text), text)


class PageTests(unittest.TestCase):
    PITCHFORK = """<nav><h2>Menu: Reviews</h2></nav>
        <div role="heading" aria-level="3">3.</div><h2>Bassvictim: <em>Forever</em></h2><p>…</p>
        <div role="heading" aria-level="3">2.</div><h2>Dijon: <em>Baby</em></h2><p>…</p>
        <div role="heading" aria-level="3">1.</div><h2>Los Thuthanaka: <em>Los Thuthanaka</em></h2>
        <strong>Listen/Buy:</strong> <strong>Listen/Buy:</strong>"""

    def test_a_rank_just_before_each_heading(self):
        self.assertEqual(b.list_entries(self.PITCHFORK),
                         [(3, "Bassvictim", "Forever"), (2, "Dijon", "Baby"), (1, "Los Thuthanaka", "Los Thuthanaka")])

    def test_numbers_that_repeat_are_not_places(self):
        page = "".join(f"<span>7</span><h3>Artist {i} – Album {i}</h3>" for i in range(6))
        self.assertTrue(all(rank is None for rank, _, _ in b.list_entries(page)))

    def test_the_kind_of_element_that_reads_most_like_entries_wins(self):
        page = "<h2>Our favourites</h2>" + "".join(f"<h3>{i}. Artist {i} – Album {i}</h3>" for i in range(1, 8))
        found = b.list_entries(page)
        self.assertEqual(len(found), 7)
        self.assertEqual(found[0], (1, "Artist 1", "Album 1"))


class MergeTests(unittest.TestCase):
    INDEX = [(1, "Geese", "Getting Killed"), (2, "Rosalía", "Lux"), (3, "Blood Orange", "Essex Honey"), (4, "Dijon", "Baby")]

    def test_the_pages_places_stand_when_they_match_the_index(self):
        page = [(1, "Geese", "Getting Killed"), (2, "Rosalía", "Lux"), (3, "Blood Orange", "Essex Honey")]
        self.assertEqual(b.merged(page, self.INDEX), self.INDEX)  # and Dijon, whom the page didn't give, comes from the index

    def test_numbers_on_the_page_that_were_not_places_give_way_to_the_index(self):
        page = [(60, "Geese", "Getting Killed"), (58, "Rosalía", "LUX"), (56, "Blood Orange", "Essex Honey")]
        self.assertEqual([r for r, _, _ in b.merged(page, self.INDEX)], [1, 2, 3, 4])

    def test_a_page_has_to_find_most_of_the_indexs_albums(self):
        self.assertTrue(b.agrees(self.INDEX[:3], self.INDEX))
        self.assertFalse(b.agrees([(1, "Someone", "Else")] * 6, self.INDEX))
        self.assertFalse(b.agrees([(None, f"A{i}", f"B{i}") for i in range(9)], []))   # with nothing to check against, 10 albums at least
        self.assertTrue(b.agrees([(None, f"A{i}", f"B{i}") for i in range(10)], []))


class FindingListsTests(unittest.TestCase):
    def test_which_headlines_are_year_end_album_lists(self):
        for title, year in [("The 50 Best Albums of 2025", 2025), ("Albums of the Year 2025", 2025),
                            ("2025 Albums of the Year", 2025), ("Our Favourite Albums of 2026", 2026),
                            ("The Best Albums of 2026 So Far", None), ("The 25 Best Songs of 2025", None),
                            ("The Best Albums of September 2026", None), ("The 100 Best Albums of All Time", None)]:
            self.assertEqual(b.year_of_list(title), year, title)

    def test_which_headlines_are_mid_year_album_lists(self):
        for title, year in [("The 50 Best Albums of 2026 So Far", 2026), ("Our 50 Favorite Albums of 2026 (So Far)", 2026),
                            ("Mid-Year Report: The Best Albums of 2026", 2026), ("The Best Songs of 2026 So Far", None),
                            ("The 50 Best Albums of 2025", None), ("The Best Albums of June 2026 So Far", None)]:
            self.assertEqual(b.year_of_midyear_list(title), year, title)

    def test_the_hand_kept_mid_year_lists(self):
        lists = b.midyear_lists(2026)
        self.assertGreaterEqual(len(lists), b.SO_FAR_FROM)
        self.assertTrue(all(l["outlet"] and l["url"].startswith("https://") for l in lists))
        self.assertEqual(b.midyear_lists(1999), [])
        for l in lists:
            for e in l.get("entries", []):
                self.assertEqual(len(e), 3, e)
                self.assertTrue(e[1] and e[2], e)

    def test_albums_copied_into_the_file_are_used_without_reading_the_page(self):
        entries = [[None, f"Artist {n}", f"Album {n}"] for n in range(6)]
        real_lists, real_read = b.midyear_lists, b.read_original
        b.midyear_lists = lambda year: [{"outlet": "SPIN", "url": "https://spin.example/so-far", "entries": entries}]
        b.read_original = lambda url, now: self.fail("the page was read")
        try:
            got = b.gather(2026, b.dt.datetime(2026, 11, 10, tzinfo=b.dt.timezone.utc), {}, [10], [], so_far=True)
        finally:
            b.midyear_lists, b.read_original = real_lists, real_read
        self.assertEqual([(g["outlet"], len(g["entries"]), g["from"]) for g in got], [("SPIN", 6, "file")])

    def test_a_feed_source_is_named_by_its_publication(self):
        self.assertEqual([b.publication(n) for n in ("NPR New Music Friday (critics’ picks)", "The Guardian Jazz Album of the Month",
                                                     "Pitchfork Best New Music", "PopMatters Picks", "Rolling Stone")],
                         ["NPR", "The Guardian", "Pitchfork", "PopMatters", "Rolling Stone"])

    def test_one_list_per_publication(self):
        def made(outlet, writer, size):
            return {"outlet": outlet, "writer": writer, "entries": [(None, f"a{i}", f"b{i}") for i in range(size)]}
        chosen = b.one_per_outlet([made("NPR", "Ann Powers", 10), made("NPR Music", None, 50), made("NPR", "Nate Chinen", 10),
                                   made("Uproxx", "Steven Hyden", 40), made("Uproxx", "Ian Cohen", 25),
                                   made("The Guardian", None, 50), made("Guardian", None, 20)])
        self.assertEqual(sorted((c["outlet"], c["writer"], len(c["entries"])) for c in chosen),
                         [("NPR Music", None, 50), ("The Guardian", None, 50), ("Uproxx", "Steven Hyden", 40)])

    def test_the_index_list_page(self):
        page = """<a href="/category/2025-albums">2025 Albums</a><h2>Ann Powers: NPR: Top 10 Albums of 2025</h2>
            <p><a href="/list_publishers/npr-1"><em>NPR</em></a>: <a href="https://www.npr.org/best" class="post_title">Top 10 Albums of 2025</a></p>
            <ol><li value="1"><div class="album-artist"><a href="/artists/geese">Geese</a></div><div class="album-title"><a href="/albums/x">Getting Killed</a></div></li>
            <li value="2"><div class="album-artist"><a>Rosalía</a></div><div class="album-title"><a>Lux</a></div></li></ol>"""
        real = feed.fetch, b.time.sleep
        feed.fetch, b.time.sleep = (lambda url, timeout=30, accept=None: page.encode()), (lambda s: None)
        try:
            got = b.read_index_list("/2025/ann-powers")
        finally:
            feed.fetch, b.time.sleep = real
        self.assertEqual(got, {"outlet": "NPR", "writer": "Ann Powers", "title": "Top 10 Albums of 2025", "url": "https://www.npr.org/best",
                               "ranked": True, "entries": [(1, "Geese", "Getting Killed"), (2, "Rosalía", "Lux")]})

    def read(self, path, page):
        real = feed.fetch, b.time.sleep
        feed.fetch, b.time.sleep = (lambda url, timeout=30, accept=None: page.encode()), (lambda s: None)
        try:
            return b.read_index_list(path)
        finally:
            feed.fetch, b.time.sleep = real

    ENTRIES = """<ol><li value="1"><div class="album-artist">Geese</div><div class="album-title">Getting Killed</div></li></ol>"""

    def test_a_writers_list_as_published_by_a_publication(self):
        page = """<a href="/category/2025-albums">2025 Albums</a><h2>Rob Sheffield: Rolling Stone: Top 20 Albums of 2025</h2>
            <p><a href="/list_authors/rob-sheffield-1">Rob Sheffield</a>: <a href="https://www.rollingstone.com/rob" class="post_title">Top 20 Albums of 2025</a>, as published by <a href="/list_publishers/rolling-stone-3"><em>Rolling Stone</em></a></p>""" + self.ENTRIES
        got = self.read("/2025/rob-sheffield-top-20-albums-of-2025", page)
        self.assertEqual((got["outlet"], got["writer"], got["title"], got["url"]),
                         ("Rolling Stone", "Rob Sheffield", "Top 20 Albums of 2025", "https://www.rollingstone.com/rob"))

    def test_a_writers_list_on_their_own_show(self):
        page = """<a href="/category/2025-albums">x</a><h2>Anthony Fantano: The Needle Drop: Top 50 Albums of 2025</h2>
            <p><a href="/list_authors/anthony-fantano-5">Anthony Fantano</a>: <a href="https://www.youtube.com/watch?v=x" class="post_title">Top 50 Albums of 2025</a>, as posted on his YouTube channel, <a href="/list_programs/the-needle-drop-e"><em>The Needle Drop</em></a></p>""" + self.ENTRIES
        got = self.read("/2025/anthony-fantano-top-50-albums-of-2025", page)
        self.assertEqual((got["outlet"], got["writer"]), ("The Needle Drop", "Anthony Fantano"))

    def test_a_critic_of_no_publication(self):
        page = """<a href="/category/2025-albums">x</a><h2>Robert Christgau: Dean&#39;s List 2025</h2>
            <p><a href="/list_authors/robert-christgau-a">Robert Christgau</a>: <a href="https://robertchristgau.substack.com/p/deans-list-2025" class="post_title">Dean&#39;s List 2025</a></p>""" + self.ENTRIES
        got = self.read("/2025/robert-christgau-dean-s-list-2025", page)
        self.assertEqual((got["outlet"], got["writer"], got["title"]), ("Robert Christgau", None, "Dean's List 2025"))

    def test_books_and_prizes_are_not_album_lists(self):
        books = """<a href="/category/2025-books">2025 Books</a><a href="/category/2025-albums.rss">rss</a><h2>The Center for Fiction: 2025 First Novel Prize</h2>
            <p><a href="/list_publishers/the-center-for-fiction-b"><em>The Center for Fiction</em></a>: <a href="https://centerforfiction.org/x" class="post_title">2025 First Novel Prize</a></p>""" + self.ENTRIES
        self.assertIsNone(self.read("/2025/the-center-for-fiction-2025-first-novel-prize", books))
        prize = """<a href="/category/2025-albums">x</a><h2>The Mercury Prize: 2025 Award</h2>
            <p><a href="/list_programs/the-mercury-prize-0"><em>The Mercury Prize</em></a>: <a href="https://www.mercuryprize.com/" class="post_title">2025 Award</a></p>""" + self.ENTRIES
        self.assertIsNone(self.read("/2025/the-mercury-prize-2025-award", prize))


class LayoutTests(unittest.TestCase):
    """Layouts found on real list pages that the headings alone didn't read."""

    def entries(self, page):
        return [(r, a, b_) for r, a, b_ in b.list_entries(page)]

    def test_artist_in_bold_album_in_italics(self):
        magnet = "".join(f"<strong>{n}) {a}</strong> <em>{t}</em> (Label)<br>" for n, a, t in
                         [(1, "Geese", "Getting Killed"), (2, "Horsegirl", "Phonetics On And On"), (3, "Snocaps", "Snocaps"),
                          (4, "Midlake", "A Bridge To Far"), (5, "Panda Bear", "Sinister Grift")])
        self.assertEqual(self.entries("<p>" + magnet + "</p>")[:2], [(1, "Geese", "Getting Killed"), (2, "Horsegirl", "Phonetics On And On")])
        flood = "".join(f'<p class="x"><strong><a href="/t/x">{a}</a>, <em>{t}</em></strong><br>Words about it.</p>' for a, t in
                        [("Bill Callahan", "My Days of 58"), ("Boards of Canada", "Inferno"), ("Gorillaz", "The Mountain"),
                         ("Jessie Ware", "Superbloom"), ("Neurosis", "An Undying Love")])
        self.assertEqual(self.entries(flood)[1], (None, "Boards of Canada", "Inferno"))

    def test_one_to_a_line_with_a_bar(self):
        gvb = "<p>" + "".join(f"0{n} <b>{a}</b> | <i><a href='x'>{t}</a></i><br>" for n, a, t in
                              [(1, "STAR MOLES", "Highway to Hell"), (2, "JUMP SOURCE", "Fold"), (3, "JANA HORN", "Jana Horn"),
                               (4, "GUN OUTFIT", "Morning Star"), (5, "helllhound", "here in the valley")]) + "</p>"
        self.assertEqual(self.entries(gvb)[0], (1, "STAR MOLES", "Highway to Hell"))

    def test_album_by_artist_and_heading_pairs(self):
        lobf = "".join(f"<h2>{n} {t} by {a}</h2><p>Listen on Bandcamp</p>" for n, t, a in
                       [(5, "Getting Killed", "Geese"), (4, "LUX", "Rosalia"), (3, "Bleeds", "Wednesday"), (2, "Baby", "Dijon"), (1, "ICONOCLASTS", "Anna von Hausswolff")])
        self.assertEqual(self.entries(lobf)[-1], (1, "Anna von Hausswolff", "ICONOCLASTS"))
        vinyl = "".join(f"<h2><strong>{a}</strong></h2><h3><strong>{t}</strong></h3><p>(Label)</p>" for a, t in
                        [("Big Thief", "Double Infinity"), ("billy woods", "GOLLIWOG"), ("Blood Orange", "Essex Honey"), ("CMAT", "Euro Country"), ("Tortoise", "Touch")])
        self.assertEqual(self.entries(vinyl)[0], (None, "Big Thief", "Double Infinity"))

    def test_bylines_and_dates_are_not_entries(self):
        for text in ("Chris Published – June 26, 2026", "Words: FLOOD Staff", "Photo: Ran Liwen", "Join our community – Join our community"):
            self.assertIsNone(b.entry(text), text)
        self.assertEqual(b.entry("Rosalía – Stream Lux"), (None, "Rosalía", "Lux"))


class ReadingTests(unittest.TestCase):
    CHALLENGE = "<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script></html>"
    LIST = "".join(f"<h2>{n}. {a} – {t}</h2>" for n, a, t in
                   [(1, "Geese", "Getting Killed"), (2, "Rosalía", "Lux"), (3, "Dijon", "Baby"), (4, "Wednesday", "Bleeds"), (5, "Clipse", "Let God Sort Em Out")])

    def read(self, answers, url="https://www.example.com/music/best-albums-2026/"):
        asked = []

        def fetch(address, timeout=30, accept=None):
            asked.append(address)
            for part, answer in answers.items():
                if part in address:
                    if isinstance(answer, Exception):
                        raise answer
                    return answer.encode()
            raise feed.FetchError("404", 404)
        real = feed.fetch, b.time.sleep, feed.time.sleep
        feed.fetch, b.time.sleep, feed.time.sleep = fetch, (lambda s: None), (lambda s: None)
        feed.ROBOTS.clear()
        try:
            entries, how = b.read_original(url, None)
            return len(entries or []), how, asked
        finally:
            feed.fetch, b.time.sleep, feed.time.sleep = real

    def test_a_site_with_no_robots_txt_can_be_read(self):
        self.assertEqual(self.read({"/music/": self.LIST})[:2], (5, "page"))     # robots.txt answers 404

    def test_a_site_that_hides_its_robots_txt_is_left_alone(self):
        n, how, asked = self.read({"robots.txt": feed.FetchError("403", 403), "/music/": self.LIST})
        self.assertEqual((n, how), (0, "robots.txt"))
        self.assertEqual(len(asked), 1)

    def test_behind_a_firewall_the_wordpress_api_then_the_archive(self):
        api = b.json.dumps([{"content": {"rendered": self.LIST}}])
        self.assertEqual(self.read({"robots.txt": "", "wp-json": api, "/music/": self.CHALLENGE})[:2], (5, "wordpress"))
        archive = b.json.dumps({"archived_snapshots": {"closest": {"available": True, "timestamp": "20260701000000"}}})
        got = self.read({"robots.txt": "", "archive.org/wayback": archive, "web.archive.org": self.LIST,
                         "/music/": feed.FetchError("403", 403)})
        self.assertEqual(got[:2], (5, "archive"))

    def test_never_round_a_paywall_or_a_disallow(self):
        archive = b.json.dumps({"archived_snapshots": {"closest": {"available": True, "timestamp": "20260701000000"}}})
        answers = {"robots.txt": "", "archive.org/wayback": archive, "web.archive.org": self.LIST, "/culture/": feed.FetchError("403", 403)}
        n, how, asked = self.read(answers, "https://www.economist.com/culture/best-albums")
        self.assertEqual(n, 0)
        self.assertFalse(any("archive.org" in a for a in asked))
        n, how, asked = self.read({**answers, "robots.txt": "User-agent: *\nDisallow: /culture/"}, "https://www.example.com/culture/best")
        self.assertEqual((n, how), (0, "robots.txt"))
        self.assertEqual(self.read({"robots.txt": "", "/music/": feed.FetchError("402", 402)})[:2], (0, "paywall"))


class AggregateTests(unittest.TestCase):
    def test_albums_by_points_then_lists_then_best_place(self):
        lists = [
            {"id": "a", "entries": [(1, "Geese", "Getting Killed"), (2, "Rosalía", "Lux"), (3, "Dijon", "Baby")]},
            {"id": "b", "entries": [(1, "Rosalía", "Lux"), (2, "Geese", "Getting Killed")]},
            {"id": "c", "entries": [(None, "Dijon", "Baby"), (None, "Geese", "Getting Killed"), (None, "Geese", "Getting killed")]},
        ]
        ranked = b.aggregate(lists)
        self.assertEqual([(e["artist"], e["points"], e["lists"], e["top10"], e["firsts"]) for e in ranked],
                         [("Geese", 23, 3, 2, 1), ("Rosalía", 18, 2, 2, 1), ("Dijon", 11, 2, 1, 0)])
        self.assertEqual(ranked[0]["placements"], [{"list": "a", "rank": 1}, {"list": "b", "rank": 2}, {"list": "c"}])

    def test_a_backing_band_in_one_credit_is_the_same_album(self):
        lists = [
            {"id": "a", "entries": [(7, "Johnny Blue Skies & The Dark Clouds", "Mutiny After Midnight"),
                                    (24, "Avalon Emerson & The Charm", "Written into Changes")]},
            {"id": "b", "entries": [(None, "Avalon Emerson and the Charm", "Written Into Changes"),
                                    (None, "Johnny Blue Skies & The Dark Clouds", "Mutiny After Midnight")]},
            {"id": "c", "entries": [(20, "Avalon Emerson", "Written into Changes"), (None, "Avalon Emerson", "Something Else")]},
            {"id": "d", "entries": [(None, "Simon & Garfunkel", "Bookends"), (None, "Simon", "Bookends")]},
        ]
        ranked = {(e["artist"], e["title"]): e["lists"] for e in b.aggregate(lists)}
        self.assertEqual(ranked, {("Johnny Blue Skies & The Dark Clouds", "Mutiny After Midnight"): 2,
                                  ("Avalon Emerson & The Charm", "Written into Changes"): 3,
                                  ("Avalon Emerson", "Something Else"): 1,
                                  ("Simon & Garfunkel", "Bookends"): 1, ("Simon", "Bookends"): 1})


if __name__ == "__main__":
    unittest.main()
