"""Parse the RBI state-security tables with the field mappings used for the published historical pack."""
from __future__ import annotations

from datetime import datetime
import hashlib
from html.parser import HTMLParser
import math
import re

INDEX = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
TITLE = "state government securities - full auction result"


class _Tables(HTMLParser):
    """Only leaf tables carry auction columns. Layout tables never contribute a row."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.tables = []
        self.links = []
        self.anchor = None
        self.text = []
        self.ignored = None

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.ignored = tag
        if self.ignored:
            return
        if tag == "a":
            self.anchor = [dict(attrs).get("href", ""), []]
        if tag == "table":
            if self.stack:
                self.stack[-1]["nested"] = True
            self.stack.append({"rows": [], "row": None, "cell": None, "nested": False})
        elif self.stack:
            node = self.stack[-1]
            if tag == "tr":
                node["row"] = []
            elif tag in ("td", "th") and node["row"] is not None:
                node["cell"] = []

    def handle_data(self, data):
        if self.ignored:
            return
        self.text.append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored:
                self.ignored = None
            return
        if tag == "a" and self.anchor is not None:
            self.links.append((self.anchor[0], " ".join(" ".join(self.anchor[1]).split())))
            self.anchor = None
        if not self.stack:
            return
        node = self.stack[-1]
        if tag in ("th", "td") and node["cell"] is not None and node["row"] is not None:
            node["row"].append(" ".join(" ".join(node["cell"]).split()))
            node["cell"] = None
        elif tag == "tr" and node["row"] is not None:
            node["rows"].append(node["row"])
            node["row"] = None
        elif tag == "table":
            self.stack.pop()
            if not node["nested"]:
                self.tables.append(node["rows"])


def discover(raw):
    parser = _Tables()
    parser.feed(raw.decode("utf-8-sig"))
    if not any("bs_pressreleasedisplay.aspx?prid=" in (href or "").lower() for href, _ in parser.links):
        raise ValueError("Expected the RBI press release index")
    ids = set()
    for href, title in parser.links:
        match = re.fullmatch(r"(?:/scripts/)?BS_PressReleaseDisplay\.aspx\?prid=(\d{1,8})", href or "", re.I)
        if match and title.casefold().replace("–", "-") == TITLE:
            ids.add(int(match.group(1)))
    return sorted(ids)


LABELS = [  # (section, label regex) -> field
    (None, r"^notified amount", "notified_amount_cr"),
    (None, r"^tenor|^tenure", "tenor_as_printed"),
    (None, r"^underwriting notified amount", "underwriting_notified_cr"),
    (None, r"^amount of underwriting accepted", "underwriting_accepted_cr"),
    (None, r"^devolvement", "devolvement_on_pds_cr"),
    (None, r"^cut-?\s*off price.*/\s*yield", "cutoff_price_or_yield"),
    (None, r"^cut-?\s*off yield", "cutoff_yield_pct"),
    (None, r"^cut-?\s*off price", "cutoff_price_rs"),
    (None, r"^non\s*-?\s*competitive price", "noncomp_price_rs"),
    (None, r"^weighted average yield", "weighted_avg_yield_pct"),
    (None, r"^weighted average price", "weighted_avg_price_rs"),
    (None, r"^total allotment amount", "total_allotment_cr"),
    ("comp_received", r"^\(i\)\s*no", "comp_bids_received_no"),
    ("comp_received", r"^\(ii\)\s*amount", "comp_bids_received_cr"),
    ("comp_accepted", r"^\(i\)\s*no", "comp_bids_accepted_no"),
    ("comp_accepted", r"^\(ii\)\s*amount", "comp_bids_accepted_cr"),
    ("comp_partial", r"^\(i\)\s*percentage", "comp_partial_allotment_pct"),
    ("comp_partial", r"^\(ii\)\s*no", "comp_partial_allotment_no"),
    ("noncomp_received", r"^\(i\)\s*no", "noncomp_bids_received_no"),
    ("noncomp_received", r"^\(ii\)\s*amount", "noncomp_bids_received_cr"),
    ("noncomp_accepted", r"^\(i\)\s*no", "noncomp_bids_accepted_no"),
    ("noncomp_accepted", r"^\(ii\)\s*amount", "noncomp_bids_accepted_cr"),
    ("noncomp_partial", r"^\(i\)\s*percentage|^percentage", "noncomp_partial_allotment_pct"),
    ("noncomp_partial", r"^\(ii\)\s*no", "noncomp_partial_allotment_no"),
]
SECTIONS = [(r"^competitive bids received", "comp_received"), (r"^competitive bids accepted", "comp_accepted"),
            (r"^partial allotment percentage of competitive", "comp_partial"),
            (r"^non\s*-?\s*competitive bids received", "noncomp_received"),
            (r"^non\s*-?\s*competitive bids accepted", "noncomp_accepted"),
            (r"^partial allotment percentage of non", "noncomp_partial")]


STATES = ["ANDHRA PRADESH", "ARUNACHAL PRADESH", "ASSAM", "BIHAR", "CHHATTISGARH", "DELHI", "GOA", "GUJARAT",
          "HARYANA", "HIMACHAL PRADESH", "JAMMU AND KASHMIR", "JHARKHAND", "KARNATAKA", "KERALA", "MADHYA PRADESH",
          "MAHARASHTRA", "MANIPUR", "MEGHALAYA", "MIZORAM", "NAGALAND", "ODISHA", "PUDUCHERRY", "PUNJAB", "RAJASTHAN",
          "SIKKIM", "TAMIL NADU", "TELANGANA", "TRIPURA", "UTTAR PRADESH", "UTTARAKHAND", "WEST BENGAL"]
# Printed variants seen in RBI's releases that are not the full name with spaces removed (reviewed 11 Nov 2026).
STATE_ALIASES = {"ANDHRA": "ANDHRA PRADESH", "HIMACHALPR": "HIMACHAL PRADESH", "JAMMUKASHMIR": "JAMMU AND KASHMIR",
                 "JAMMUANDKASHMIRUT": "JAMMU AND KASHMIR", "JAMMUKASHMIRUT": "JAMMU AND KASHMIR",
                 "NCTOFDELHI": "DELHI", "KERALAM": "KERALA", "ORISSA": "ODISHA", "PONDICHERRY": "PUDUCHERRY"}


def canonical_state(printed):
    if not printed:
        return None
    key = re.sub(r"[^A-Z]", "", printed.upper())
    for state in STATES:
        if re.sub(r"[^A-Z]", "", state) == key:
            return state
    return STATE_ALIASES.get(key)


def num(s: str):
    t = s.replace(",", "").strip()
    try:
        value = float(t)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def is_header(cells: list[str]) -> bool:
    return len(cells) >= 2 and not cells[0].strip() and any(re.search(r"\b(SGS|SDL|UTGS|\d{4})\b", c, re.I) for c in cells[1:])


def _parse(raw):
    document = raw.decode("utf-8-sig")
    tree = _Tables()
    tree.feed(document)
    page_text = " ".join(" ".join(tree.text).split())
    if TITLE not in page_text.casefold().replace("–", "-"):
        raise ValueError("Unexpected RBI release title")
    date_m = re.search(r"Date\s*:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", page_text)
    held_m = re.search(r"held on ([A-Z][a-z]+ \d{1,2}, \d{4})", page_text)
    rows, totals, problems = [], {}, []
    for ti, table in enumerate(tree.tables):
        cols, section = None, None
        for cells in table:
            if not any(cells):
                continue
            if is_header(cells):
                cols = [{"security_as_printed": c, "table_index": ti, "values": {}, "rows_text": {}} for c in cells[1:]]
                section = None
                rows.extend(c for c in cols if c["security_as_printed"].strip().lower() != "total")
                totals_cols = [c for c in cols if c["security_as_printed"].strip().lower() == "total"]
                if totals_cols:
                    totals = totals_cols[0]
                continue
            if cols is None:
                continue
            label = cells[0].strip().lower()
            values = cells[1:]
            if all(not v.strip() for v in values):
                section = next((s for rx, s in SECTIONS if re.search(rx, label)), section)
                continue
            field = next((f for sec, rx, f in LABELS if (sec is None or sec == section) and re.search(rx, label)), None)
            if field is None:
                problems.append(f"table {ti}: unmapped label {cells[0]!r} (section {section})")
                continue
            for col, val in zip(cols, values + [""] * (len(cols) - len(values))):
                col["values"][field] = val
                col["rows_text"][field] = " ".join(cells)
    out = []
    for r in rows:
        v = r["values"]
        rec = {"security_as_printed": r["security_as_printed"], "table_index": r["table_index"]}
        for k, s in v.items():
            rec[k + "_printed"] = s
            if k != "tenor_as_printed":
                rec[k] = num(s)
        # A combined "price / yield" row, or a cell printing "price/yield": split by the printed slash; a single
        # figure under the combined label is a yield when it is below 30 (a price is near 100), never inferred both.
        for k in ("cutoff_price_or_yield", "cutoff_yield_pct", "cutoff_price_rs"):
            s = (v.get(k) or "").strip()
            if "/" in s:
                a, _, b = s.partition("/")
                a, b = num(a), num(b)
                if a is not None and b is not None and a > 30 > b:
                    rec["cutoff_price_rs"], rec["cutoff_yield_pct"] = a, b
            elif k == "cutoff_price_or_yield" and num(s) is not None and num(s) != 0:
                rec["cutoff_yield_pct" if num(s) < 30 else "cutoff_price_rs"] = num(s)
        for k in ("cutoff_yield_pct", "cutoff_price_rs", "weighted_avg_yield_pct"):
            if rec.get(k) == 0:          # RBI prints 0 when a state rejected every bid: no cut-off, not a 0% yield
                rec[k] = None
        rec.pop("cutoff_price_or_yield", None)
        for k in ("comp_partial_allotment_no", "noncomp_partial_allotment_no"):
            m = re.fullmatch(r"\(?\s*(\d+)\s*bids?\s*\)?", (v.get(k) or "").strip(), re.I)
            if m:
                rec[k] = float(m.group(1))
        name = re.sub(r"\((?:UT|U\.T\.)\)", " ", re.sub(r"[*#@$]+", " ", r["security_as_printed"]))
        sm = re.match(r"\s*([A-Za-z &.\-]+?)\s+(?:SGS|SDL|UTGS)?\s*(\d{4})\b", name)
        rec["state_as_printed"] = sm.group(1).strip().upper() if sm else None
        rec["state"] = canonical_state(rec["state_as_printed"])
        rec["maturity_year"] = int(sm.group(2)) if sm else None
        rec.pop("tenor_as_printed_printed", None)
        rec["tenor_as_printed"] = v.get("tenor_as_printed")
        rec["matched_rows"] = r["rows_text"]
        out.append(rec)
    check = {"date_printed": date_m.group(1) if date_m else None, "auction_held_on_printed": held_m.group(1) if held_m else None,
             "securities": len(out), "problems": problems}
    if totals:
        for f in ("notified_amount_cr", "total_allotment_cr"):
            tot = num(totals["values"].get(f, "") or "")
            parts = [x.get(f) for x in out]
            if tot is not None and all(p is not None for p in parts):
                check[f"sum_{f}"] = round(sum(parts), 3)
                check[f"printed_total_{f}"] = tot
                check[f"sum_ok_{f}"] = abs(sum(parts) - tot) < 0.0015
    foot = re.findall(r"(?:^|\s)([*#@$]{1,2}\s?[A-Z][^*#@$]{10,300}?\.)", page_text)
    check["footnotes_printed"] = foot[:10]
    return out, check



def packet(prid, raw, retrieved_utc):
    """Admit a full release only after its printed notified and allotted totals reconcile."""
    parsed, check = _parse(raw)
    required = ("sum_ok_notified_amount_cr", "sum_ok_total_allotment_cr")
    if not parsed or check["problems"] or not all(check.get(key) for key in required):
        raise ValueError("Unresolved auction columns or printed totals")
    if not all(r.get("state") and r.get("maturity_year") for r in parsed):
        raise ValueError("Unresolved state or security")
    if len({r["security_as_printed"] for r in parsed}) != len(parsed):
        raise ValueError("Duplicate security in RBI release")
    if not check["auction_held_on_printed"] or not check["date_printed"]:
        raise ValueError("Missing RBI release date")
    held = datetime.strptime(check["auction_held_on_printed"], "%B %d, %Y").date()
    released = datetime.strptime(check["date_printed"], "%b %d, %Y").date()
    if held > released or released > datetime.fromisoformat(retrieved_utc).date():
        raise ValueError("Inconsistent RBI release dates")
    rows = []
    for r in parsed:
        rows.append([held.isoformat(), prid, r["state"], r["state_as_printed"], r["security_as_printed"].replace("–", "-"),
                     r["maturity_year"], r["tenor_as_printed"], r.get("notified_amount_cr"), r.get("comp_bids_received_cr"),
                     r.get("comp_bids_received_no"), r.get("cutoff_yield_pct"), r.get("cutoff_price_rs"),
                     r.get("weighted_avg_yield_pct"), r.get("total_allotment_cr"), r.get("noncomp_bids_accepted_cr"),
                     r.get("devolvement_on_pds_cr")])
    return {"prid": str(prid), "rows": rows, "release": {
        "release_date": released.isoformat(), "auction_date": held.isoformat(), "url": f"{INDEX}?prid={prid}",
        "retrieved_utc": retrieved_utc, "sha256": hashlib.sha256(raw).hexdigest(), "total_check": "matched",
        "unparsed_labels": 0}}
