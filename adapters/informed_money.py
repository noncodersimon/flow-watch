"""Informed-money events - director dealings (PDMR) and major
shareholding notifications (TR-1), parsed from Investegate RNS.

Store shape is the event store, data/events.json:
  { "updated": "...", "events": { "<instrument id>": [
      { "date": "2026-08-20", "kind": "pdmr_buy", "who": "J Smith",
        "role": "CHIEF FINANCIAL OFFICER", "value_gbp": 250000,
        "currency": "GBP", "detail": "...", "url": "..." }, ... ] } }

value_gbp covers the sterling tranches only and is null for a dealing quoted
in euros or dollars - cross-listed issuers like Unilever quote the same award
in all three. currency records what the form actually said.

Event kinds:
  pdmr_buy    - director bought on the open market (own cash - the real signal)
  pdmr_sell   - director sold on the open market
  pdmr_award  - share-scheme activity: option exercise, vest, nil-cost award,
                and any sale that is settling one. Recorded for completeness
                but deliberately NOT counted as buying or selling, because it
                is calendar-driven rather than a view on the price.
  tr1_up      - institution increased a major holding past a threshold
  tr1_down    - institution decreased one

Two layers, kept separate on purpose (see CLAUDE.md - a web session cannot
always reach the sources, and parsing must be testable offline):
  - fetch_*  : thin network wrappers, no logic
  - parse_*  : pure functions over saved HTML, covered by tests/fixtures

The issuer check matters more than it looks. Investegate indexes an
announcement under every company it names, so HSBC's page carries TR-1s
where HSBC is the *holder* of someone else's shares - a real example being
a notification whose issuer is Informa PLC. Attributing those to HSBC would
silently invent insider activity. Every parsed event is therefore dropped
unless the issuer named in the form matches the instrument.
"""

import html as html_mod
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from common import load_meta, load_events, merge_events, save_events

BASE = "https://www.investegate.co.uk"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
MAX_PER_TICKER = 15      # newest announcements to open per instrument per run
PAUSE_SECONDS = 1.0      # be polite to a free source


# --------------------------------------------------------------------------
# fetch layer - network only, no parsing
# --------------------------------------------------------------------------

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def listing_url(rns_ticker):
    return f"{BASE}/company/{urllib.parse.quote(rns_ticker)}"


# --------------------------------------------------------------------------
# parse layer - pure functions, tested against saved fixtures
# --------------------------------------------------------------------------

_BLOCK_END = re.compile(
    r"(?is)</(td|tr|p|div|li|h[1-6]|table)\s*>|<br\s*/?>"
)
_SCRIPTS = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1\s*>")
_TAGS = re.compile(r"(?s)<[^>]+>")


def html_to_text(raw):
    """Flatten HTML to newline-per-cell text, preserving the label/value
    structure of the RNS forms (they are laid out as tables)."""
    s = _SCRIPTS.sub(" ", raw)
    s = _BLOCK_END.sub("\n", s)
    s = _TAGS.sub(" ", s)
    s = html_mod.unescape(s)
    s = s.replace("\xa0", " ")
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


_MONTHS = "January February March April May June July August September October November December".split()


def parse_date(text):
    """Accept the several date spellings RNS forms use -> ISO, or None."""
    if not text:
        return None
    t = text.strip().strip(".,")
    t = re.sub(r"(?i)\b(\d{1,2})(st|nd|rd|th)\b", r"\1", t)
    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d-%B-%Y",
                "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})", t)
    if m:
        day, mon, year = m.groups()
        for i, name in enumerate(_MONTHS, start=1):
            if name.lower().startswith(mon.lower()[:3]):
                return f"{int(year):04d}-{i:02d}-{int(day):02d}"
    return None


_ANCHOR = re.compile(
    r"(?is)<a\b[^>]*href=\"(?P<url>[^\"]*/announcement/[^\"]+)\"[^>]*>(?P<text>.*?)</a>"
)
_DATE_CELL = re.compile(r"(?i)\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b")


def parse_listing(raw):
    """Extract [{date, headline, url}] from a company announcement page.

    Markup-agnostic on purpose: the page is a Date | Time | Source |
    Announcement table, but the exact nesting is not ours to rely on. For
    each announcement anchor we take the nearest date appearing before it,
    and where a row links twice (the source badge as well as the headline)
    the longer link text wins - that is the headline.
    """
    s = _SCRIPTS.sub(" ", raw)
    best = {}
    order = []
    for m in _ANCHOR.finditer(s):
        url = html_mod.unescape(m.group("url")).strip()
        text = html_mod.unescape(_TAGS.sub(" ", m.group("text")))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        dates = _DATE_CELL.findall(s[:m.start()])
        iso = parse_date(dates[-1]) if dates else None
        if not iso:
            continue
        if url.startswith("/"):
            url = BASE + url
        prev = best.get(url)
        if prev is None:
            order.append(url)
            best[url] = {"date": iso, "headline": text, "url": url}
        elif len(text) > len(prev["headline"]):
            prev["headline"] = text
    return [best[u] for u in order]


