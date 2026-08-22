"""Fetch CFTC Commitments of Traders (legacy, futures-only) positioning
via the CFTC public reporting Socrata API. Free, no key required.

Metrics produced:
  cot_net        - non-commercial (speculator) net position, contracts
  cot_percentile - today's net position as a percentile of the full
                   fetched history (5y), i.e. "how stretched is
                   speculative positioning vs its own past"
"""

import json
import sys
import urllib.parse
import urllib.request

from common import load_meta, load_store, merge_series, save_store, percentile_of_last

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
YEARS = 5


def fetch_cot(code):
    params = {
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all",
        "$where": f"cftc_contract_market_code='{code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(YEARS * 53),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        rows = json.load(r)
    points = []
    for row in rows:
        try:
            d = row["report_date_as_yyyy_mm_dd"][:10]
            net = float(row["noncomm_positions_long_all"]) - float(row["noncomm_positions_short_all"])
            points.append([d, net])
        except (KeyError, ValueError):
            continue
    return sorted(points)


def main():
    meta = load_meta()
    commodities = [i for i in meta["instruments"] if i["type"] == "commodity"]

    net_store = load_store("cot_net")
    pct_store = load_store("cot_percentile")

    for inst in commodities:
        code = inst.get("cftc_code")
        if not code:
            continue
        print(f"fetching COT {inst['id']} ({code})")
        try:
            points = fetch_cot(code)
        except Exception as e:  # noqa: BLE001 - keep the run alive per-instrument
            print(f"  failed: {e}", file=sys.stderr)
            continue
        if not points:
            continue
        merge_series(net_store, inst["id"], points)

        # percentile series computed over the merged history
        merged = net_store["series"][inst["id"]]
        values = [p[1] for p in merged]
        pct_points = []
        for i in range(len(merged)):
            pct_points.append([merged[i][0], percentile_of_last(values[: i + 1])])
        pct_store["series"][inst["id"]] = pct_points

    save_store("cot_net", net_store)
    save_store("cot_percentile", pct_store)


if __name__ == "__main__":
    main()
