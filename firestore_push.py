"""Utility to push CSV rows into a Google Firestore collection."""

from __future__ import annotations

import argparse
import csv
import logging

from google.cloud import firestore


def push_csv(path: str, collection: str) -> None:
    """Stream rows from ``path`` into ``collection`` using batched writes."""
    db = firestore.Client()
    batch = db.batch()
    pushed = 0
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=1):
                doc_ref = db.collection(collection).document()
                batch.set(doc_ref, row)
                pushed = i
                if i % 400 == 0:
                    batch.commit()
                    batch = db.batch()
            batch.commit()
        logging.info("Pushed %d rows to %s", pushed, collection)
    except Exception as e:  # pragma: no cover - Firestore/network errors
        logging.exception("Failed to push CSV to Firestore: %s", e)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument(
        "--collection", required=True, help='e.g. "nfl_props/2025_wk1/edges"'
    )
    args = ap.parse_args()

    push_csv(args.csv, args.collection)