_PDMR_HEADLINE = re.compile(
    r"(?i)director[/ ]?(pdmr|persons? discharging)|pdmr shareholding"
    r"|transactions? (of|by) directors"
)
_TR1_HEADLINE = re.compile(
    r"(?i)\btr-?1\b|holding\(s\) in company|holdings in company"
    r"|notification of major holdings|major shareholding"
)


def headline_kind(headline):
    """'pdmr', 'tr1' or None - which form to expect behind this headline."""
    if _PDMR_HEADLINE.search(headline):
        return "pdmr"
    if _TR1_HEADLINE.search(headline):
        return "tr1"
    return None


_SUFFIXES = re.compile(
    r"(?i)\b(p\.?l\.?c|plc|limited|ltd|group|holdings?|incorporated|inc|company|the)\b"
)


def normalise_issuer(name):
    """Reduce an issuer name to a comparable core token string.

    Dots go first so "BP P.L.C." collapses to "BP PLC" and then to "BP",
    rather than to the letter soup "BP P L C".
    """
    if not name:
        return ""
    s = html_mod.unescape(name).upper().replace(".", "")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def issuer_matches(expected, announced):
    """True only when the issuer named in the form IS our instrument.

    Exact match on the normalised name, deliberately. A bank's Investegate
    page is full of forms where the bank is the holder and some unrelated
    company is the issuer, so a loose rule invents insider activity that
    never happened - "BP" must not match "BP Marsh & Partners plc". Where a
    company needs a different spelling, set "issuer" on it in meta.json.
    """
    a, b = normalise_issuer(expected), normalise_issuer(announced)
    return bool(a) and a == b


def _value_after(lines, index, count=1):
    """The next `count` non-label lines at or after index."""
    out = []
    for line in lines[index + 1:]:
        out.append(line)
        if len(out) >= count:
            break
    return out


def _find(lines, pattern, start=0):
    rx = re.compile(pattern, re.I)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            return i
    return -1


_AWARD = re.compile(
    r"(?i)option|award|vest|nil[- ]cost|lti?p\b|share (incentive|save|match)"
    r"|sharesave|scheme|deferred bonus|restricted share|dividend (re)?investment"
    r"|scrip|grant"
)
_BUY = re.compile(r"(?i)\b(purchase|acquisition|acquire[ds]?|bought|subscription|subscribe)")
_SELL = re.compile(r"(?i)\b(sale|sold|dispos)")


def classify_nature(nature):
    """Map the 'Nature of the transaction' free text to an event kind.

    Share-scheme activity wins over buy/sell wording: 'sale of shares on
    exercise of nil cost options' is a scheme settlement, not a director
    forming a view on the price.
    """
    if not nature:
        return None
    if _AWARD.search(nature):
        return "pdmr_award"
    if _BUY.search(nature):
        return "pdmr_buy"
    if _SELL.search(nature):
        return "pdmr_sell"
    return None


