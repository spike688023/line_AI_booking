#!/usr/bin/env python3
"""
One-time cleanup script to delete old LangGraph FirestoreSaver checkpoint data.

The FirestoreSaver (from langgraph_checkpoint_firestore) stored data with this
Firestore structure:

    checkpoints (top-level collection)
      ├── <thread_id_doc_1>
      │     ├── writes (subcollection)
      │     │     ├── <write_doc_1>
      │     │     ├── <write_doc_2>
      │     │     └── ...
      │     └── checkpoints (subcollection)
      │           ├── <checkpoint_doc_1>
      │           └── ...
      ├── <thread_id_doc_2>
      │     ├── writes (subcollection)
      │     └── checkpoints (subcollection)
      └── ...

This script:
  1. Iterates over every document in the top-level "checkpoints" collection
  2. For each document, deletes all docs in its "writes" and "checkpoints" subcollections
  3. Deletes the parent document itself
  4. Defaults to DRY-RUN mode (pass --execute to actually delete)

Usage:
    python cleanup_checkpoints.py           # dry-run (shows what would be deleted)
    python cleanup_checkpoints.py --execute # actually delete the data
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOP_LEVEL_COLLECTION = "checkpoints"
SUBCOLLECTIONS = ["writes", "checkpoints"]
BATCH_SIZE = 400  # Firestore batch limit is 500; leave headroom


def get_firestore_client() -> firestore.Client:
    """Initialize the Firestore client the same way the project does."""
    load_dotenv()

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("ERROR: GOOGLE_CLOUD_PROJECT is not set in .env")
        sys.exit(1)

    db_name = os.getenv("FIRESTORE_DATABASE")

    if db_name:
        client = firestore.Client(project=project_id, database=db_name)
    else:
        client = firestore.Client(project=project_id)

    print(f"Connected to Firestore project={project_id}, database={db_name or '(default)'}")
    return client


def delete_documents_in_batches(client: firestore.Client, doc_refs: list, dry_run: bool) -> int:
    """Delete a list of document references in batches. Returns count deleted."""
    if not doc_refs:
        return 0

    if dry_run:
        return len(doc_refs)

    deleted = 0
    for i in range(0, len(doc_refs), BATCH_SIZE):
        batch = client.batch()
        chunk = doc_refs[i : i + BATCH_SIZE]
        for ref in chunk:
            batch.delete(ref)
        batch.commit()
        deleted += len(chunk)

    return deleted


def cleanup(client: firestore.Client, dry_run: bool) -> None:
    """Walk the checkpoint collection tree and delete everything."""
    mode_label = "DRY RUN" if dry_run else "EXECUTING"
    print(f"\n{'='*60}")
    print(f"  Checkpoint Cleanup  [{mode_label}]")
    print(f"{'='*60}\n")

    collection_ref = client.collection(TOP_LEVEL_COLLECTION)
    # Use list_documents() to find phantom parents (docs that only exist as subcollection parents)
    parent_docs = list(collection_ref.list_documents())

    if not parent_docs:
        print(f"No documents found in '{TOP_LEVEL_COLLECTION}' collection. Nothing to do.")
        return

    print(f"Found {len(parent_docs)} parent document(s) in '{TOP_LEVEL_COLLECTION}'\n")

    total_writes = 0
    total_checkpoints = 0
    total_parents = 0

    for idx, parent_ref in enumerate(parent_docs, 1):
        print(f"[{idx}/{len(parent_docs)}] Document: {parent_ref.path}")

        for sub_name in SUBCOLLECTIONS:
            sub_ref = parent_ref.collection(sub_name)
            sub_docs = list(sub_ref.stream())

            if sub_docs:
                sub_doc_refs = [d.reference for d in sub_docs]
                count = delete_documents_in_batches(client, sub_doc_refs, dry_run)

                action = "Would delete" if dry_run else "Deleted"
                print(f"    {action} {count} doc(s) from subcollection '{sub_name}'")

                if sub_name == "writes":
                    total_writes += count
                elif sub_name == "checkpoints":
                    total_checkpoints += count
            else:
                print(f"    No documents in subcollection '{sub_name}'")

        # Delete the parent document itself
        if not dry_run:
            parent_ref.delete()
        total_parents += 1

        # Also discover any other subcollections not in our list
        for subcol in parent_ref.collections():
            if subcol.id not in SUBCOLLECTIONS:
                extra_docs = list(subcol.stream())
                if extra_docs:
                    extra_refs = [d.reference for d in extra_docs]
                    count = delete_documents_in_batches(client, extra_refs, dry_run)
                    action = "Would delete" if dry_run else "Deleted"
                    print(f"    {action} {count} doc(s) from extra subcollection '{subcol.id}'")

        action = "Would delete" if dry_run else "Deleted"
        print(f"    {action} parent document '{parent_ref.id}'")
        print()

    # Summary
    print(f"{'='*60}")
    print(f"  Summary  [{mode_label}]")
    print(f"{'='*60}")
    action = "Would delete" if dry_run else "Deleted"
    print(f"  {action} {total_writes} 'writes' documents")
    print(f"  {action} {total_checkpoints} 'checkpoints' documents")
    print(f"  {action} {total_parents} parent documents")
    print(f"  Total: {total_writes + total_checkpoints + total_parents} documents")

    if dry_run:
        print(f"\n  This was a DRY RUN. No data was deleted.")
        print(f"  Re-run with --execute to delete for real.\n")
    else:
        print(f"\n  Cleanup complete.\n")


def main():
    parser = argparse.ArgumentParser(
        description="Delete old LangGraph FirestoreSaver checkpoint data from Firestore."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually delete the data. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()

    dry_run = not args.execute

    client = get_firestore_client()

    start = time.time()
    cleanup(client, dry_run)
    elapsed = time.time() - start

    print(f"Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
