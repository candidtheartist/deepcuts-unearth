import unittest

from build_labels import choose, credit, logo_file, logo_url, plain, records, wikidata_id


class LabelNames(unittest.TestCase):
    def test_records_on_the_end_doesnt_matter(self):
        self.assertEqual(plain("Sub Pop Records"), "sub pop")
        self.assertEqual(plain("Domino Recording Co."), "domino")
        self.assertEqual(plain("[PIAS]"), "pias")
        # A name that is only such a word keeps it.
        self.assertEqual(plain("Music"), "music")

    def test_the_only_label_with_the_name_is_chosen(self):
        candidates = [
            {"id": "a", "name": "Epitaph", "type": "Original Production"},
            {"id": "b", "name": "Epitaph Records", "type": "Holding"},
            {"id": "c", "name": "Epitaph Europe", "type": "Imprint"},
        ]
        self.assertEqual(choose("Epitaph Records", candidates), "a")

    def test_a_holding_company_counts_when_nothing_else_has_the_name(self):
        self.assertEqual(choose("Thrill Jockey Records", [{"id": "h", "name": "Thrill Jockey Records", "type": "Holding"}]), "h")

    def test_same_named_labels_are_told_apart_by_whos_putting_records_out(self):
        candidates = [
            {"id": "old", "name": "Young", "type": "Original Production"},
            {"id": "live", "name": "Young", "type": "Original Production"},
            {"id": "other", "name": "Young Money", "type": "Original Production"},
        ]
        self.assertIsNone(choose("Young", candidates))
        self.assertEqual(choose("Young", candidates, {"old": 0, "live": 33}), "live")
        # Too close to call is left for a person.
        self.assertIsNone(choose("Young", candidates, {"old": 20, "live": 33}))

    def test_nothing_with_the_name_is_nothing(self):
        self.assertIsNone(choose("BPitch Berlin", [{"id": "x", "name": "BPitch Control", "type": None}]))


class Records(unittest.TestCase):
    def release(self, group, title, date, barcode=None, artists=(("Sunn O)))", ""),)):
        return {"id": title + str(date), "title": title, "date": date, "barcode": barcode,
                "release-group": {"id": group, "title": title},
                "artist-credit": [{"name": name, "joinphrase": join} for name, join in artists]}

    def test_editions_of_one_record_become_one_entry_with_every_barcode(self):
        found = records([
            self.release("g1", "Glory Black", "2026-03-06", "098787170566"),
            self.release("g1", "Glory Black", "2026-03-01", "098787965360"),
            self.release("g1", "Glory Black", "2026-03-06"),
            self.release("g2", "Older", "2025-11-02"),
        ])
        self.assertEqual([r["title"] for r in found], ["Glory Black", "Older"])
        self.assertEqual(found[0]["date"], "2026-03-01")
        self.assertEqual(found[0]["upcs"], ["098787170566", "098787965360"])
        self.assertEqual(found[1]["upcs"], [])

    def test_artists_are_joined_the_way_theyre_credited(self):
        release = self.release("g", "What of Our Nature", "2025-11-21", artists=(("Haley Heynderickx", " & "), ("Max García Conover", "")))
        self.assertEqual(credit(release), "Haley Heynderickx & Max García Conover")

    def test_a_record_with_no_artist_is_left_out(self):
        self.assertEqual(records([self.release("g", "Untitled", "2026-01-01", artists=())]), [])


class Build(unittest.TestCase):
    def test_a_label_with_no_apple_music_page_keeps_its_other_spellings(self):
        import datetime
        import build_labels
        asked = build_labels.releases_of
        build_labels.releases_of = lambda mbid, today: []
        try:
            out, failed = build_labels.build([
                {"name": "Island Records", "mbid": "a", "aka": ["Universal-Island Records Ltd."]},
                {"name": "Sub Pop Records", "mbid": "b", "appleMusicID": "1544001416"},
                {"name": "Not Resolved"},
            ], {}, datetime.date(2026, 10, 6))
        finally:
            build_labels.releases_of = asked
        self.assertEqual(failed, 0)
        self.assertEqual(out[0], {"name": "Island Records", "mbid": "a", "releases": [], "aka": ["Universal-Island Records Ltd."]})
        self.assertNotIn("aka", out[1])
        self.assertEqual(len(out), 2)


class LongLists(unittest.TestCase):
    def test_a_busy_label_is_asked_for_in_halves(self):
        import datetime
        import build_labels
        # 600 releases, one a day: more than MusicBrainz will page through in one go.
        start = datetime.date(2025, 1, 1)
        days = [(start + datetime.timedelta(days=n)).isoformat() for n in range(600)]
        asked = []

        def ask(path, query, limit, offset):
            since, until = query.split("date:[")[1].rstrip("]").split(" TO ")
            asked.append((since, until, offset))
            inside = [day for day in days if since <= day <= until]
            self.assertLess(offset, build_labels.MOST_PER_ASK)
            return {"count": len(inside), "releases": [{"id": day} for day in inside[offset:offset + limit]]}

        real = build_labels.ask
        build_labels.ask = ask
        try:
            found = build_labels.between("x", start, start + datetime.timedelta(days=599))
        finally:
            build_labels.ask = real
        self.assertEqual(sorted(r["id"] for r in found), days)



class Logos(unittest.TestCase):
    def test_the_wikidata_id_comes_from_musicbrainzs_links(self):
        label = {"relations": [
            {"type": "official site", "url": {"resource": "https://warp.net/"}},
            {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q1312934"}},
        ]}
        self.assertEqual(wikidata_id(label), "Q1312934")
        self.assertEqual(wikidata_id({"relations": []}), "")
        self.assertEqual(wikidata_id({}), "")

    def test_the_logo_is_wikidatas_logo_image(self):
        entity = {"claims": {"P154": [{"mainsnak": {"datavalue": {"value": "Warp Records logo.svg"}}}]}}
        self.assertEqual(logo_file(entity), "Warp Records logo.svg")
        self.assertIsNone(logo_file({"claims": {"P18": []}}))
        # A claim that says "no value" has nothing to show.
        self.assertIsNone(logo_file({"claims": {"P154": [{"mainsnak": {"snaktype": "novalue"}}]}}))

    def test_the_address_asks_commons_for_a_picture_of_the_file(self):
        self.assertEqual(logo_url("Logo for 300 Entertainment.svg"),
                         "https://commons.wikimedia.org/wiki/Special:FilePath/Logo_for_300_Entertainment.svg?width=330")
        self.assertIn("Def%20Jam".replace("%20", "_"), logo_url("Def Jam & Co.png"))
        self.assertIn("%26", logo_url("Def Jam & Co.png"))


if __name__ == "__main__":
    unittest.main()
