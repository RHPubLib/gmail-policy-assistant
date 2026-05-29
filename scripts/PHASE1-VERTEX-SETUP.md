# Phase 1 — Vertex AI Agent Builder setup (manual)

This phase has to be done in the GCP console — the Agent Builder configuration
isn't fully scriptable yet, and the Shared Drive step needs Workspace Admin.

Prerequisite: Phase 0 complete (`01-gcp-setup.sh`, `02-deploy-budget-cap.sh`,
`03-set-quotas.sh`).

---

## 1. Create the Shared Drive (~5 min)

In Google Drive at `drive.google.com`, in the left sidebar under **Shared
drives** → **+ New** → name it **`RHPL Policies KB`**.

**Make the service account a Manager:**

1. Open the new Shared Drive.
2. Click **Manage members** (top-right people icon).
3. Add `policies-addon@your-gcp-project.iam.gserviceaccount.com` as a
   **Manager**.
4. Add `derek.brown@rhpl.org` (or whoever maintains policies) as a Manager.
5. Leave non-member access set to "Off — restricted." Vertex AI indexes via
   the service account, not via individual staff Drive access.

> **Why a Shared Drive and not "My Drive":** A service account can't own
> files in a user's "My Drive." A Shared Drive lets the SA be a Manager, which
> is what Vertex AI Search needs to index it.

## 2. Upload the converted policy markdowns (~10 min)

The OWUI pipeline has already converted source PDFs to markdown. Copy them up:

```bash
# From the local-ai server:
cd /path/to/kb-converted

# Verify what you have:
find . -name "*.md" | head -20
find . -name "*.md" | wc -l    # how many docs?

# Upload using rclone (preferred — preserves folder structure) or the Drive
# web UI (drag-and-drop the top-level folders).
# rclone setup is one-time: rclone config → add a "drive" remote scoped to the
# Shared Drive. Then:
rclone copy /path/to/kb-converted/ \
  rhpl-shared-drive:"RHPL Policies KB/" \
  --drive-shared-with-me --progress
```

Folder structure on the Drive should mirror the OWUI KB:

```
RHPL Policies KB/
├── Personnel Policies/
│   ├── General/
│   ├── Benefits/
│   └── Work Rules/
├── Public Service Policies/
│   ├── Circulation Policies/      ← CIRC-1, CIRC-2, ...
│   └── Customer Service Policies/
└── Staff Knowledge/
```

> Don't upload the source PDFs — Vertex AI Search prefers clean text. The
> markdown files Docling produced are already pre-processed.

## 3. Create the Vertex AI Search data store (~5 min)

In the GCP console:
[**Agent Builder → Data Stores → Create Data Store**](https://console.cloud.google.com/gen-app-builder/data-stores)

| Field | Value |
|---|---|
| Source | **Google Drive** |
| What to index | The `RHPL Policies KB` Shared Drive folder |
| Data store name | `policies-kb` |
| Data store ID | `policies-kb-<random suffix>` (copy this — goes into `.env`) |
| Location | **global** (most options are global-only at the data-store level) |
| Document parsing | **Layout parser** (better for the policy tables) |
| Document chunking | Default chunk size; let Vertex tune it |

Initial indexing takes 5–30 minutes depending on doc count. The data store
status will show "Active" once it's ready to query.

**Save the data store ID** — you'll need it for `.env` (`VERTEX_DATA_STORE_ID`)
and the Agent Builder app.

## 4. Create the Agent Builder app (~10 min)

[**Agent Builder → Apps → Create App → Search**](https://console.cloud.google.com/gen-app-builder/engines)

| Field | Value |
|---|---|
| App type | **Search** |
| App name | `your-policies` |
| Company name | Rochester Hills Public Library |
| Connected data store | `policies-kb` (the one you just created) |
| Generative answers | **Enabled** |
| Generative model | **Gemini 2.5 Flash** (cheapest with current quality) |
| Citations | **Required** |
| Adversarial query filter | **Enabled** |
| Non-summary-seeking query filter | **Enabled** |

### Custom prompt (preamble)

Paste this into the **Custom prompt / preamble** field. It's the OWUI system
prompt with two Vertex tweaks marked `[VERTEX]`:

```
You are a policy and procedures assistant for Rochester Hills Public Library (RHPL) staff. Your knowledge base contains the library's official policies and guidelines.

Before answering, mentally translate the staff member's question into library HR/policy vocabulary. Examples: "time off when someone dies" → "bereavement leave"; "call in sick" → "sick leave"; "can I work from home" → "telework"; "got hurt at work" → "workers compensation"; "written up" → "disciplinary action"; "what are my options if I have to serve in the military" → "military leave". Use this translation to identify which policy a retrieved document is answering, even if the wording differs.

When answering:
- Answer ONLY from the attached policy documents (the search results). Do not use outside knowledge or guess.
- Cite the specific policy document name and number (e.g., "Per CIRC-2 Loan and Renewal Policy..."). [VERTEX] Use the citation_indices the system provides — every factual claim should map to a retrieved chunk.
- Distinguish between a Policy (binding rule) and Guidelines (procedural guidance).
- If the answer is not in the knowledge base, say so clearly and direct the staff member to the library director. Do not invent a policy that does not exist.
- Keep answers concise and practical for staff use.
- [VERTEX] If the question is not about RHPL policies (general knowledge, creative writing, opinion, personal data lookup), decline cleanly: "I only answer questions about RHPL policies and procedures. For that, please contact the library director."

Always write in American English ("color" not "colour", "organize" not "organise").
```

Save the app. **Copy the Serving Config ID** — you'll need it for `.env`
(`VERTEX_SERVING_CONFIG_ID`, typically `default_search`).

## 5. Smoke test (~2 min)

From Cloud Shell:

```bash
PROJECT_ID=your-gcp-project
DS_ID=policies-kb-<your suffix>        # from step 3

curl -sS -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/collections/default_collection/dataStores/${DS_ID}/servingConfigs/default_search:converse" \
  -d '{
    "query": {"input": "How many puzzle kits can a patron check out at one time?"},
    "summarySpec": {
      "summaryResultCount": 5,
      "includeCitations": true,
      "ignoreAdversarialQuery": true
    }
  }' | jq .
```

Expected: JSON with `reply.summary.summaryText` containing "2" (or whatever the
current limit is) and a citation pointing to CIRC-2.

## 6. Wire up the eval harness

```bash
cd /var/opt/rhpl/policies-addon
$EDITOR .env
#   VERTEX_DATA_STORE_ID=<the ID from step 3>
#   VERTEX_SERVING_CONFIG_ID=default_search

python3 eval/run_eval.py     # both backends, side-by-side
```

Open the generated `eval/results-<ts>.md` and score each row. Pass-bar in
`eval/README.md`. Outcome goes in `eval/findings.md`.

If Phase 2 passes, proceed to Phase 3 (build the Gmail Add-on against this
Vertex app). If it fails after up to three rounds of prompt iteration, the
plan reverts to the local-bridge architecture (see plan file).
