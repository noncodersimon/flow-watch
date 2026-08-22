"""Parser tests for the SEC Form 4 adapter, run against saved fixtures.

Same reasoning as tests/test_informed_money.py: the fetch is a thin
wrapper and is not tested, the parsing is real logic and is. Fixtures are
real Apple filings saved on 2026-08-22.

The case that matters most is the Rule 10b5-1 split. A plan sale was
adopted months before it executed, so counting it as insider selling says
something about the calendar rather than about the price. Apple's recent
filings contain both kinds side by side, which is what these fixtures
capture.
"""

import contextlib
import io
import json
import os
import unittest

from context import REPO_ROOT  # noqa: F401 - puts adapters/ on sys.path

import sec_form4 as sf

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8", errors="replace") as f:
        return f.read()


class FilingListTest(unittest.TestCase):
    def setUp(self):
        self.rows = sf.parse_filing_list(fixture("sec_atom_aapl.xml"))

    def test_filings_are_found(self):
        self.assertGreater(len(self.rows), 3)

    def test_rows_are_well_formed(self):
        for row in self.rows:
            self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(row["dir_url"].startswith("https://www.sec.gov/Archives/"))
            self.assertTrue(row["dir_url"].endswith("/"),
                            "dir_url must be a directory, not the index page")

    def test_index_page_is_stripped_to_its_directory(self):
        for row in self.rows:
            self.assertNotIn("-index.htm", row["dir_url"])


class FilingDirectoryTest(unittest.TestCase):
    def test_finds_the_ownership_xml(self):
        idx = json.loads(fixture("sec_index.json"))
        self.assertEqual(sf.pick_form4_name(idx), "form4.xml")

    def test_skips_edgar_rendered_copies(self):
        """Some agents ship an xslF345X03/ rendering alongside the real XML."""
        idx = {"directory": {"item": [
            {"name": "xslF345X03/wf-form4_123.xml"},
            {"name": "wf-form4_123.xml"},
        ]}}
        self.assertEqual(sf.pick_form4_name(idx), "wf-form4_123.xml")

    def test_missing_xml_is_none(self):
        self.assertIsNone(sf.pick_form4_name({"directory": {"item": [
            {"name": "0001140361-26-033928.txt"}]}}))
        self.assertIsNone(sf.pick_form4_name({}))


class ClassifyTest(unittest.TestCase):
    def test_open_market_codes(self):
        self.assertEqual(sf.classify_form4("P", False), "pdmr_buy")
        self.assertEqual(sf.classify_form4("S", False), "pdmr_sell")

    def test_plan_trades_are_not_a_view_on_price(self):
        self.assertEqual(sf.classify_form4("P", True), "pdmr_scheduled")
        self.assertEqual(sf.classify_form4("S", True), "pdmr_scheduled")

    def test_scheme_codes(self):
        for code in ["A", "M", "F", "G", "C"]:
            self.assertEqual(sf.classify_form4(code, False), "pdmr_award", code)

    def test_a_plan_does_not_change_scheme_activity(self):
        """An M or F under a plan is still scheme activity, not a plan trade."""
        self.assertEqual(sf.classify_form4("M", True), "pdmr_award")
        self.assertEqual(sf.classify_form4("F", True), "pdmr_award")

    def test_unknown_codes_are_ignored(self):
        self.assertIsNone(sf.classify_form4("X", False))
        self.assertIsNone(sf.classify_form4("", False))


class DiscretionaryFilingTest(unittest.TestCase):
    """Levinson's 50,000 share sale - not under a plan, so it is real signal."""

    def setUp(self):
        self.txs = sf.parse_form4(fixture("sec_form4_discretionary.xml"), ref_prefix="ACC")

    def test_transactions_are_found(self):
        self.assertTrue(self.txs)

    def test_issuer_and_symbol(self):
        for tx in self.txs:
            self.assertEqual(tx["symbol"], "AAPL")
            self.assertIn("Apple", tx["issuer"])

    def test_not_under_a_plan(self):
        for tx in self.txs:
            self.assertFalse(tx["under_plan"])

    def test_the_sale_counts_as_selling(self):
        sells = [t for t in self.txs if t["kind"] == "pdmr_sell"]
        self.assertTrue(sells, "a non-plan S code must count as a sell")
        self.assertEqual(sells[0]["code"], "S")
        self.assertGreater(sells[0]["shares"], 0)
        self.assertGreater(sells[0]["price_usd"], 0)

    def test_detail_carries_the_size(self):
        sells = [t for t in self.txs if t["kind"] == "pdmr_sell"]
        self.assertIn("shares", sells[0]["detail"])
        self.assertIn("$", sells[0]["detail"])

    def test_refs_are_unique(self):
        refs = [t["ref"] for t in self.txs]
        self.assertEqual(len(refs), len(set(refs)))
        for r in refs:
            self.assertTrue(r.startswith("ACC#"))


