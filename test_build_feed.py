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
