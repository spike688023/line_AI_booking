#!/bin/bash
# Get current date and commit hash
DATE=$(date +%Y%m%d-%H%M)
COMMIT_SHA=$(git rev-parse --short HEAD)

echo "------------------------------------------"
echo "Deploying version: ${DATE}-${COMMIT_SHA}"
echo "------------------------------------------"

# Submit build with substitutions
gcloud builds submit --substitutions=_DATE=${DATE},_COMMIT_SHA=${COMMIT_SHA}

echo "------------------------------------------"
echo "Ensuring service is publicly accessible..."
echo "------------------------------------------"

gcloud run services add-iam-policy-binding coffee-shop-agent \
    --member="allUsers" \
    --role="roles/run.invoker" \
    --region="asia-east1" \
    --quiet

