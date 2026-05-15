#!/usr/bin/env bash
#
# Deploy the hard-cap Cloud Function that detaches billing when the budget
# is breached. Run AFTER scripts/01-gcp-setup.sh (which creates the Pub/Sub
# topic this function subscribes to).
#
# Designed to run from Cloud Shell. Idempotent — rerunning redeploys the
# function in-place.
#
# To simulate a budget breach (test that it works) without spending real money,
# see TESTING below.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-your-gcp-project}"
REGION="${REGION:-us-central1}"
FUNCTION_NAME="${FUNCTION_NAME:-budget-cap}"
PUBSUB_TOPIC="${PUBSUB_TOPIC:-budget-cap-alerts}"
SA_NAME="${SA_NAME:-budget-cap-runtime}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/budget-cap"

say() { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# ---------------------------------------------------------------------------
# Runtime service account — separate identity for the Cloud Function.
# Granted the minimum permission to detach billing (project-level binding +
# the Billing Account User role on the billing account).
# ---------------------------------------------------------------------------
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
say "Creating runtime service account: $SA_EMAIL"
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Budget-cap Cloud Function runtime" \
    --description="Detaches billing from this project when the budget is breached"
else
  echo "  (already exists)"
fi

# Project-level: needs to read+update billing info on its own project
say "Granting roles/billing.projectManager on the project"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/billing.projectManager" \
  --condition=None --quiet >/dev/null

# Billing-account-level: needs Billing Account User to call updateBillingInfo
BILLING_ACCOUNT=$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingAccountName)' | sed 's|billingAccounts/||')
say "Granting roles/billing.user on billing account $BILLING_ACCOUNT"
gcloud billing accounts add-iam-policy-binding "$BILLING_ACCOUNT" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/billing.user" --quiet >/dev/null || true

# ---------------------------------------------------------------------------
# Eventarc + Pub/Sub IAM dance (discovered the hard way 2026-05-11).
# Gen2 Cloud Functions with Pub/Sub triggers route through Eventarc → Cloud
# Run. Three permissions must be in place for the invocation chain to work:
#   1. Pub/Sub service agent must be able to mint a token AS the trigger SA
#      → roles/iam.serviceAccountTokenCreator on the trigger SA
#   2. The Cloud Run service must accept calls from the trigger SA
#      → roles/run.invoker on the function's Cloud Run service
#   3. The Pub/Sub service identity has to exist in the project first
#      (auto-created on first topic, but doesn't hurt to force it).
# ---------------------------------------------------------------------------
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

say "Ensuring Pub/Sub service identity exists"
# `gcloud beta` not always installed; the agent is auto-created on first topic
# anyway. We just reference it below.

say "Granting Pub/Sub service agent roles/iam.serviceAccountTokenCreator on trigger SA"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --member="serviceAccount:$PUBSUB_AGENT" \
  --role="roles/iam.serviceAccountTokenCreator" --quiet >/dev/null

# Note: the run.invoker binding on the Cloud Run service has to happen AFTER
# the function is deployed (the service doesn't exist before then). We do it
# at the bottom of this script.

# ---------------------------------------------------------------------------
# Deploy the function (2nd gen, Pub/Sub trigger)
# ---------------------------------------------------------------------------
say "Deploying Cloud Function: $FUNCTION_NAME"
gcloud functions deploy "$FUNCTION_NAME" \
  --gen2 \
  --region="$REGION" \
  --runtime=python312 \
  --source="$SOURCE_DIR" \
  --entry-point=handle_budget_notification \
  --trigger-topic="$PUBSUB_TOPIC" \
  --service-account="$SA_EMAIL" \
  --set-env-vars="TARGET_PROJECT_ID=$PROJECT_ID" \
  --max-instances=1 \
  --timeout=60s

# Grant the trigger SA roles/run.invoker on the just-deployed Cloud Run service.
# Without this, Eventarc gets HTTP 401 when trying to invoke the function.
say "Granting roles/run.invoker on the deployed function to trigger SA"
gcloud run services add-iam-policy-binding "$FUNCTION_NAME" \
  --region="$REGION" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.invoker" \
  --condition=None --quiet >/dev/null

say "Deployed. Inspect:"
echo "  gcloud functions describe $FUNCTION_NAME --region=$REGION --gen2"
echo "  gcloud functions logs read $FUNCTION_NAME --region=$REGION --gen2 --limit=20"

cat <<'EOF'

----------------------------------------------------------------
  TESTING (simulated breach — no real charges)
----------------------------------------------------------------
Manually publish a fake "100% of budget" notification to the topic:

  gcloud pubsub topics publish budget-cap-alerts --message='{
    "budgetDisplayName": "policies-addon-annual",
    "costAmount": 600,
    "budgetAmount": 500,
    "currencyCode": "USD",
    "alertThresholdExceeded": 1.0
  }'

Then check the function logs:

  gcloud functions logs read budget-cap --region=us-central1 --gen2 --limit=20

You should see the function log "BUDGET BREACHED" and detach billing. After
verifying, RE-ATTACH billing in the console (Billing → Linked Account) so the
project stays usable. Now you know the hard cap works.
EOF
