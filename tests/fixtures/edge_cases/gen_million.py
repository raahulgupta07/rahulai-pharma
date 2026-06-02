"""Generate a true 1M-row CSV for scale tests. Output gitignored.
Run once before nightly CI hits million_row test.
"""
import csv, random, datetime, os, sys

OUT = os.path.join(os.path.dirname(__file__), "million_row.csv")
ROWS = int(os.environ.get("MILLION_ROWS", "1000000"))

def main():
    random.seed(42)
    start = datetime.date(2024, 1, 1)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "dt", "sku", "qty", "price", "region"])
        for i in range(ROWS):
            w.writerow([
                i,
                (start + datetime.timedelta(days=random.randint(0, 500))).isoformat(),
                f"SKU-{random.randint(1, 5000)}",
                random.randint(1, 500),
                round(random.uniform(1, 9999), 2),
                random.choice(["North", "South", "East", "West"]),
            ])
    print(f"wrote {ROWS} rows to {OUT}", file=sys.stderr)

if __name__ == "__main__":
    main()
