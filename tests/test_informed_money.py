"""Parser tests for the informed-money adapter, run against saved fixtures.

CLAUDE.md deliberately does not unit test adapter fetch functions - they are
thin wrappers over live APIs. It does say that when a fetch grows real logic,
that logic should move into a pure function and be tested there. The RNS
parsers are exactly that case: real logic, no network. The fixtures in
tests/fixtures are real Investegate pages saved on 2026-08-22, with scripts
and styles stripped.

The most important test here is the issuer guard. Investegate indexes an
announcement under every company it names, so a bank's page carries TR-1s
where the bank is the holder and some unrelated company is the issuer.
Attributing those to the bank would invent insider activity out of nothing.
"""

import os
import unittest

from context import REPO_ROOT  # noqa: F401 - puts adapters/ on sys.path

import informed_money as im

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8", errors="replace") as f:
        return f.read()


class ListingParserTest(unittest.TestCase):
    def setUp(self):
        self.rows = im.parse_listing(fixture("listing_hsba.html"))

    def test_rows_are_found(self):
        self.assertGreater(len(self.rows), 20)

    def test_rows_are_well_formed(self):
        for row in self.rows:
            self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(row["headline"])
            self.assertTrue(row["url"].startswith("https://"))
            self.assertIn("/announcement/", row["url"])

    def test_urls_are_unique(self):
        urls = [r["url"] for r in self.rows]
        self.assertEqual(len(urls), len(set(urls)))

    def test_headline_wins_over_source_badge(self):
        """A row links twice - the source badge ("RNS") and the headline."""
        self.assertNotIn("RNS", [r["headline"] for r in self.rows])

    def test_finds_the_typed_announcements(self):
        typed = [r for r in self.rows if im.headline_kind(r["headline"])]
        self.assertGreater(len(typed), 5)


class HeadlineKindTest(unittest.TestCase):
    def test_pdmr_headlines(self):
        for h in ["Director/PDMR Shareholding", "Director/PDMR shareholding",
                  "Transactions of Directors"]:
            self.assertEqual(im.headline_kind(h), "pdmr", h)

    def test_tr1_headlines(self):
        for h in ["Holding(s) in Company",
                  "TR-1: notification of major holdings",
                  "Standard form for notification of major holdings"]:
            self.assertEqual(im.headline_kind(h), "tr1", h)

    def test_ignored_headlines(self):
        for h in ["Transaction in Own Shares", "Notice of AGM",
                  "Issue of Equity", "Total Voting Rights"]:
            self.assertIsNone(im.headline_kind(h), h)


class DateParserTest(unittest.TestCase):
    def test_the_spellings_rns_actually_uses(self):
        cases = {
            "21 August 2026": "2026-08-21",
            "21 Aug 2026": "2026-08-21",
            "19-Aug-2026": "2026-08-19",
            "1 September 2026": "2026-09-01",
            "2026-08-21": "2026-08-21",
            "21st August 2026": "2026-08-21",
        }
        for raw, want in cases.items():
            self.assertEqual(im.parse_date(raw), want, raw)

    def test_rubbish_is_none(self):
        for raw in ["", None, "not a date", "Immediately"]:
            self.assertIsNone(im.parse_date(raw))


class ClassifyNatureTest(unittest.TestCase):
    def test_open_market_deals(self):
        self.assertEqual(im.classify_nature("PURCHASE OF ORDINARY SHARES"), "pdmr_buy")
        self.assertEqual(im.classify_nature("Acquisition of shares"), "pdmr_buy")
        self.assertEqual(im.classify_nature("SALE OF ORDINARY SHARES"), "pdmr_sell")
        self.assertEqual(im.classify_nature("Disposal of shares"), "pdmr_sell")

    def test_scheme_activity_is_not_a_view_on_price(self):
        """The whole point of the pdmr_award kind - a vest-and-sell is
        calendar-driven, so it must not land on the sell side."""
        for nature in [
            "EXERCISE OF NIL COST OPTIONS UNDER THE LONG TERM INCENTIVE PLAN",
            "SALE OF SHARES ON EXERCISE OF NIL COST OPTIONS",
            "Vesting of restricted share awards",
            "Grant of options under the Sharesave scheme",
            "Acquisition of shares under the Share Incentive Plan",
        ]:
            self.assertEqual(im.classify_nature(nature), "pdmr_award", nature)

    def test_unknown_is_none(self):
        self.assertIsNone(im.classify_nature("Something else entirely"))
        self.assertIsNone(im.classify_nature(""))