class PlanFilingTest(unittest.TestCase):
    """A Rule 10b5-1 filing - the direction is real but it is not a signal."""

    def setUp(self):
        self.txs = sf.parse_form4(fixture("sec_form4_plan.xml"), ref_prefix="ACC")

    def test_flagged_as_a_plan(self):
        for tx in self.txs:
            self.assertTrue(tx["under_plan"])

    def test_no_sale_is_counted_as_selling(self):
        kinds = {t["kind"] for t in self.txs}
        self.assertNotIn("pdmr_sell", kinds)
        self.assertNotIn("pdmr_buy", kinds)

    def test_sales_become_scheduled(self):
        for tx in self.txs:
            if tx["code"] == "S":
                self.assertEqual(tx["kind"], "pdmr_scheduled")

    def test_detail_says_so(self):
        for tx in self.txs:
            self.assertIn("10b5-1", tx["detail"])


class MalformedInputTest(unittest.TestCase):
    def test_bad_xml_is_empty_not_an_exception(self):
        # the adapter reports the parse failure on stderr; swallow it so a
        # passing test run stays quiet
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(sf.parse_form4("<not xml"), [])
        self.assertIn("parse failed", err.getvalue())

    def test_document_with_no_transactions(self):
        xml = ("<ownershipDocument><issuer><issuerName>X</issuerName>"
               "<issuerTradingSymbol>X</issuerTradingSymbol></issuer>"
               "</ownershipDocument>")
        self.assertEqual(sf.parse_form4(xml), [])


class UserAgentTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("SEC_CONTACT", None)

    def tearDown(self):
        os.environ.pop("SEC_CONTACT", None)
        if self._saved is not None:
            os.environ["SEC_CONTACT"] = self._saved

    def test_env_override_wins(self):
        os.environ["SEC_CONTACT"] = "flow-watch someone@example.com"
        self.assertEqual(sf.user_agent(), "flow-watch someone@example.com")

    def test_a_bare_email_gets_the_tool_name(self):
        """The obvious thing to put in the variable is just an address."""
        os.environ["SEC_CONTACT"] = "someone@example.com"
        self.assertEqual(sf.user_agent(), "flow-watch someone@example.com")

    def test_whitespace_only_falls_back(self):
        os.environ["SEC_CONTACT"] = "   "
        self.assertEqual(sf.user_agent(), sf.DEFAULT_CONTACT)

    def test_value_with_no_address_is_warned_about(self):
        """SEC answers 403 to a User-Agent with no contact in it."""
        os.environ["SEC_CONTACT"] = "flow-watch"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(sf.user_agent(), "flow-watch")
        self.assertIn("403", err.getvalue())

    def test_default_is_a_contactable_shape(self):
        """SEC rejects a User-Agent that does not name a contact address."""
        self.assertIn("@", sf.user_agent())


class EventKeyTest(unittest.TestCase):
    """Two sales by one insider on one day differ only by ref, because a
    dollar trade carries a null value_gbp."""

    def test_ref_separates_otherwise_identical_events(self):
        import common
        store = {"updated": None, "events": {}}
        a = {"date": "2026-06-15", "kind": "pdmr_sell", "who": "B Borders",
             "value_gbp": None, "ref": "ACC#0"}
        b = dict(a, ref="ACC#1")
        common.merge_events(store, "AAPL", [a, b])
        self.assertEqual(len(store["events"]["AAPL"]), 2,
                         "same-day sales must not collapse into one")

    def test_the_same_ref_still_dedupes(self):
        import common
        store = {"updated": None, "events": {}}
        a = {"date": "2026-06-15", "kind": "pdmr_sell", "who": "B",
             "value_gbp": None, "ref": "ACC#0"}
        common.merge_events(store, "AAPL", [a])
        common.merge_events(store, "AAPL", [dict(a)])
        self.assertEqual(len(store["events"]["AAPL"]), 1)


if __name__ == "__main__":
    unittest.main()
