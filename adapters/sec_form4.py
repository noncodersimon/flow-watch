"""US insider dealings from SEC Form 4 filings.

The US counterpart to informed_money.py. It writes into the same event
store, data/events.json, using the same kinds, so the dashboard treats a
US insider exactly like a UK one.

Why this is a separate adapter rather than more of informed_money.py: the
sources have nothing in common. Investegate is HTML built for humans and
needs several hundred lines of text wrangling; Form 4 is structured XML
with an explicit transaction code, so classification is a lookup rather
than a guess. Only the event store is shared, and that now lives in
common.py.

Route (there is no single index of a company's Form 4s that we can reach):
  1. browse-edgar Atom feed per CIK  -> recent Form 4 filings
  2. the filing directory's index.json -> the ownership XML's filename,
     which varies by filing agent (form4.xml, wf-form4_1234.xml, ...)
  3. the XML itself -> transactions

Classification is by SEC transaction code, which is far more reliable than
the UK free-text "nature of the transaction":
  P  open-market purchase          -> pdmr_buy
  S  open-market sale              -> pdmr_sell
  A  grant, award or other receipt -> pdmr_award
  M  exercise of a derivative      -> pdmr_award
  F  shares withheld to pay tax    -> pdmr_award
  G  gift                          -> pdmr_award
  ... other codes are non-market or rare and are ignored.

Rule 10b5-1 trades are the US analogue of a UK vest-and-sell: the insider
adopts the plan months ahead, so the execution is calendar-driven and not
a view on the price. A P or S made under a plan therefore becomes
pdmr_scheduled and is excluded from the buy/sell totals. In one Apple
sample this is the difference between a director's discretionary 50,000
share sale and another insider's routine vest-and-sell on the same page.

Only the non-derivative table is read. An option exercise already appears
there as the shares arrive (code M), so reading the derivative table too
would double-count the same event.

Values are left in dollars: value_gbp is null and currency is "USD", per
the decision not to bolt an FX source onto a signal count. The size is
kept in the detail string so nothing is lost.

SEC's fair-access policy requires a User-Agent naming a contact address,
and it rejects unroutable ones (users.noreply.github.com is refused). Set
SEC_CONTACT to an address you actually monitor.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from common import load_meta, load_events, merge_events, save_events

SEC_BASE = "https://www.sec.gov"
BROWSE = SEC_BASE + "/cgi-bin/browse-edgar"
DEFAULT_CONTACT = "flow-watch admin@flow-watch.example"
MAX_FILINGS = 20      # newest Form 4s to open per company per run
PAUSE_SECONDS = 0.3   # SEC asks for no more than 10 requests a second

OPEN_MARKET = {"P": "pdmr_buy", "S": "pdmr_sell"}
SCHEME_CODES = {"A", "M", "F", "G", "C", "D", "I", "U", "W", "Z"}
CODE_LABELS = {
    "P": "Open-market purchase", "S": "Open-market sale",
    "A": "Grant or award", "M": "Exercise of derivative",
    "F": "Shares withheld for tax", "G": "Gift",
    "C": "Conversion of derivative", "D": "Disposition to the issuer",
    "I": "Discretionary transaction", "U": "Tender of shares",
    "W": "Acquired or disposed by will", "Z": "Deposit or withdrawal from trust",
}


# --------------------------------------------------------------------------
# fetch layer - network only, no parsing
# --------------------------------------------------------------------------

def user_agent():
    """The User-Agent SEC sees. Set SEC_CONTACT to a real contact address.

    SEC refuses any User-Agent with no contact address in it (and refuses
    unroutable ones such as users.noreply.github.com). A bare email is
    accepted, which is the obvious thing to put in the variable, so that
    case is normalised to "flow-watch <address>" - their guidance asks for
    the tool to be named alongside the address.
    """
    raw = (os.environ.get("SEC_CONTACT") or "").strip()
    if not raw:
        return DEFAULT_CONTACT
    if "@" not in raw:
        print(f"sec_form4: SEC_CONTACT ({raw!r}) names no contact address - "
              "SEC will reject this with 403", file=sys.stderr)
        return raw
    return raw if " " in raw else f"flow-watch {raw}"


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def filings_url(cik, count=MAX_FILINGS):
    """browse-edgar Atom feed of a company's Form 4 filings.

    owner=only is load-bearing. EDGAR treats "type" as a prefix, so
    type=4 with owner=include also matches 424B2 prospectuses - and
    JPMorgan files so many of those that not one real Form 4 appeared in
    its feed. Apple only worked by luck. owner=only restricts the feed to
    ownership filings, which is what Form 4 is.
    """
    params = {
        "action": "getcompany", "CIK": cik, "type": "4",
        "dateb": "", "owner": "only", "count": str(count), "output": "atom",
    }
    return BROWSE + "?" + urllib.parse.urlencode(params)


# --------------------------------------------------------------------------
# parse layer - pure functions, tested against saved fixtures
# --------------------------------------------------------------------------

_ENTRY = re.compile(r"(?s)<entry>(.*?)</entry>")


def _tag(block, name):
    m = re.search(rf"(?s)<{name}>(.*?)</{name}>", block)
    return m.group(1).strip() if m else None


def parse_filing_list(atom_xml):
    """[{date, dir_url}] for the Form 4 filings in a browse-edgar Atom feed."""
    out = []
    for m in _ENTRY.finditer(atom_xml):
        block = m.group(1)
        # exact "4" only: this drops the 4/A amendments, which restate a
        # transaction already filed and would otherwise double-count it
        if (_tag(block, "filing-type") or "").strip() != "4":
            continue
        href = _tag(block, "filing-href")
        if not href:
            continue
        out.append({
            "date": _tag(block, "filing-date"),
            "dir_url": href.rsplit("/", 1)[0] + "/",
        })
    return out


def pick_form4_name(index_json):
    """The ownership XML in a filing directory. Its name varies by agent, so
    take the first .xml that is not one of EDGAR's rendered xsl copies."""
    try:
        items = index_json["directory"]["item"]
    except (KeyError, TypeError):
        return None
    for item in items:
        name = item.get("name", "")
        if name.lower().endswith(".xml") and not name.lower().startswith("xsl"):
            return name
    return None


