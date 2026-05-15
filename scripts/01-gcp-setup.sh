#!/usr/bin/env bash
#
# Phase 0 — GCP guardrails for the policies-addon project.
#
# Run this from Google Cloud Shell (no local gcloud install needed). It is
# IDEMPOTENT: rerunning is safe and only creates missing resources.
#
# Prerequisites (you, manually, ~15 min, once):
#   1. Create a new GCP project in the console:
#      https://console.cloud.google.com/projectcreate
#      Suggested project ID:  your-gcp-project
#      Organization:          rhpl.org
#   2. Link it to the Google for Nonprofits billing account so credits apply.
#   3. Open Cloud Shell from the console (top-right terminal icon).
#   4. Paste this entire script and review the variables below before running.
#
# What this script creates (idempotently):
#   • Enables required APIs (Vertex AI, Discovery Engine, Billing, Pub/Sub, ...)
#   • Service account: policies-addon@<project>.iam.gserviceaccount.com
#   • Project-level $500/yr budget with email alerts at 50/90/100/150%
#   • Pub/Sub topic for budget breach notifications
#   • (Cloud Function deploy is 02-deploy-budget-cap.sh — run that next)
#
# What this script DOES NOT do:
#   • Create the Vertex AI data store (Phase 1, manual via console)
#   • Deploy the Cloud Function (02-deploy-budget-cap.sh)
#   • Set the per-API quotas (03-set-quotas.sh)
#
# After this runs, run 02-deploy-budget-cap.sh, then 03-set-quotas.sh.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — review and edit before running
# ---------------------------------------------------------------------------

PROJECT_ID="${PROJECT_ID:-your-gcp-project}"
REGION="${REGION:-us-central1}"
SA_NAME="${SA_NAME:-policies-addon}"
BUDGET_AMOUNT_USD="${BUDGET_AMOUNT_USD:-500}"
BUDGET_DISPLAY_NAME="${BUDGET_DISPLAY_NAME:-policies-addon-annual}"
BUDGET_ALERT_EMAIL="${BUDGET_ALERT_EMAIL:-derek.brown@rhpl.org}"
PUBSUB_TOPIC="${PUBSUB_TOPIC:-budget-cap-alerts}"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# ---------------------------------------------------------------------------

say() { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }

say "Targeting project: $PROJECT_ID"
gcloud config set project "$PROJECT_ID" >/dev/null

# Sanity: project must exist and be billing-linked
if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  warn "Project $PROJECT_ID does not exist or you lack access."
  warn "Create it at https://console.cloud.google.com/projectcreate"
  exit 1
fi

BILLING_ACCOUNT=$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingAccountName)' 2>/dev/null | sed 's|billingAccounts/||')
if [[ -z "$BILLING_ACCOUNT" ]]; then
  warn "Project is not linked to a billing account."
  warn "Link your Google for Nonprofits billing account in the console first:"
  warn "  https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT_ID"
  exit 1
fi
say "Billing account: $BILLING_ACCOUNT"

# ---------------------------------------------------------------------------
# Enable APIs
# ---------------------------------------------------------------------------
say "Enabling APIs"
gcloud services enable \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  cloudbilling.googleapis.com \
  billingbudgets.googleapis.com \
  pubsub.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  eventarc.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com

# ---------------------------------------------------------------------------
# Service account
# ---------------------------------------------------------------------------
say "Creating service account: $SA_EMAIL"
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="RHPL Policies Add-on (Apps Script)" \
    --description="Used by the Gmail Add-on to query Vertex AI Agent Builder. No DWD."
  # IAM bindings against a brand-new SA race with backend propagation. Poll
  # for the SA to be visible before granting roles below.
  for i in $(seq 1 20); do
    if gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then break; fi
    sleep 2
  done
else
  echo "  (already exists)"
fi

# Roles needed: query Discovery Engine + invoke Vertex AI generative models
for role in roles/discoveryengine.editor roles/aiplatform.user; do
  say "Granting $role to $SA_EMAIL"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$role" --condition=None --quiet >/dev/null
done

# ---------------------------------------------------------------------------
# Pub/Sub topic for budget breach notifications (consumed by the Cloud Function)
# ---------------------------------------------------------------------------
say "Creating Pub/Sub topic: $PUBSUB_TOPIC"
if ! gcloud pubsub topics describe "$PUBSUB_TOPIC" >/dev/null 2>&1; then
  gcloud pubsub topics create "$PUBSUB_TOPIC"
else
  echo "  (already exists)"
fi

# ---------------------------------------------------------------------------
# Budget — alerts at 50/90/100/150%, plus Pub/Sub notification for hard-cap
# ---------------------------------------------------------------------------
say "Creating annual budget: \$${BUDGET_AMOUNT_USD}"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
TOPIC_FULL="projects/$PROJECT_ID/topics/$PUBSUB_TOPIC"

# Idempotency: if a budget with this display name already exists, skip.
EXISTING=$(gcloud billing budgets list \
  --billing-account="$BILLING_ACCOUNT" \
  --filter="displayName=$BUDGET_DISPLAY_NAME" \
  --format='value(name)' | head -n1)

if [[ -n "$EXISTING" ]]; then
  echo "  Budget '$BUDGET_DISPLAY_NAME' already exists: $EXISTING"
  echo "  (delete it manually if you want this script to recreate it)"
else
  gcloud billing budgets create \
    --billing-account="$BILLING_ACCOUNT" \
    --display-name="$BUDGET_DISPLAY_NAME" \
    --budget-amount="${BUDGET_AMOUNT_USD}USD" \
    --filter-projects="projects/$PROJECT_NUMBER" \
    --threshold-rule=percent=50,basis=current-spend \
    --threshold-rule=percent=90,basis=current-spend \
    --threshold-rule=percent=100,basis=current-spend \
    --threshold-rule=percent=150,basis=current-spend \
    --notifications-rule-pubsub-topic="$TOPIC_FULL" \
    --notifications-rule-monitoring-notification-channels="" \
    --calendar-period=year
  echo "  Budget created. Pub/Sub notifications routed to $TOPIC_FULL"
fi

# Note: gcloud doesn't accept email addresses directly on budget create.
# Email alerts are configured separately via Monitoring notification channels.
# Do this in the console once (Billing → Budgets & alerts → edit → "Manage notifications"
# → add an email channel for $BUDGET_ALERT_EMAIL).

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
cat <<EOF

\033[1;32m=========================================================\033[0m
\033[1;32m  Phase 0a complete.\033[0m
\033[1;32m=========================================================\033[0m

  Project:           $PROJECT_ID
  Service account:   $SA_EMAIL
  Pub/Sub topic:     $PUBSUB_TOPIC
  Annual budget:     \$$BUDGET_AMOUNT_USD (alerts at 50/90/100/150%)
  Billing account:   $BILLING_ACCOUNT

Next steps:
  1. Add an EMAIL notification channel for $BUDGET_ALERT_EMAIL
     in the budget settings (console only — gcloud cannot do this).
     Billing → Budgets & alerts → edit budget → Manage notifications.

  2. Deploy the hard-cap Cloud Function:
       bash scripts/02-deploy-budget-cap.sh

  3. Then set the per-API quotas:
       bash scripts/03-set-quotas.sh

EOF
