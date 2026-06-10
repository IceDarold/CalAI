"""Analyze SR Legacy quality — how many foods are clean vs processed/junk."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent.parent / "data" / "app.db"
db = sqlite3.connect(str(DB))

for max_len in [40, 50, 60, 80]:
    cnt = db.execute(
        "SELECT COUNT(1) FROM food_items WHERE source='sr_legacy_food' AND LENGTH(name) < ?",
        (max_len,),
    ).fetchone()[0]
    print(f"SR Legacy, name < {max_len} chars: {cnt}")

brands = db.execute(
    "SELECT COUNT(1) FROM food_items WHERE source='sr_legacy_food' AND name GLOB '*[A-Z][A-Z][A-Z]*'"
).fetchone()[0]
print(f"\nSR Legacy with ALL CAPS (brands): {brands}")

clean = db.execute("""
    SELECT COUNT(1) FROM food_items
    WHERE source='sr_legacy_food'
    AND LENGTH(name) < 60
    AND name NOT GLOB '*[A-Z][A-Z][A-Z][A-Z]*'
""").fetchone()[0]
print(f"Clean SR Legacy (short + no brands): {clean}")
print(f"Plus Foundation Foods (377) = {clean + 377} total")

print("\n=== Sample clean SR Legacy ===")
for row in db.execute(
    "SELECT name FROM food_items WHERE source='sr_legacy_food' AND LENGTH(name) < 60 "
    "AND name NOT GLOB '*[A-Z][A-Z][A-Z][A-Z]*' ORDER BY RANDOM() LIMIT 25"
):
    print(f"  {row[0][:80]}")

db.close()
