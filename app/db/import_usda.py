"""One-shot USDA FoodData Central import.

Downloads or reads local CSV zips, extracts foods with their key nutrients
(energy, protein, fat, carbs per 100g), and inserts into the food_items table.

Run once: python -m app.db.import_usda
"""

import csv
import logging
import sqlite3
import zipfile
from io import TextIOWrapper
from pathlib import Path

logger = logging.getLogger(__name__)

# Nutrient IDs from USDA
NUTRIENT_ENERGY_IDS = {"1008", "2047", "2048"}  # Energy, Atwater General, Atwater Specific
NUTRIENT_PROTEIN_ID = "1003"
NUTRIENT_FAT_ID = "1004"
NUTRIENT_CARBS_ID = "1005"

# Data types we want
WANTED_DATA_TYPES = {"sr_legacy_food", "foundation_food"}

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Source zip files (download if missing)
ZIP_FILES = [
    ("foundation", "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2026-04-30.zip"),
    ("sr_legacy", "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip"),
]


def ensure_downloaded() -> list[tuple[str, Path]]:
    """Download USDA zip files if not present. Returns list of (label, zip_path)."""
    result = []
    for label, url in ZIP_FILES:
        filename = url.split("/")[-1]
        zip_path = DATA_DIR / filename
        if not zip_path.exists():
            logger.info(f"Downloading {label} from {url}...")
            import httpx
            resp = httpx.get(url, timeout=120, follow_redirects=True)
            resp.raise_for_status()
            zip_path.write_bytes(resp.content)
            logger.info(f"Downloaded {label}: {len(resp.content)} bytes")
        result.append((label, zip_path))
    return result


def read_foods_from_zip(zip_path: Path) -> dict[str, tuple[str, str]]:
    """Read food.csv from zip, return {fdc_id: (description, category_name)}."""
    foods: dict[str, tuple[str, str]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        # Read food.csv
        food_csv = [n for n in zf.namelist() if n.endswith("food.csv") and "FoodData" in n][0]
        with zf.open(food_csv) as f:
            reader = csv.DictReader(TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                data_type = row.get("data_type", "").strip('"')
                if data_type in WANTED_DATA_TYPES:
                    fdc_id = row["fdc_id"].strip('"')
                    desc = row["description"].strip('"')
                    foods[fdc_id] = (desc, data_type)

        # Read food_category.csv if present for category names
        cat_csv_names = [n for n in zf.namelist() if n.endswith("food_category.csv")]
        categories: dict[str, str] = {}
        if cat_csv_names:
            with zf.open(cat_csv_names[0]) as f:
                reader = csv.DictReader(TextIOWrapper(f, encoding="utf-8"))
                for row in reader:
                    cat_id = row.get("id", "").strip('"')
                    cat_name = row.get("description", "").strip('"')
                    categories[cat_id] = cat_name

        # Read food_nutrient.csv
        nutrient_csv = [n for n in zf.namelist() if n.endswith("food_nutrient.csv") and "FoodData" in n][0]
        nutrients: dict[str, dict[str, float]] = {}
        with zf.open(nutrient_csv) as f:
            reader = csv.DictReader(TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                fid = row["fdc_id"].strip('"')
                nid = row["nutrient_id"].strip('"')
                amount = float(row.get("amount", "0").strip('"') or 0)
                if fid not in nutrients:
                    nutrients[fid] = {}
                nutrients[fid][nid] = amount

    # Merge: fdc_id → (description, kcal, protein, fat, carbs, category)
    merged: dict[str, tuple[str, float | None, float | None, float | None, float | None, str]] = {}
    for fdc_id, (desc, dtype) in foods.items():
        nut = nutrients.get(fdc_id, {})
        kcal = None
        for eid in NUTRIENT_ENERGY_IDS:
            if eid in nut and nut[eid] > 0:
                kcal = nut[eid]
                break
        protein = nut.get(NUTRIENT_PROTEIN_ID)
        fat = nut.get(NUTRIENT_FAT_ID)
        carbs = nut.get(NUTRIENT_CARBS_ID)

        # Skip foods with no energy value
        if kcal is None or kcal <= 0:
            continue

        merged[fdc_id] = (desc, kcal, protein, fat, carbs, dtype)

    return merged


def import_to_sqlite(db_path: str, foods: dict[str, tuple[str, float | None, float | None, float | None, float | None, str]]) -> int:
    """Import foods into SQLite food_items table. Returns count of inserted rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fdc_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            name_lower TEXT NOT NULL,
            category TEXT DEFAULT '',
            kcal_per_100g REAL DEFAULT 0,
            protein_per_100g REAL DEFAULT 0,
            fat_per_100g REAL DEFAULT 0,
            carbs_per_100g REAL DEFAULT 0,
            source TEXT DEFAULT 'usda'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_food_items_name_lower ON food_items(name_lower)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_food_items_fdc_id ON food_items(fdc_id)")

    count = 0
    for fdc_id, (name, kcal, protein, fat, carbs, source) in foods.items():
        conn.execute(
            "INSERT OR REPLACE INTO food_items (fdc_id, name, name_lower, kcal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g, source, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')",
            (fdc_id, name, name.lower(), kcal or 0, protein or 0, fat or 0, carbs or 0, source),
        )
        count += 1

    conn.commit()
    conn.close()
    return count


def run_import(db_path: str | None = None) -> int:
    """Run the full import pipeline. Returns total imported count."""
    if db_path is None:
        db_path = str(DATA_DIR / "app.db")

    # Ensure downloads
    zips = ensure_downloaded()

    total = 0
    for label, zip_path in zips:
        logger.info(f"Processing {label} from {zip_path}...")
        foods = read_foods_from_zip(zip_path)
        logger.info(f"Extracted {len(foods)} foods from {label}")
        imported = import_to_sqlite(db_path, foods)
        logger.info(f"Imported {imported} foods from {label}")
        total += imported

    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    count = run_import()
    print(f"Done. Total imported: {count} foods.")
