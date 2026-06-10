"""Clean SR Legacy: remove junk (long names, brand names with ALL CAPS)."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent.parent / "data" / "app.db"
db = sqlite3.connect(str(DB))

before = db.execute("SELECT COUNT(1) FROM food_items").fetchone()[0]
deleted = db.execute(
    "DELETE FROM food_items WHERE source='sr_legacy_food' AND (LENGTH(name) >= 60 OR name GLOB '*[A-Z][A-Z][A-Z][A-Z]*')"
).rowcount
after = db.execute("SELECT COUNT(1) FROM food_items").fetchone()[0]
db.commit()

print(f"Before: {before}")
print(f"Deleted: {deleted} junk items")
print(f"After: {after} clean foods")
print(f"  Foundation Foods: {db.execute(\"SELECT COUNT(1) FROM food_items WHERE source='foundation_food'\").fetchone()[0]}")
print(f"  SR Legacy: {db.execute(\"SELECT COUNT(1) FROM food_items WHERE source='sr_legacy_food'\").fetchone()[0]}")

# Verify apple search is now better
print("\n=== apple ===")
for row in db.execute("SELECT name, LENGTH(name) FROM food_items WHERE name_lower LIKE '%apple%' ORDER BY LENGTH(name) LIMIT 5"):
    print(f"  [{row[1]} chars] {row[0][:80]}")

print("\n=== egg ===")
for row in db.execute("SELECT name, LENGTH(name) FROM food_items WHERE name_lower LIKE '%egg%' ORDER BY LENGTH(name) LIMIT 5"):
    print(f"  [{row[1]} chars] {row[0][:80]}")

print("\n=== rice ===")
for row in db.execute("SELECT name, LENGTH(name) FROM food_items WHERE name_lower LIKE '%rice%' ORDER BY LENGTH(name) LIMIT 5"):
    print(f"  [{row[1]} chars] {row[0][:80]}")

db.close()
