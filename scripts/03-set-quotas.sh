#!/usr/bin/env bash
#
# Set per-API quotas for the policies-addon project. This is the third (and
# tightest-bound) layer of the cost guardrails:
#
#   Layer 1: budget alerts        (email at 50/90/100/150%)
#   Layer 2: hard-cap function    (detaches billing at 100%)
#   Layer 3: API quotas (here)    (caps usage, not just spend)
#
# Quotas are independent of billing — a runaway loop hits 429 Too Many Requests
# long before it spends money. Sized to ~100x our peak realistic load so they
# don't fire under normal use.
#
# Many Vertex AI / Discovery Engine quotas can only be edited via the console
# (the API enforces them but doesn't expose all of them via gcloud). This
# script prints exactly what to change and gives you direct links.
#
# Run from Cloud Shell. Some commands may need rerunning if you bump them in
# the console first; this script reports current state.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-your-gcp-project}"
REGION="${REGION:-us-central1}"

say() { printf '\n\033[1;34m== %s ==\033[0m\n' "$*"; }
note() { printf '  \033[1;33mNOTE:\033[0m %s\n' "$*"; }

gcloud config set project "$PROJECT_ID" >/dev/null

# ---------------------------------------------------------------------------
# Recommended quota values for this workload (current peak ~50 chats/mo)
# ---------------------------------------------------------------------------
cat <<EOF

Target quotas for $PROJECT_ID:

  Discovery Engine API
    discoveryengine.googleapis.com/queries_per_minute_per_project    →   100
    discoveryengine.googleapis.com/operations_per_day_per_project    →   500

  Vertex AI (Gemini generate-content)
    aiplatform.googleapis.com/online_prediction_requests_per_minute  →   100
    aiplatform.googleapis.com/generate_content_input_tokens_per_minute  →   50000
    aiplatform.googleapis.com/generate_content_output_tokens_per_minute →   10000

These are roughly 100x peak realistic load. If they ever fire under real use,
loosen the cap rather than removing it.

EOF

say "Show current Discovery Engine quotas"
gcloud services quota list \
  --service=discoveryengine.googleapis.com \
  --consumer="projects/$PROJECT_ID" \
  --format='table(metric, displayName, unit, consumerOverride.overrideValue:label=OVERRIDE)' \
  2>/dev/null | head -30 || note "(API may not be enabled yet — run 01-gcp-setup.sh first)"

say "Show current Vertex AI quotas"
gcloud services quota list \
  --service=aiplatform.googleapis.com \
  --consumer="projects/$PROJECT_ID" \
  --format='table(metric, displayName, unit, consumerOverride.overrideValue:label=OVERRIDE)' \
  2>/dev/null | head -30 || note "(API may not be enabled yet — run 01-gcp-setup.sh first)"

cat <<EOF

----------------------------------------------------------------
  Apply the overrides (console, ~5 minutes)
----------------------------------------------------------------
Open these two URLs in your browser. Each one shows that API's quotas filtered
to your project. For each metric in the target list above, click the row, then
"Edit Quota" and set the new value:

  Discovery Engine:
    https://console.cloud.google.com/apis/api/discoveryengine.googleapis.com/quotas?project=$PROJECT_ID

  Vertex AI:
    https://console.cloud.google.com/apis/api/aiplatform.googleapis.com/quotas?project=$PROJECT_ID

Some quotas accept overrides instantly; a few require a Google review (rare for
LOWERING a quota — Google approves "make my project smaller" requests promptly).

----------------------------------------------------------------
  Verify the quota wall actually fires (recommended)
----------------------------------------------------------------
After setting Discovery Engine queries_per_minute_per_project to 100, run a
quick burst test (assumes the Vertex app exists — do this after Phase 1):

  for i in \$(seq 1 200); do
    curl -s -H "Authorization: Bearer \$(gcloud auth print-access-token)" \\
      -H "Content-Type: application/json" \\
      "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_ID/locations/global/.../serve" \\
      -d '{"query":{"input":"test"}}' &
  done; wait
  # You should see HTTP 429 responses after ~100 requests/min.
EOF
