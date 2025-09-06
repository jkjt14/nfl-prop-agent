import argparse, csv, os, json
from google.cloud import firestore

def push_csv(path: str, collection: str):
    db = firestore.Client()
    batch = db.batch()
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r):
            doc_ref = db.collection(collection).document()
            batch.set(doc_ref, row)
            if (i+1) % 400 == 0:
                batch.commit(); batch = db.batch()
        batch.commit()
    print(f"Pushed {i+1} rows to {collection}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--collection", required=True, help='e.g. "nfl_props/2025_wk1/edges"')
    args = ap.parse_args()

    # Requires GOOGLE_APPLICATION_CREDENTIALS env var to a service-account JSON
    push_csv(args.csv, args.collection)