def _text(node, path):
    if node is None:
        return None
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None


def _number(text):
    if not text:
        return None
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def _is_true(text):
    return str(text).strip().lower() in ("true", "1", "y", "yes")


def classify_form4(code, under_plan):
    """SEC transaction code -> event kind, or None to ignore.

    A plan trade keeps its direction out of the totals: it was scheduled
    months ago, so it says nothing about what the insider thinks today.
    """
    if code in OPEN_MARKET:
        return "pdmr_scheduled" if under_plan else OPEN_MARKET[code]
    if code in SCHEME_CODES:
        return "pdmr_award"
    return None


def parse_form4(xml_text, ref_prefix=""):
    """Parse a Form 4 ownership document into transaction dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  form4 parse failed: {e}", file=sys.stderr)
        return []

    issuer = root.find("issuer")
    issuer_name = _text(issuer, "issuerName")
    symbol = (_text(issuer, "issuerTradingSymbol") or "").strip().upper()

    owner = root.find("reportingOwner")
    who = _text(owner, "reportingOwnerId/rptOwnerName")
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    role = _text(rel, "officerTitle")
    if not role and rel is not None:
        if _is_true(_text(rel, "isDirector")):
            role = "Director"
        elif _is_true(_text(rel, "isTenPercentOwner")):
            role = "10% owner"

    # the document-level flag, with a footnote fallback for filings that only
    # mention the plan in prose
    under_plan = _is_true(_text(root, "aff10b5One"))
    if not under_plan and re.search(r"(?i)rule\s*10b5-?1", xml_text):
        under_plan = True

    out = []
    table = root.find("nonDerivativeTable")
    if table is None:
        return out
    for i, tx in enumerate(table.findall("nonDerivativeTransaction")):
        code = _text(tx, "transactionCoding/transactionCode")
        kind = classify_form4(code, under_plan)
        if not kind:
            continue
        shares = _number(_text(tx, "transactionAmounts/transactionShares/value"))
        price = _number(_text(tx, "transactionAmounts/transactionPricePerShare/value"))
        when = _text(tx, "transactionDate/value")
        label = CODE_LABELS.get(code, f"Code {code}")
        bits = [label]
        if shares:
            bits.append(f"{shares:,.0f} shares")
            if price:
                bits.append(f"at ${price:,.2f}")
        detail = " ".join(bits)
        if under_plan:
            detail += " (Rule 10b5-1 plan)"
        out.append({
            "ref": f"{ref_prefix}#{i}" if ref_prefix else None,
            "issuer": issuer_name,
            "symbol": symbol,
            "who": who,
            "role": role,
            "date": when,
            "kind": kind,
            "code": code,
            "shares": shares,
            "price_usd": price,
            "under_plan": under_plan,
            "detail": detail,
        })
    return out


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def collect_for_instrument(inst, fetcher=fetch):
    """Fetch and parse recent Form 4 activity for one US instrument."""
    cik = str(inst.get("cik") or "").strip()
    if not cik:
        return []
    expected = (inst.get("sec_symbol") or inst["id"]).strip().upper()
    try:
        atom = fetcher(filings_url(cik))
    except Exception as e:  # noqa: BLE001 - per-instrument, never fatal
        print(f"  {inst['id']}: filing list failed: {e}", file=sys.stderr)
        return []

    events, skipped = [], 0
    for row in parse_filing_list(atom)[:MAX_FILINGS]:
        dir_url = row["dir_url"]
        accession = dir_url.rstrip("/").rsplit("/", 1)[-1]
        try:
            index_json = json.loads(fetcher(dir_url + "index.json"))
            name = pick_form4_name(index_json)
            if not name:
                continue
            xml_text = fetcher(dir_url + name)
        except Exception as e:  # noqa: BLE001
            print(f"  {inst['id']}: {dir_url} failed: {e}", file=sys.stderr)
            continue
        time.sleep(PAUSE_SECONDS)

        for tx in parse_form4(xml_text, ref_prefix=accession):
            # The Atom feed is already scoped to this CIK, but the filing
            # names the issuer explicitly - trust that rather than the feed.
            if tx["symbol"] and tx["symbol"] != expected:
                skipped += 1
                continue
            events.append({
                "date": tx["date"] or row["date"],
                "kind": tx["kind"],
                "who": tx["who"],
                "role": tx["role"],
                "value_gbp": None,          # dollar trade, see the module docstring
                "currency": "USD",
                "detail": tx["detail"][:200],
                "url": dir_url,
                "ref": tx["ref"],
            })
    if skipped:
        print(f"  {inst['id']}: {skipped} transaction(s) for other issuers ignored")
    return events


def main():
    meta = load_meta()
    targets = [i for i in meta["instruments"]
               if i["type"] == "equity" and i.get("region") == "US" and i.get("cik")]
    if not targets:
        print("sec_form4: no US equities with a cik in meta.json")
        return

    store = load_events()
    total = 0
    for inst in targets:
        print(f"sec_form4: {inst['id']} (CIK {inst['cik']})")
        events = collect_for_instrument(inst)
        if events:
            merge_events(store, inst["id"], events)
            total += len(events)
            print(f"  {len(events)} event(s)")

    save_events(store)
    print(f"sec_form4: {total} new event(s) across {len(targets)} instruments")


if __name__ == "__main__":
    main()