class IssuerGuardTest(unittest.TestCase):
    def test_real_issuer_names_match(self):
        cases = [
            ("HSBC", "HSBC HOLDINGS PLC"),
            ("Barclays", "BARCLAYS PLC"),
            ("Shell", "SHELL PLC"),
            ("BP p.l.c.", "BP P.L.C."),
            ("AstraZeneca", "ASTRAZENECA PLC"),
            ("London Stock Exchange", "LONDON STOCK EXCHANGE GROUP PLC"),
            ("Vodafone", "VODAFONE GROUP PLC"),
            ("Rio Tinto", "RIO TINTO PLC"),
        ]
        for expected, announced in cases:
            self.assertTrue(im.issuer_matches(expected, announced),
                            f"{expected} should match {announced}")

    def test_other_issuers_are_rejected(self):
        cases = [
            ("Barclays", "CENTRAL ASIA METALS PLC"),
            ("HSBC", "INFORMA PLC"),
            ("BP p.l.c.", "BP MARSH & PARTNERS PLC"),
            ("Shell", "SHELL MIDSTREAM PARTNERS LP"),
            ("Tesco", "TESLA INC"),
        ]
        for expected, announced in cases:
            self.assertFalse(im.issuer_matches(expected, announced),
                             f"{expected} must not match {announced}")

    def test_missing_names_are_rejected(self):
        self.assertFalse(im.issuer_matches("HSBC", None))
        self.assertFalse(im.issuer_matches(None, "HSBC HOLDINGS PLC"))
        self.assertFalse(im.issuer_matches("", ""))


class PdmrParserTest(unittest.TestCase):
    def setUp(self):
        self.txs = im.parse_pdmr(im.html_to_text(fixture("pdmr_mitie.html")))

    def test_every_transaction_block_is_found(self):
        # the fixture is an exercise + sale against two separate schemes
        self.assertEqual(len(self.txs), 4)

    def test_person_and_issuer(self):
        for tx in self.txs:
            self.assertEqual(tx["who"], "Peter Dickinson")
            self.assertEqual(tx["issuer"], "MITIE GROUP PLC")
            self.assertIn("CHIEF LEGAL OFFICER", tx["role"])
            self.assertEqual(tx["date"], "2026-08-21")

    def test_all_four_are_scheme_activity(self):
        self.assertEqual({t["kind"] for t in self.txs}, {"pdmr_award"})

    def test_pence_prices_become_pounds(self):
        sales = [t for t in self.txs if t["price_gbp"]]
        self.assertTrue(sales)
        for tx in sales:
            # 208.5907p a share
            self.assertAlmostEqual(tx["price_gbp"], 2.085907, places=6)

    def test_value_is_price_times_volume(self):
        """The stated total corroborates it here, to within pennies."""
        values = sorted(t["value_gbp"] for t in self.txs)
        self.assertAlmostEqual(values[-1], 4544603.13, delta=5.0)
        self.assertAlmostEqual(values[-2], 1791164.17, delta=5.0)

    def test_all_sterling(self):
        for tx in self.txs:
            self.assertEqual(tx["currency"], "GBP")

    def test_volumes(self):
        volumes = {t["volume"] for t in self.txs}
        self.assertEqual(volumes, {2178718.0, 858698.0})

    def test_this_fixture_is_not_ours(self):
        """Mitie is not in meta.json - the guard must drop the lot."""
        for tx in self.txs:
            self.assertFalse(im.issuer_matches("HSBC", tx["issuer"]))


