#!/bin/bash

# Extract Project ID from .env
if [ -f .env ]; then
    PROJECT_ID=$(grep GOOGLE_CLOUD_PROJECT .env | cut -d '"' -f 2)
else
    echo "Error: .env file not found"
    exit 1
fi

if [ -z "$PROJECT_ID" ]; then
    echo "Error: GOOGLE_CLOUD_PROJECT not found in .env"
    exit 1
fi

# Get current date and commit hash
DATE=$(date +%Y%m%d-%H%M)
COMMIT_SHA=$(git rev-parse --short HEAD)

echo "------------------------------------------"
echo "Deploying version: ${DATE}-${COMMIT_SHA}"
echo "Target Project: ${PROJECT_ID}"
echo "------------------------------------------"

# Submit build with substitutions, explicitly specifying project
gcloud builds submit --project "$PROJECT_ID" --substitutions=_DATE=${DATE},_COMMIT_SHA=${COMMIT_SHA}

echo "------------------------------------------"
echo "Ensuring service is publicly accessible..."
echo "------------------------------------------"

gcloud run services add-iam-policy-binding wellness-booking \
    --project "$PROJECT_ID" \
    --member="allUsers" \
    --role="roles/run.invoker" \
    --region="asia-east1" \
    --quiet