def _number(text):
    m = re.search(r"[-+]?[\d,]*\.?\d+", (text or "").replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _money_gbp(text):
    """Parse a money token to pounds. Handles '£4,544,603.13' and '208.5907p'."""
    if not text:
        return None
    t = text.strip()
    if re.search(r"(?i)^nil$|^n/?a$", t):
        return 0.0
    n = _number(t)
    if n is None:
        return None
    if "£" in t or "gbp" in t.lower():
        return n
    if re.search(r"(?i)\d\s*p(ence)?\b", t):
        return n / 100.0
    return n


def _currency_of(token):
    """Currency actually quoted in a price cell, or None if unmarked."""
    t = (token or "").strip()
    if "\u00a3" in t:
        return "GBP"
    if "\u20ac" in t:
        return "EUR"
    if "$" in t:
        return "USD"
    if re.search(r"(?i)\d\s*p(ence)?\b", t):
        return "GBP"
    return None


def _price_amount(token):
    """(amount in major units, currency) for a price cell.

    Unmarked prices are taken as GBP - every instrument this adapter covers is
    LSE listed. Explicit euro and dollar prices are kept as such so they can be
    excluded from a sterling total rather than silently counted as pounds:
    Unilever quotes the same award in GBP, EUR and USD tranches.
    """
    t = (token or "").strip()
    if re.fullmatch(r"(?i)nil|n/?a|-{1,2}", t):
        return 0.0, "GBP"
    cur = _currency_of(t)
    n = _number(t)
    if n is None:
        return None, cur
    if cur == "GBP" and "\u00a3" not in t and re.search(r"(?i)\d\s*p(ence)?\b", t):
        n = n / 100.0          # pence -> pounds
    return n, (cur or "GBP")


def _price_volume_pairs(block, start):
    """All (amount, currency, volume) tranches in a section 4(c) cell."""
    buf = []
    for line in block[start + 1:]:
        stripped = line.strip()
        if re.fullmatch(r"(?i)[a-f]\)", stripped):
            break
        if re.search(r"(?i)Aggregated information|Date of the transaction", stripped):
            break
        if re.fullmatch(r"(?i)(price|volume)\(s\)", stripped):
            continue
        buf.append(stripped)
    pairs = []
    for k in range(0, len(buf) - 1, 2):
        amount, cur = _price_amount(buf[k])
        vol = _number(buf[k + 1])
        if amount is not None and vol is not None:
            pairs.append((amount, cur, vol))
    return pairs


def parse_pdmr(text):
    """Parse a PDMR (MAR Article 19) announcement into transaction dicts.

    The form repeats section 1-4 per transaction, so the text is split on the
    notification header and each block parsed independently.
    """
    lines = text.split("\n")
    header = _find(lines, r"NOTIFICATION AND PUBLIC DISCLOSURE OF TRANSACTIONS")
    blocks = []
    if header == -1:
        blocks = [lines]
    else:
        starts = [i for i, l in enumerate(lines)
                  if re.search(r"(?i)NOTIFICATION AND PUBLIC DISCLOSURE OF TRANSACTIONS", l)]
        for n, s in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            blocks.append(lines[s:end])

    out = []
    for block in blocks:
        i = _find(block, r"Details of the person discharging managerial")
        who = None
        if i != -1:
            j = _find(block, r"^Name$", i)
            if j != -1:
                who = _value_after(block, j)[0] if _value_after(block, j) else None
        role = None
        j = _find(block, r"Position/status")
        if j != -1:
            vals = _value_after(block, j)
            role = vals[0] if vals else None

        issuer = None
        j = _find(block, r"Details of the issuer")
        if j != -1:
            k = _find(block, r"^Name$", j)
            if k != -1:
                vals = _value_after(block, k)
                issuer = vals[0] if vals else None

        j = _find(block, r"Nature of the transaction")
        nature = None
        if j != -1:
            vals = _value_after(block, j)
            nature = vals[0] if vals else None

        j = _find(block, r"Date of the transaction")
        when = None
        if j != -1:
            vals = _value_after(block, j)
            when = parse_date(vals[0]) if vals else None

        # Section 4(c) can hold several tranches, and for a cross-listed issuer
        # they are in different currencies. Only the sterling ones make up a
        # sterling value; the rest are recorded but left out of value_gbp.
        price = volume = total = None
        pairs = []
        j = _find(block, r"Price\(s\) and volume\(s\)")
        if j != -1:
            pairs = _price_volume_pairs(block, j)
        gbp = [(a, v) for a, c, v in pairs if c == "GBP"]
        currency = "GBP" if gbp else (pairs[0][1] if pairs else None)
        if gbp:
            price = gbp[0][0]
            volume = sum(v for _, v in gbp)
        # Section 4(d) is not laid out consistently between issuers. Mitie puts
        # the total value first; BP puts the average price first and the total
        # last. So price x volume decides, and a stated pound figure is used
        # only when it corroborates - taking the first pound sign turned a
        # 1.96m BP sale into 5.59.
        candidate = sum(a * v for a, v in gbp) if gbp else None
        stated = []
        j = _find(block, r"Aggregated information")
        if j != -1:
            for v in _value_after(block, j, count=10):
                if "£" in v:
                    amount = _money_gbp(v)
                    if amount:
                        stated.append(amount)
        if candidate is not None:
            close = [m for m in stated if abs(m - candidate) <= 0.02 * candidate]
            total = min(close, key=lambda m: abs(m - candidate)) if close else candidate
        elif stated:
            # No sterling tranche in 4(c), but 4(d) states a pound total - that
            # is a genuine sterling figure, so the event is sterling after all.
            total = max(stated)
            currency = "GBP"

        kind = classify_nature(nature)
        if not (who and kind):
            continue
        out.append({
            "who": who, "role": role, "issuer": issuer, "nature": nature,
            "date": when, "kind": kind, "price_gbp": price,
            "volume": volume, "currency": currency,
            "value_gbp": None if total is None else round(total, 2),
        })
    return out


def parse_tr1(text):
    """Parse a TR-1 major-holdings form. Returns a dict or None."""
    lines = text.split("\n")
    j = _find(lines, r"Issuer Name")
    issuer = None
    if j != -1:
        vals = _value_after(lines, j)
        issuer = vals[0] if vals else None

    holder = None
    j = _find(lines, r"Details of person subject to the notification")
    if j != -1:
        k = _find(lines, r"^Name$", j)
        if k != -1:
            vals = _value_after(lines, k)
            holder = vals[0] if vals else None

    when = None
    j = _find(lines, r"Date on which the threshold was crossed")
    if j != -1:
        vals = _value_after(lines, j)
        when = parse_date(vals[0]) if vals else None
    if not when:
        j = _find(lines, r"Date on which Issuer notified")
        if j != -1:
            vals = _value_after(lines, j)
            when = parse_date(vals[0]) if vals else None

    # Section 7: resulting totals then previous-notification totals.
    pct_now = pct_prev = rights = None
    j = _find(lines, r"Resulting situation on the date on which")
    if j != -1:
        nums = []
        for line in lines[j + 1:j + 8]:
            n = _number(line)
            if n is not None and re.fullmatch(r"[\d,.]+", line.strip()):
                nums.append(n)
        if len(nums) >= 3:
            pct_now = nums[2]
        if len(nums) >= 4:
            rights = nums[3]
    j = _find(lines, r"Position of previous notification")
    if j != -1:
        nums = []
        for line in lines[j + 1:j + 8]:
            n = _number(line)
            if n is not None and re.fullmatch(r"[\d,.]+", line.strip()):
                nums.append(n)
        if len(nums) >= 3:
            pct_prev = nums[2]

    if not issuer or pct_now is None:
        return None
    if pct_prev is None:
        kind = "tr1_up"
    elif pct_now > pct_prev:
        kind = "tr1_up"
    elif pct_now < pct_prev:
        kind = "tr1_down"
    else:
        return None
    return {
        "issuer": issuer, "who": holder, "date": when, "kind": kind,
        "pct_now": pct_now, "pct_prev": pct_prev, "voting_rights": rights,
    }


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def rns_ticker(inst):
    """Investegate company code for an instrument."""
    return inst.get("rns") or inst["id"].split(".")[0]


def collect_for_instrument(inst, fetcher=fetch):
    """Fetch and parse the recent informed-money events for one instrument."""
    ticker = rns_ticker(inst)
    expected_issuer = inst.get("issuer") or inst["name"]
    try:
        listing_html = fetcher(listing_url(ticker))
    except Exception as e:  # noqa: BLE001 - per-instrument, never fatal
        print(f"  {inst['id']}: listing failed: {e}", file=sys.stderr)
        return []

    rows = [r for r in parse_listing(listing_html) if headline_kind(r["headline"])]
    events, skipped = [], 0
    for row in rows[:MAX_PER_TICKER]:
        kind = headline_kind(row["headline"])
        try:
            text = html_to_text(fetcher(row["url"]))
        except Exception as e:  # noqa: BLE001
            print(f"  {inst['id']}: {row['url']} failed: {e}", file=sys.stderr)
            continue
        time.sleep(PAUSE_SECONDS)

        if kind == "pdmr":
            for tx in parse_pdmr(text):
                if not issuer_matches(expected_issuer, tx["issuer"]):
                    skipped += 1
                    continue
                events.append({
                    "date": tx["date"] or row["date"],
                    "kind": tx["kind"],
                    "who": tx["who"],
                    "role": tx["role"],
                    "value_gbp": tx["value_gbp"],
                    "currency": tx["currency"],
                    "detail": (tx["nature"] or "")[:200],
                    "url": row["url"],
                })
        else:
            rec = parse_tr1(text)
            if not rec:
                continue
            if not issuer_matches(expected_issuer, rec["issuer"]):
                skipped += 1
                continue
            events.append({
                "date": rec["date"] or row["date"],
                "kind": rec["kind"],
                "who": rec["who"],
                "role": None,
                "value_gbp": None,
                "currency": None,
                "detail": f"{rec['pct_prev']}% -> {rec['pct_now']}% of voting rights",
                "url": row["url"],
            })
    if skipped:
        print(f"  {inst['id']}: {skipped} announcement(s) about other issuers ignored")
    return events


def main():
    meta = load_meta()
    targets = [i for i in meta["instruments"]
               if i["type"] == "equity" and i.get("region") == "UK"]

    store = load_events()
    total = 0
    for inst in targets:
        print(f"informed_money: {inst['id']} ({rns_ticker(inst)})")
        events = collect_for_instrument(inst)
        if events:
            merge_events(store, inst["id"], events)
            total += len(events)
            print(f"  {len(events)} event(s)")

    save_events(store)
    print(f"informed_money: {total} new event(s) across {len(targets)} instruments")


if __name__ == "__main__":
    main()
