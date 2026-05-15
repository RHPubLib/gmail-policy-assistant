# Phase 3 — Apps Script project setup & dev deployment

Time: ~30 minutes total. Browser work only — nothing on the server.

## What you'll do

1. Create a new Apps Script project tied to your Google account
2. Paste in three files: `Code.gs`, `appsscript.json`, and `CitationMap.gs`
3. Set five Script Properties
4. Create a "Test deployment" so the add-on appears in your own Gmail only
5. Try it in Gmail with the same questions from the eval

After it works for you, **Phase 4** covers Workspace Marketplace publish + admin-OU install for the broader Library Admin pilot. Don't skip ahead — confirm it works for one user (you) before rolling out further.

---

## 1. Create the Apps Script project

1. Go to **https://script.google.com** (signed in as `derek.brown@rhpl.org`).
2. Top-left: **New project**.
3. Top of the page, click the default project name **"Untitled project"** → rename to **`RHPL Policies Add-on`** → Rename.

## 2. Paste `Code.gs`

1. In the file list (left sidebar), there's a single `Code.gs` file.
2. Open it, **Ctrl+A** → **Delete** the boilerplate.
3. Paste the contents of `/var/opt/rhpl/policies-addon/gmail-addon/Code.gs` (~19 KB, ~490 lines).
4. **Ctrl+S** to save.

## 3. Enable + paste `appsscript.json`

1. Click the **⚙ gear icon** (Project Settings) in the left sidebar.
2. Check the box **"Show 'appsscript.json' manifest file in editor"**.
3. Back in the editor (`< >` icon), `appsscript.json` is now in the file list.
4. Open it, **Ctrl+A** → **Delete**.
5. Paste the contents of `/var/opt/rhpl/policies-addon/gmail-addon/appsscript.json`.
6. **Ctrl+S** to save.

> **Gotcha (caught during deploy):** Don't include `useLocaleFromApp: true` in the
> manifest unless you also add `https://www.googleapis.com/auth/script.locale` to
> oauthScopes. The committed manifest already omits both — leave it that way.

## 4. Add `CitationMap.gs` (separate file)

The citation map is ~13 KB and **exceeds the 9 KB per-property limit** on Script Properties, so it lives as a `.gs` file rather than a property value.

1. In the Files panel, click the **`+`** next to "Files" → **Script**.
2. Name it **`CitationMap`** (the `.gs` is added automatically).
3. The new file opens with default boilerplate. **Ctrl+A** → **Delete**.
4. Paste the contents of `/var/opt/rhpl/policies-addon/gmail-addon/CitationMap.gs`.
5. **Ctrl+S**.

> **Tip:** Easiest way to grab the file content onto your clipboard:
> ```bash
> cat /var/opt/rhpl/policies-addon/gmail-addon/CitationMap.gs | wl-copy   # Wayland
> cat /var/opt/rhpl/policies-addon/gmail-addon/CitationMap.gs | xclip -selection clipboard   # X11
> ```
> Or `xdg-open` the file in your default editor and copy from there.

## 5. Set Script Properties (five entries)

Click **⚙ Project Settings** → scroll to **Script Properties** → **Edit script properties** → **Add script property** for each:

| Property | Value |
|---|---|
| `SERVICE_ACCOUNT_EMAIL` | `policies-addon@your-gcp-project.iam.gserviceaccount.com` |
| `SERVICE_ACCOUNT_KEY` | Contents of `/tmp/sa_key_for_script_property.txt` — a single very long line, ~1700 chars, starting with `-----BEGIN PRIVATE KEY-----\nMII...`. The `\n` are literal backslash-n; the code converts them to real newlines at runtime. |
| `GCP_PROJECT_ID` | `your-gcp-project` |
| `VERTEX_ENGINE_ID` | `your-policies-md` |
| `CACHE_VERSION` | `1` |

Click the blue **Save script properties** at the bottom of the section.

> **Sanity checks before saving:**
> - `SERVICE_ACCOUNT_KEY` displays as ONE long line. If actual line breaks snuck in, the JWT signing will fail with `Service account token exchange failed: invalid_grant`.
> - **Don't add `CITATION_MAP` as a Script Property** — it exceeds the 9 KB per-property limit and will cause the entire Save to silently fail (every other property reverts). The citation map lives in `CitationMap.gs` (step 4).

