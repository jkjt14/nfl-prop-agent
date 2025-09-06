
"""
Optional: push results to Firebase/Firestore.
Requires:
  pip install firebase-admin
  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/serviceAccount.json

Usage:
  python firestore_push.py --csv edges_bestbook.csv --collection nfl_props/2025_week1/edges
"""
import argparse, os, pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--collection", required=True, help="Firestore collection path, e.g., nfl_props/2025_week1/edges")
    args = ap.parse_args()

    if not firebase_admin._apps:
        cred = credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
        firebase_admin.initialize_app(cred)
    db = firestore.client()

    df = pd.read_csv(args.csv)
    batch = db.batch()
    coll = db.collection(args.collection)
    for i, row in df.iterrows():
        doc = coll.document()
        batch.set(doc, row.to_dict())
    batch.commit()
    print(f"Pushed {len(df)} rows to {args.collection}")

if __name__ == "__main__":
    main()
