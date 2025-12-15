import os
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()

project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
print(f"Project ID: {project_id}")

if not project_id:
    print("Error: GOOGLE_CLOUD_PROJECT not set")
    exit(1)

try:
    db = firestore.Client(project=project_id)
    print("Firestore client initialized")
    
    reservations_ref = db.collection("reservations")
    docs = list(reservations_ref.stream())
    
    print(f"Found {len(docs)} reservations:")
    for doc in docs:
        print(f" - {doc.id}: {doc.to_dict()}")
        
except Exception as e:
    print(f"Error: {e}")
