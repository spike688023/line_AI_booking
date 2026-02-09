#!/usr/bin/env python3
"""Test different import paths for FirestoreCheckpointer"""

import sys

# Test 1: langgraph_checkpoint_firestore
try:
    from langgraph_checkpoint_firestore import FirestoreCheckpointer
    print("✅ SUCCESS: from langgraph_checkpoint_firestore import FirestoreCheckpointer")
    sys.exit(0)
except ImportError as e:
    print(f"❌ FAILED: from langgraph_checkpoint_firestore import FirestoreCheckpointer - {e}")

# Test 2: langgraph.checkpoint.firestore
try:
    from langgraph.checkpoint.firestore import FirestoreCheckpointer
    print("✅ SUCCESS: from langgraph.checkpoint.firestore import FirestoreCheckpointer")
    sys.exit(0)
except ImportError as e:
    print(f"❌ FAILED: from langgraph.checkpoint.firestore import FirestoreCheckpointer - {e}")

# Test 3: Check what's actually in the package
try:
    import langgraph_checkpoint_firestore as pkg
    print(f"📦 Package contents: {dir(pkg)}")
except ImportError:
    print("❌ Package not installed")

sys.exit(1)