class CurrencyTest(unittest.TestCase):
    """Unilever quotes the same award in GBP, EUR and USD tranches, so a
    price cell's currency decides whether it can contribute to value_gbp."""

    def test_currency_detection(self):
        self.assertEqual(im._currency_of("\u00a3 5.594192"), "GBP")
        self.assertEqual(im._currency_of("208.5907p"), "GBP")
        self.assertEqual(im._currency_of("\u20ac55.82"), "EUR")
        self.assertEqual(im._currency_of("$56.055"), "USD")
        self.assertIsNone(im._currency_of("45.39243"))

    def test_pence_become_pounds_but_pounds_do_not(self):
        self.assertEqual(im._price_amount("208.5907p"), (2.085907, "GBP"))
        self.assertEqual(im._price_amount("\u00a3 5.594192"), (5.594192, "GBP"))

    def test_unmarked_prices_are_taken_as_sterling(self):
        """Every instrument this adapter covers is LSE listed."""
        self.assertEqual(im._price_amount("45.39243"), (45.39243, "GBP"))

    def test_nil_cost_is_zero_sterling(self):
        self.assertEqual(im._price_amount("NIL"), (0.0, "GBP"))

    def test_foreign_prices_keep_their_currency(self):
        self.assertEqual(im._price_amount("\u20ac55.82"), (55.82, "EUR"))
        self.assertEqual(im._price_amount("$56.055"), (56.055, "USD"))

    def test_foreign_transaction_has_no_sterling_value(self):
        block = ("NOTIFICATION AND PUBLIC DISCLOSURE OF TRANSACTIONS\n"
                 "Details of the person discharging managerial responsibilities\n"
                 "Name\nA Director\n"
                 "Details of the issuer\nName\nUNILEVER PLC\n"
                 "Nature of the transaction\nSale of PLC EUR shares\n"
                 "Price(s) and volume(s)\nPrice(s)\nVolume(s)\n\u20ac55.82\n1,400\n"
                 "e)\nDate of the transaction\n6 March 2026\n")
        tx = im.parse_pdmr(block)
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx[0]["currency"], "EUR")
        self.assertIsNone(tx[0]["value_gbp"],
                          "a euro price must not be recorded as pounds")

    def test_mixed_tranches_count_only_the_sterling_one(self):
        block = ("NOTIFICATION AND PUBLIC DISCLOSURE OF TRANSACTIONS\n"
                 "Details of the person discharging managerial responsibilities\n"
                 "Name\nA Director\n"
                 "Details of the issuer\nName\nUNILEVER PLC\n"
                 "Nature of the transaction\nPurchase of shares\n"
                 "Price(s) and volume(s)\nPrice(s)\nVolume(s)\n"
                 "\u00a310.00\n100\n\u20ac12.00\n50\n"
                 "e)\nDate of the transaction\n6 March 2026\n")
        tx = im.parse_pdmr(block)
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx[0]["currency"], "GBP")
        self.assertEqual(tx[0]["value_gbp"], 1000.0)


class Tr1ParserTest(unittest.TestCase):
    def test_hsbc_page_tr1_is_about_informa(self):
        rec = im.parse_tr1(im.html_to_text(fixture("tr1_hsba.html")))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["issuer"], "INFORMA PLC")
        self.assertEqual(rec["who"], "HSBC Holdings plc")
        self.assertEqual(rec["kind"], "tr1_up")
        self.assertAlmostEqual(rec["pct_now"], 6.123, places=3)
        self.assertAlmostEqual(rec["pct_prev"], 6.054, places=3)
        self.assertEqual(rec["date"], "2026-08-19")
        # the guard - this must never be filed under HSBC
        self.assertFalse(im.issuer_matches("HSBC", rec["issuer"]))

    def test_barclays_page_tr1_is_about_central_asia_metals(self):
        rec = im.parse_tr1(im.html_to_text(fixture("tr1_barc.html")))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["issuer"], "CENTRAL ASIA METALS PLC")
        self.assertEqual(rec["who"], "Barclays PLC")
        self.assertEqual(rec["kind"], "tr1_up")
        self.assertFalse(im.issuer_matches("Barclays", rec["issuer"]))

    def test_direction_follows_the_percentages(self):
        self.assertEqual(im.parse_tr1("Issuer Name\nACME PLC\n"
                                      "Resulting situation on the date on which\n"
                                      "1.0\n2.0\n5.000\n1000\n"
                                      "Position of previous notification\n"
                                      "1.0\n2.0\n7.000\n")["kind"], "tr1_down")

    def test_rubbish_is_none(self):
        self.assertIsNone(im.parse_tr1("nothing useful here"))


class MergeEventsTest(unittest.TestCase):
    def test_duplicates_are_not_re_added(self):
        store = {"updated": None, "events": {}}
        ev = {"date": "2026-08-21", "kind": "pdmr_buy", "who": "A Smith",
              "value_gbp": 1000.0}
        im.merge_events(store, "X.LON", [ev])
        im.merge_events(store, "X.LON", [dict(ev)])
        self.assertEqual(len(store["events"]["X.LON"]), 1)

    def test_new_events_accumulate_in_date_order(self):
        store = {"updated": None, "events": {}}
        im.merge_events(store, "X.LON", [
            {"date": "2026-08-21", "kind": "pdmr_buy", "who": "B", "value_gbp": 2.0},
            {"date": "2026-08-19", "kind": "pdmr_sell", "who": "A", "value_gbp": 1.0},
        ])
        dates = [e["date"] for e in store["events"]["X.LON"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), 2)


class RnsTickerTest(unittest.TestCase):
    def test_defaults_to_the_id_stem(self):
        self.assertEqual(im.rns_ticker({"id": "HSBA.LON", "name": "HSBC"}), "HSBA")

    def test_explicit_override_wins(self):
        """BP is 'BP.' on Investegate - the dot is part of the LSE ticker."""
        self.assertEqual(
            im.rns_ticker({"id": "BP.LON", "name": "BP", "rns": "BP."}), "BP.")


if __name__ == "__main__":
    unittest.main()
