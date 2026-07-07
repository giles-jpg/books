#!/usr/bin/env python3
"""
enrich_pages.py  —  Giles' Library page-count enrichment
========================================================

Fills in missing "pages" values by querying Open Library first, then
Google Books as a fallback. Cautious by design:

  * Never overwrites a page count you already have.
  * Skips audiobooks (page counts are meaningless for them).
  * Matches on title + author; if the author doesn't corroborate, the
    match is recorded as LOW confidence for you to review, NOT written.
  * Writes nothing to your real data until you've reviewed the log and
    re-run with --apply.

USAGE (run in Terminal, in the folder containing giles-library-data.js)
-----------------------------------------------------------------------
  1. Dry run — looks everything up, writes a review file, changes nothing:

         python3 enrich_pages.py

     This produces  enrich_review.csv  (open it in Excel/Numbers) and
     a proposed data file  giles-library-data.enriched.js  that you can
     inspect but need not use yet.

  2. Review enrich_review.csv. Columns you care about:
        confidence = HIGH  -> author matched, safe
        confidence = LOW   -> title matched but author didn't; check these
several
        source     = which API answered

  3. When happy, apply for real:

         python3 enrich_pages.py --apply

     This backs up your current giles-library-data.js to
     giles-library-data.backup-YYYYMMDD-HHMMSS.js and writes the enriched
     counts into a fresh giles-library-data.js.

     By default --apply writes only HIGH-confidence matches. To also write
     the LOW-confidence ones (after you've eyeballed them):

         python3 enrich_pages.py --apply --include-low

No external libraries needed — only Python 3's standard library.
"""

import json, sys, time, re, urllib.parse, urllib.request, csv, shutil, datetime, os

DATA_FILE = "giles-library-data.js"       # input & (on --apply) output
REVIEW_CSV = "enrich_review.csv"
PROPOSED_JS = "giles-library-data.enriched.js"

HEADERS = {"User-Agent": "GilesLibrary/1.0 (personal catalogue enrichment)"}
SLEEP = 0.5          # seconds between API calls — polite, avoids throttling
TIMEOUT = 10         # seconds per request

# ---------- helpers ----------

def load_books(path):
    """Read the `const BOOKS = [...]:` file and return the list."""
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"const\s+BOOKS\s*=\s*(\[.*\])\s*;", txt, re.S)
    if not m:
        sys.exit("Could not find `const BOOKS = [...]` in " + path)
    return json.loads(m.group(1))

def write_books(path, books):
    with open(path, "w", encoding="utf-8") as f:
        f.write("// Giles' Library \u2014 data file. Keep next to giles-library.html\n")
        f.write("const BOOKS = ")
        f.write(json.dumps(books, ensure_ascii=False, indent=1))
        f.write(";\n")

def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s or ""))
    s = s.split(":")[0].lower().replace("\u2019", "'").replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()

def lastname(a):
    a = re.sub(r"\(.*?\)", "", str(a or "")).strip()
    parts = a.split()
    return parts[-1].lower() if parts else ""

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)

# ---------- API lookups ----------

def lookup_openlibrary(title, author):
    """Return (pages, matched_author_str) or (None, None)."""
    q = urllib.parse.urlencode({"title": title, "author": author, "limit": 3})
    try:
        d = fetch_json("https://openlibrary.org/search.json?" + q)
    except Exception:
        return None, None
    for doc in d.get("docs", []):
        pages = doc.get("number_of_pages_median")
        if pages:
            authors = ", ".join(doc.get("author_name", []) or [])
            return int(pages), authors
    return None, None

def lookup_googlebooks(title, author):
    q = 'intitle:"%s" inauthor:"%s"' % (title, author)
    url = "https://www.googleapis.com/books/v1/volumes?q=" + urllib.parse.quote(q) + "&maxResults=3"
    try:
        d = fetch_json(url)
    except Exception:
        return None, None
    for item in d.get("items", []):
        vi = item.get("volumeInfo", {})
        pages = vi.get("pageCount")
        if pages and int(pages) > 0:
            authors = ", ".join(vi.get("authors", []) or [])
            return int(pages), authors
    return None, None

# ---------- main ----------

def main():
    apply = "--apply" in sys.argv
    include_low = "--include-low" in sys.argv

    if not os.path.exists(DATA_FILE):
        sys.exit("Cannot find %s in this folder. cd to where it lives, then re-run." % DATA_FILE)

    books = load_books(DATA_FILE)
    todo = [b for b in books
            if not b.get("pages")
            and b.get("format") != "Audiobook"
            and b.get("title")]

    print("Total books: %d" % len(books))
    print("Missing page counts (excl. audiobooks): %d" % len(todo))
    print("Querying Open Library, then Google Books as fallback...")
    print("(about %d minutes at %.1fs/book)\n" % (round(len(todo) * SLEEP * 1.2 / 60) + 1, SLEEP))

    rows = []          # for the review CSV
    results = {}       # (title,author) -> (pages, confidence)
    for i, b in enumerate(todo, 1):
        t, a = b["title"], b["author"]
        pages, matched_auth = lookup_openlibrary(t, a)
        source = "openlibrary"
        if not pages:
            time.sleep(SLEEP)
            pages, matched_auth = lookup_googlebooks(t, a)
            source = "googlebooks"
        confidence = ""
        if pages:
            confidence = "HIGH" if lastname(a) and lastname(a) in norm(matched_auth) else "LOW"
            results[(t, a)] = (pages, confidence)
        rows.append({
            "title": t, "author": a, "shelf": b.get("shelf", ""),
            "found_pages": pages or "", "matched_author": matched_auth or "",
            "source": source if pages else "none", "confidence": confidence or "no match",
        })
        if i % 25 == 0 or i == len(todo):
            print("  ...%d / %d done" % (i, len(todo)))
        time.sleep(SLEEP)

    # write review CSV
    with open(REVIEW_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "author", "shelf",
                                          "found_pages", "matched_author",
                                          "source", "confidence"])
        w.writeheader()
        w.writerows(rows)

    hi = sum(1 for r in rows if r["confidence"] == "HIGH")
    lo = sum(1 for r in rows if r["confidence"] == "LOW")
    no = sum(1 for r in rows if r["confidence"] == "no match")
    print("\nResults: %d HIGH-confidence, %d LOW-confidence, %d no match." % (hi, lo, no))
    print("Review written to %s" % REVIEW_CSV)

    # decide which to write
    def keep(conf):
        return conf == "HIGH" or (include_low and conf == "LOW")

    enriched = json.loads(json.dumps(books))  # deep copy
    n_written = 0
    for b in enriched:
        key = (b.get("title"), b.get("author"))
        if key in results and not b.get("pages"):
            pages, conf = results[key]
            if keep(conf):
                b["pages"] = pages
                n_written += 1

    if apply:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = "giles-library-data.backup-%s.js" % stamp
        shutil.copy(DATA_FILE, backup)
        write_books(DATA_FILE, enriched)
        print("\nAPPLIED: wrote %d page counts into %s" % (n_written, DATA_FILE))
        print("Backup of your previous file: %s" % backup)
        if not include_low:
            print("(LOW-confidence matches were NOT written. Re-run with "
                  "--apply --include-low to add them after review.)")
    else:
        write_books(PROPOSED_JS, enriched)
        print("\nDRY RUN: no changes made to %s" % DATA_FILE)
        print("Proposed result written to %s (%d counts) for inspection." % (PROPOSED_JS, n_written))
        print("When happy:  python3 enrich_pages.py --apply")

if __name__ == "__main__":
    main()
