"""Download real property photos from Pexels (free API key: https://www.pexels.com/api/).

  set PEXELS_API_KEY=...            (Windows)   |   export PEXELS_API_KEY=...   (mac/linux)
  python scripts/fetch_photos.py --out samples/images

Saves portrait-oriented, high-res photos named 00_exterior.jpg ... 05_*.jpg so samples/property.json works unchanged.
"""
import argparse, json, os, sys, urllib.parse, urllib.request
from pathlib import Path

SLOTS = [("00_exterior", "modern apartment building exterior"), ("01_living_room", "modern living room interior"),
         ("02_kitchen", "modern kitchen interior"), ("03_bedroom", "bedroom interior"),
         ("04_balcony", "apartment balcony view"), ("05_clubhouse_pool", "residential swimming pool")]

def get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "listingreel/1.0"})
    return urllib.request.urlopen(req, timeout=30).read()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="samples/images"); a = ap.parse_args()
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        sys.exit("error: PEXELS_API_KEY not set\nhint: free key at https://www.pexels.com/api/")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for stem, q in SLOTS:
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": q, "orientation": "portrait", "size": "large", "per_page": 1})
        try:
            ph = json.loads(get(url, key))["photos"][0]
            (out / f"{stem}.jpg").write_bytes(urllib.request.urlopen(ph["src"]["large2x"], timeout=60).read())
            print(f"ok   {stem}.jpg  (photo by {ph['photographer']} on Pexels)")
        except Exception as e:
            print(f"fail {stem}: {e}")
if __name__ == "__main__":
    main()
