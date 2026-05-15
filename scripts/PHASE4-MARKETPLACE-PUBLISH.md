# Phase 4 — Workspace Marketplace private publish

Goal: replace the per-user "test deployment" with a Marketplace-published,
Workspace-admin-installable add-on visible only to `rhpl.org` users. You
admin-install it to a pilot OU first (a handful of staff), gather real-use
feedback for ~2 weeks, then expand to all staff.

Time: ~30–45 minutes of console work. Nothing more on the server.

---

## 1. Create a versioned deployment in Apps Script (5 min)

The current "test deployment" runs `Test latest code` against your account
only. Marketplace publishes need a **versioned** deployment — a frozen
snapshot with a stable Deployment ID that Marketplace can reference.

1. Apps Script editor for `RHPL Policies Add-on`.
2. Top-right: **Deploy** → **New deployment**.
3. **Select type** (gear icon next to "Select type") → **Add-on**.
4. Settings:
   - **Description**: `v1 — initial Marketplace release` (just for your records)
5. **Deploy**.
6. Google shows a **Deployment ID** like `AKfyc...`. **Copy this** — you'll paste it into the Marketplace SDK later. Note: it's different from the test deployment's ID.

The old test deployment (`AKfycbyDtPfNKy_kBg5QTw6ItBUlVpeIBPsnHUfG1KkHj5E`) can stay alive for now — it doesn't conflict. You'll uninstall it at the end after verifying the Marketplace install works.

## 2. Enable Workspace Marketplace SDK (1 min)

In GCP Console for `your-gcp-project`:

1. Open **APIs & Services → Library**: https://console.cloud.google.com/apis/library?project=your-gcp-project
2. Search for **"Google Workspace Marketplace SDK"** → click it → **Enable**.

Once enabled, the SDK shows two sub-pages in the left nav: **App Configuration** and **Store Listing**. Both need to be filled in.

## 3. Fill in App Configuration (5 min)

Open **Marketplace SDK → App Configuration**:
https://console.cloud.google.com/apis/api/appsmarket-component.googleapis.com/googleappsmarketplace?project=your-gcp-project

| Field | Value |
|---|---|
| **App Visibility** | **Public** is *not* what we want. Pick **My Domain** so only `rhpl.org` users can find it. Cannot be changed after saving. |
| **Installation Settings** | **Admin Only Install** |
| **App Integrations** | Check **Google Workspace add-on** |
| **App Integrations → Apps Script Project** | Paste your **Deployment ID** from step 1 (`AKfyc...`) |
| **OAuth Scopes** | Paste these exactly, one per line: |

```
https://www.googleapis.com/auth/gmail.addons.execute
https://www.googleapis.com/auth/script.external_request
https://www.googleapis.com/auth/userinfo.email
```