## 6. Create a test (dev) deployment

1. Top-right of the Apps Script editor: **Deploy** → **Test deployments**.
2. **Application(s)**: Gmail (auto-suggested from the manifest).
3. Click **Install** at the bottom.
4. Authorize the OAuth consent (gmail.addons.execute, script.external_request, userinfo.email).
5. Confirm "Application installed."
6. **Copy the Deployment ID** for later (Phase 4 Marketplace setup).

## 7. Try it in Gmail

1. Open **https://mail.google.com**, hard-reload if it was already open (Ctrl+Shift+R).
2. Right sidebar: a new icon with the RHPL logo (a small green/blue mark). Click it.
3. Type a question and click **Ask Policies**. Try:
   - *"How many puzzle kits can I have checked out at one time?"*
   - *"How many sick days do I get a year?"*
   - *"How many vacation days do I get as a full-time employee?"*
4. Expected: ~2–4 second response, formatted answer, "Sources" section with clickable 📄 buttons to source PDFs in Drive.

## 8. Verifying end-to-end

Apps Script project → **Executions** (bar-chart icon, left sidebar). Each invocation logs:

- `onHomepage` (sidebar first loaded)
- `onSubmitQuestion` (clicked Ask)
- `logQuery_` JSON line — `{ts, user, q}`: timestamp + your email + question, no answer body

### Common errors and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `SERVICE_ACCOUNT_EMAIL or SERVICE_ACCOUNT_KEY not set` | Properties weren't saved, OR `CITATION_MAP` was added as a property and bumped against the 9 KB limit | Re-add and save the five small properties; ensure no `CITATION_MAP` property exists |
| `Service account token exchange failed: invalid_grant` | SA key has real line breaks instead of `\n` escapes | Re-copy from `/tmp/sa_key_for_script_property.txt` |
| `Vertex AI :answer returned HTTP 403` | SA lacks `discoveryengine.editor` or `aiplatform.user` | Re-run `scripts/01-gcp-setup.sh` |
| `Vertex AI :answer returned HTTP 500` | Transient (we saw this during the eval too) | Retry. If persistent, check engine: `gcloud beta discovery-engine engines list` |
| Sources show "(no Drive link)" for everything | `CitationMap.gs` file doesn't exist or has a syntax error → `CITATION_MAP_DATA` is undefined → `getCitationMap_()` returns `{}` | Add the `CitationMap.gs` file (step 4) and confirm its first non-comment line is `const CITATION_MAP_DATA = {` |
| Broken-image icon in card header | Logo URL 404 or 403 | Manifest + Code.gs use `https://www.rhpl.org/favicon.ico` which is confirmed-working; don't change it without testing |
| `Required permissions: ...script.locale` | Manifest has `useLocaleFromApp: true` but no `script.locale` scope | Remove `useLocaleFromApp: true` from `addOns.common` |

## 9. Iterating on the add-on

Apps Script "Test latest code" mode picks up changes automatically after Save — no redeploy needed. Workflow:

1. Edit `gmail-addon/Code.gs` (or `CitationMap.gs`) on the workstation
2. Copy into the Apps Script editor → save
3. Hard-reload the Gmail tab (Ctrl+Shift+R) to flush cached cards
4. For prompt changes: also bump `CACHE_VERSION` to flush per-user answer caches

When the policy doc set changes (new PDFs/Docs uploaded to Drive), regenerate the
citation map — see RUNBOOK.md → "Regenerating CitationMap.gs after policy doc changes."

## What's next

Once the add-on works for you alone:

- **Phase 4** — Workspace Marketplace private publish + force-install to Library Admin OU for a small pilot (~2 weeks)
- **Phase 5 (potential)** — pivot to a Drive-connector-backed engine pointed at the Director's live BOT official documents folder, so policy edits auto-sync to the assistant. Currently pending the Director's confirmation of which Drive folder is the canonical source of truth.

---

## Reference

- Engine + eval results: `eval/findings.md`
- The code is structurally a fork of `/var/opt/rhpl/patron-sync/gmail-addon/Code.gs` — same JWT mint pattern, same caching, same error-card pattern; different API target.
- Credential rotation: `RUNBOOK.md` → "Rotating the policies-addon service account key"