(These match what's already in `appsscript.json`.)

| Field | Value |
|---|---|
| **Developer Information** → Name | Rochester Hills Public Library |
| **Developer Information** → Email | derek.brown@rhpl.org |
| **Developer Information** → Website | https://www.rhpl.org |

**Save** at the bottom.

## 4. Fill in Store Listing (10 min)

Open **Marketplace SDK → Store Listing**.

### App name and descriptions

| Field | Value |
|---|---|
| **App name** | RHPL Policies |
| **App category** | Workflow (closest match — there's no "internal HR helper" category) |
| **Language** | English |
| **App detailed description** | (pre-written below) |
| **App short description** | (pre-written below) |
| **Brief Description** (in Workspace Marketplace listing) | (pre-written below) |

**Short description** (max 80 characters):
```
Ask RHPL's policies and procedures right in Gmail. Cited answers in seconds.
```

**Detailed description** (paste verbatim):
```
RHPL Policies is an internal Rochester Hills Public Library tool that lets
staff ask questions about library policies and procedures directly from
their Gmail sidebar — no browser tab switching, no document hunting.

How it works:
• Click the clipboard icon in the Gmail right sidebar.
• Type your question in plain English. Examples:
  – "How many sick days do I get a year?"
  – "Can a non-resident check out a puzzle kit?"
  – "What family members are covered by our bereavement policy?"
• Answers come back in a few seconds, drawn ONLY from RHPL's official
  approved policy documents. Every answer cites the specific policy by
  number (e.g. CIRC-2, BENR-6) with a clickable link to the source PDF
  in the intranet Drive.
• If the answer isn't in the policy KB, the assistant says so clearly
  and directs you to the Library Director.

What's behind it:
• Google Vertex AI Search grounded on the Library Director's intranet
  policy folders. The index auto-refreshes nightly when the Director
  publishes updates.
• Only "approved" policy PDFs are indexed — drafts and working documents
  in the Director's private workspace are never consulted.
• Built on the same secure service-account pattern as the existing
  RHPL Patron Check Gmail add-on. Staff Google accounts never get direct
  access to any backend system.

Privacy:
• Questions are logged for audit (who asked what, when) — answer bodies
  are not stored.
• No content is shared outside the rhpl.org Google Workspace organization.
• Available only to @rhpl.org accounts.

For questions or feature requests, contact derek.brown@rhpl.org.
```

### Graphics

Upload the four PNG icons we generated:

| Size | File path on workstation |
|---|---|
| 32×32 | `/var/opt/rhpl/policies-addon/gmail-addon/icons/policies-addon-32.png` |
| 48×48 | `/var/opt/rhpl/policies-addon/gmail-addon/icons/policies-addon-48.png` |
| 96×96 | `/var/opt/rhpl/policies-addon/gmail-addon/icons/policies-addon-96.png` |
| 128×128 | `/var/opt/rhpl/policies-addon/gmail-addon/icons/policies-addon-128.png` |

**The sidebar tab icon in Gmail comes from the 128×128 here**, not the `logoUrl` in the manifest. Skip these and the icon will look broken.

### Screenshots

You'll need at least one screenshot (PNG/JPG, max 5 MB each, recommended 1280×800 or 16:10 aspect). You already have great ones from testing — use one or more of the answer-card screenshots you took (the vacation-days one, the bereavement one, or the puzzle-kits one).

If you need a fresh clean shot: open Gmail, click the RHPL Policies icon, ask *"How many puzzle kits can I have checked out?"*, take a screenshot of the answer with the sidebar visible.

### Support links

| Field | Value |
|---|---|
| **Support URL** | https://www.rhpl.org/contact (or any RHPL support page) |
| **Privacy policy URL** | https://www.rhpl.org/about (or wherever your standard privacy policy lives) |
| **Terms of service URL** | Same as above, or your standard ToS page |

**Save** at the bottom.

## 5. Publish (1 min)

Back at the top of Store Listing → click **Publish** (or **Submit for publication**). Since visibility is "My Domain" (private to rhpl.org), this is approved automatically — no Google review required.

You should now see status: **Published**.

## 6. Force-install to your pilot OU (3 min)

This makes the add-on appear automatically in selected staff's Gmail.

1. Open [admin.google.com](https://admin.google.com).
2. **Apps** → **Google Workspace Marketplace apps** → **Apps list**.
3. **Add app** (top of the list) → search "RHPL Policies" or scroll under "Private apps".
4. Click your app → **Admin install**.
5. **Continue** through the permissions screen (it shows the same OAuth scopes you configured in step 3).
6. **Select OUs**: pick the **Library Admin OU** (or whatever small group you want to pilot with — your IT OU is also a sensible first target).
7. **Finish**.

It can take **up to 24 hours** for the install to propagate to all selected users' Gmail (often it's much faster — minutes — but Google says up to 24h).

## 7. Verify the Marketplace install works (2 min)

After ~10 minutes:

1. Reload Gmail (Ctrl+Shift+R).
2. The clipboard 📋 icon should appear in the right sidebar — installed via Marketplace this time, not via your personal test deployment.
3. Ask a known question. Confirm it works the same way it did during testing.

## 8. Clean up the test deployment (2 min)

Once you've confirmed the Marketplace install works for you (and ideally a second pilot person):

1. Back to Apps Script → **Deploy** → **Test deployments**.
2. Find your existing test deployment.
3. **Uninstall**.

The Marketplace-published version remains. Your personal Gmail now uses the same install path as every other pilot staff member.

## Pilot rollout discipline

- **First 24 hours**: just you on the Marketplace install. Verify it behaves the same as the test deployment did. If anything's off, the test deployment is still in `Test latest code` mode so you can iterate quickly.
- **Days 2–14**: pilot OU (~3–8 staff). Watch the Apps Script Executions tab and the project's Cloud Logging for any errors. Audit the question-log entries (`logQuery_` console.log lines) — what staff actually ask is real product feedback.
- **After 2 weeks**: if no significant issues, expand the Marketplace install in Admin Console to additional staff OUs.

## When you push a code change

For the live "Test latest code" test deployment, every save propagates instantly. The Marketplace-published versioned deployment is **frozen at v1**. To push a code change to staff:

1. Edit `Code.gs` (or whatever) in Apps Script. Save.
2. **Deploy** → **Manage deployments** → click the existing versioned deployment → pencil icon (Edit).
3. Under "Version" choose **New version** → describe what changed.
4. **Deploy**.
5. Marketplace picks up the new version on its next refresh (typically within an hour, often much faster).

You **don't** need to re-publish in Marketplace SDK or re-install — same Deployment ID, just a new version of the underlying code.

## Bumping OAuth scopes

If you ever add a new OAuth scope to `appsscript.json`, two extra steps:

1. Create a new version (as above).
2. Marketplace SDK → App Configuration → update the scopes list to match → Save.
3. Staff will see a one-time re-authorization prompt on next add-on use.

---

## Quick reference

| What | Where |
|---|---|
| Generated icons (32/48/96/128) | `/var/opt/rhpl/policies-addon/gmail-addon/icons/` |
| Apps Script project | https://script.google.com (search "RHPL Policies Add-on") |
| Marketplace SDK config | https://console.cloud.google.com/apis/api/appsmarket-component.googleapis.com/googleappsmarketplace?project=your-gcp-project |
| Workspace admin app install | https://admin.google.com/ac/apps/gsuite |
| The deployed Vertex engine | `your-policies-engine` in `your-gcp-project` project |
| Nightly sync script | `/var/opt/rhpl/policies-addon/scripts/sync-live-policies.py` (see PHASE5 doc for cron setup — not yet done) |
