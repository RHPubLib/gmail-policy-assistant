// RHPL Policies — Gmail Add-on
//
// Sidebar "Ask Policies" card that queries Vertex AI Agent Builder
// (your-policies-md engine) and renders the grounded answer + citations
// linking to source PDF/DOCX in the RHPL Policies KB Shared Drive.
//
// Architecture:
//   1. User opens Gmail, clicks the add-on icon → homepage trigger fires
//   2. onHomepage() returns a card with a multi-line text input + "Ask" button
//   3. User types question, clicks Ask → onSubmitQuestion() runs
//   4. Service-account JWT mint → OAuth bearer → Vertex AI :answer call
//   5. Parse answer text + citations, look up each citation in CITATION_MAP
//      to find its Drive file ID, render answer card with clickable sources
//   6. "Ask another question" button on answer card pops back to input
//   7. Most recent answer is stashed in UserCache (15 min TTL) so it
//      re-renders when Gmail navigation resets the card stack (homepage ↔
//      message view); "Ask another question" clears the stash
//
// All auth uses a single GCP service account (policies-addon@*). Staff
// Google accounts never get direct Vertex AI permissions.
//
// Required Script Properties (set in Project Settings → Script Properties):
//   SERVICE_ACCOUNT_EMAIL    policies-addon@your-gcp-project.iam.gserviceaccount.com
//   SERVICE_ACCOUNT_KEY      RSA private key as single line with \n escapes (see RUNBOOK)
//   GCP_PROJECT_ID           your-gcp-project
//   VERTEX_ENGINE_ID         your-policies-md
//   VERTEX_SERVING_CONFIG_ID default_search   (optional, this is the default)
//   CITATION_MAP             JSON object from gmail-addon/citation-map.json
//   CACHE_VERSION            integer salt (bump to flush all per-user answer caches)


// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/** Gmail homepage trigger — runs when user opens the add-on in Gmail.
 *
 * Gmail Add-ons reset the card stack each time the trigger context changes
 * (homepage ↔ message view), so a fresh input card would blank out an answer
 * the staff member was still reading. We stash the most recent answer in
 * UserCache (15 min TTL) and re-render it here if present, so the answer
 * "follows" the user as they navigate the inbox. The "Ask another question"
 * button clears the stash to get back to the empty input card. */
function onHomepage(e) {
  try {
    const stashed = getStashedAnswer_();
    if (stashed && stashed.question && stashed.result) {
      return buildAnswerCard_(stashed.question, stashed.result, stashed.ts);
    }
    return buildInputCard_('');
  } catch (err) {
    console.error('onHomepage failed:', err && err.stack ? err.stack : err);
    return buildErrorCard_(err);
  }
}

/** Gmail contextual trigger — same behavior as homepage. */
function onGmailMessage(e) {
  return onHomepage(e);
}


// ---------------------------------------------------------------------------
// Action handlers
// ---------------------------------------------------------------------------

/** Submit handler — reads the form input, calls Vertex, returns answer card. */
function onSubmitQuestion(e) {
  try {
    const question = ((e && e.formInput && e.formInput.question) || '').trim();
    if (!question) {
      return CardService.newActionResponseBuilder()
        .setNotification(CardService.newNotification().setText('Please type a question.'))
        .build();
    }

    const userEmail = (e && e.commonEventObject && e.commonEventObject.userLocale)
      ? (Session.getActiveUser().getEmail() || '(unknown)')
      : (Session.getActiveUser().getEmail() || '(unknown)');

    logQuery_(userEmail, question);

    const result = askPolicies_(question);
    stashLastAnswer_(question, result);
    return CardService.newActionResponseBuilder()
      .setNavigation(CardService.newNavigation().pushCard(
        buildAnswerCard_(question, result)
      ))
      .build();
  } catch (err) {
    console.error('onSubmitQuestion failed:', err && err.stack ? err.stack : err);
    return CardService.newActionResponseBuilder()
      .setNavigation(CardService.newNavigation().pushCard(buildErrorCard_(err)))
      .build();
  }
}

/** "Ask another question" button — clears the stashed answer so the
 *  homepage trigger goes back to a blank input card, then replaces the
 *  current top card with a fresh input card. (popToRoot alone isn't enough
 *  when the navigation stack was started from a restored answer card.) */
function onAskAnother(e) {
  clearStashedAnswer_();
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation()
      .popToRoot()
      .updateCard(buildInputCard_('')))
    .build();
}


/** "Report a problem" button on the answer card — opens the feedback card,
 *  pre-populated with the question and the answer the staff member just saw. */
function onReportProblem(e) {
  try {
    const params = (e && e.commonEventObject && e.commonEventObject.parameters) || {};
    return CardService.newActionResponseBuilder()
      .setNavigation(CardService.newNavigation().pushCard(
        buildFeedbackCard_(params.question || '', params.answer || '')
      ))
      .build();
  } catch (err) {
    console.error('onReportProblem failed:', err && err.stack ? err.stack : err);
    return CardService.newActionResponseBuilder()
      .setNavigation(CardService.newNavigation().pushCard(buildErrorCard_(err)))
      .build();
  }
}


/** Feedback form submit — sends the email, returns a thank-you card. */
function onSubmitFeedback(e) {
  try {
    const feedback = ((e && e.formInput && e.formInput.feedback) || '').trim();
    if (!feedback) {
      return CardService.newActionResponseBuilder()
        .setNotification(CardService.newNotification().setText(
          'Please tell us what you were trying to find before sending.'))
        .build();
    }
    const params = (e && e.commonEventObject && e.commonEventObject.parameters) || {};
    const question = params.question || '(unavailable)';
    const answer = params.answer || '(unavailable)';
    const userEmail = Session.getActiveUser().getEmail() || '(unknown)';

    sendFeedbackEmail_(userEmail, question, answer, feedback);
    logFeedback_(userEmail, question, feedback);

    return CardService.newActionResponseBuilder()
      .setNavigation(CardService.newNavigation().pushCard(buildFeedbackThanksCard_()))
      .build();
  } catch (err) {
    console.error('onSubmitFeedback failed:', err && err.stack ? err.stack : err);
    return CardService.newActionResponseBuilder()
      .setNavigation(CardService.newNavigation().pushCard(buildErrorCard_(err)))
      .build();
  }
}


/** Cancel button on the feedback card — pops back to the answer card. */
function onCancelFeedback(e) {
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().popCard())
    .build();
}


// ---------------------------------------------------------------------------
// Card builders
// ---------------------------------------------------------------------------

function buildCardHeader_() {
  return CardService.newCardHeader()
    .setTitle('RHPL Policies')
    .setSubtitle('Ask a policy or procedures question')
    .setImageUrl('https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f4cb.png');
}

function buildInputCard_(prefill) {
  const input = CardService.newTextInput()
    .setFieldName('question')
    .setTitle('Your question')
    .setHint('e.g. "How many sick days do I get?" or "Can a non-resident check out a puzzle kit?"')
    .setMultiline(true)
    .setValue(prefill || '');

  const askBtn = CardService.newTextButton()
    .setText('Ask Policies')
    .setTextButtonStyle(CardService.TextButtonStyle.FILLED)
    .setOnClickAction(CardService.newAction().setFunctionName('onSubmitQuestion'));

  const section = CardService.newCardSection()
    .addWidget(input)
    .addWidget(askBtn);

  const footer = CardService.newCardSection()
    .addWidget(CardService.newTextParagraph().setText(
      '<font color="#666666">' +
      'Answers come from RHPL\'s official policy documents. ' +
      'If the answer isn\'t in the policy KB, the assistant will say so. ' +
      'For situations not covered, contact the library director.' +
      '</font>'
    ));

  return CardService.newCardBuilder()
    .setHeader(buildCardHeader_())
    .addSection(section)
    .addSection(footer)
    .build();
}

function buildAnswerCard_(question, result, askedAtMs) {
  const qSection = CardService.newCardSection()
    .addWidget(CardService.newTextParagraph().setText(
      '<b>Q:</b> ' + escapeHtml_(question)
    ));
  if (askedAtMs) {
    qSection.addWidget(CardService.newTextParagraph().setText(
      '<font color="#888888"><i>Asked ' + formatAgo_(askedAtMs) + '</i></font>'
    ));
  }

  const answerText = mdToCardHtml_(result.answer || '(no answer)');
  const aSection = CardService.newCardSection()
    .addWidget(CardService.newTextParagraph().setText(answerText));

  // Sources — render each unique source as a clickable button to the Drive doc.
  const sourcesSection = CardService.newCardSection().setHeader('Sources');
  const seen = {};
  let renderedAny = false;
  (result.sources || []).forEach(function(src) {
    const key = src.filename || src.title;
    if (!key || seen[key]) return;
    seen[key] = true;
    const driveUrl = citationToDriveUrl_(src);
    const label = src.title || stripExt_(src.filename);
    if (driveUrl) {
      sourcesSection.addWidget(CardService.newTextButton()
        .setText('📄 ' + label)
        .setOpenLink(CardService.newOpenLink()
          .setUrl(driveUrl)
          .setOpenAs(CardService.OpenAs.OVERLAY)));
      renderedAny = true;
    } else {
      sourcesSection.addWidget(CardService.newTextParagraph().setText(
        '<font color="#666666">📄 ' + escapeHtml_(label) + ' <i>(no Drive link)</i></font>'
      ));
      renderedAny = true;
    }
  });
  if (!renderedAny) {
    sourcesSection.addWidget(CardService.newTextParagraph().setText(
      '<font color="#666666"><i>No sources cited.</i></font>'
    ));
  }

  const followup = CardService.newCardSection()
    .addWidget(CardService.newTextButton()
      .setText('Ask another question')
      .setOnClickAction(CardService.newAction().setFunctionName('onAskAnother')))
    .addWidget(CardService.newTextButton()
      .setText('Report a problem')
      .setOnClickAction(CardService.newAction()
        .setFunctionName('onReportProblem')
        .setParameters({
          question: String(question || ''),
          answer: String(result.answer || ''),
        })))
    .addWidget(CardService.newTextParagraph().setText(
      '<font color="#666666">Answer not in policy? Contact your departmental manager ' +
      'or the library director depending on the question.</font>'
    ));

  return CardService.newCardBuilder()
    .setHeader(buildCardHeader_())
    .addSection(qSection)
    .addSection(aSection)
    .addSection(sourcesSection)
    .addSection(followup)
    .build();
}

function buildFeedbackCard_(question, answer) {
  // Show the question + a trimmed excerpt of the answer for context (read-only).
  const qDisplay = CardService.newKeyValue()
    .setTopLabel('Your question')
    .setContent(question || '(unavailable)')
    .setMultiline(true);

  const trimmed = String(answer || '').trim();
  const excerpt = trimmed.length > 300 ? trimmed.substring(0, 297) + '…' : trimmed;
  const aDisplay = CardService.newKeyValue()
    .setTopLabel('Answer you received (excerpt)')
    .setContent(excerpt || '(no answer recorded)')
    .setMultiline(true);

  const input = CardService.newTextInput()
    .setFieldName('feedback')
    .setTitle('What were you trying to find?')
    .setHint('Tell us what was missing, wrong, or where else to look.')
    .setMultiline(true);

  const sendBtn = CardService.newTextButton()
    .setText('Submit feedback')
    .setTextButtonStyle(CardService.TextButtonStyle.FILLED)
    .setOnClickAction(CardService.newAction()
      .setFunctionName('onSubmitFeedback')
      .setParameters({
        question: String(question || ''),
        answer: String(answer || ''),
      }));

  const cancelBtn = CardService.newTextButton()
    .setText('Cancel')
    .setOnClickAction(CardService.newAction().setFunctionName('onCancelFeedback'));

  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader()
      .setTitle('Submit feedback')
      .setSubtitle('Help us improve the answers')
      .setImageUrl('https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f4cb.png'))
    .addSection(CardService.newCardSection()
      .addWidget(qDisplay)
      .addWidget(aDisplay)
      .addWidget(input)
      .addWidget(sendBtn)
      .addWidget(cancelBtn))
    .build();
}


function buildFeedbackThanksCard_() {
  return CardService.newCardBuilder()
    .setHeader(buildCardHeader_())
    .addSection(CardService.newCardSection()
      .addWidget(CardService.newTextParagraph().setText(
        '<b>✓ Thanks for the feedback</b><br><br>' +
        'Your submission has been received along with the question you asked ' +
        'and the answer you got. We\'ll review it and may follow up if we need ' +
        'more detail.'
      ))
      .addWidget(CardService.newTextButton()
        .setText('Ask another question')
        .setOnClickAction(CardService.newAction().setFunctionName('onAskAnother'))))
    .build();
}


function buildErrorCard_(err) {
  const message = (err && (err.message || err.toString())) || 'Unknown error';
  const section = CardService.newCardSection()
    .addWidget(CardService.newTextParagraph().setText(
      '<b>Something went wrong</b><br><br>' + escapeHtml_(message) +
      '<br><br><font color="#666666">If this keeps happening, contact IT.</font>'
    ));
  return CardService.newCardBuilder()
    .setHeader(buildCardHeader_())
    .addSection(section)
    .build();
}


// ---------------------------------------------------------------------------
// Vertex AI Agent Builder call
// ---------------------------------------------------------------------------

// Preamble matches eval/run_eval.py iter-2 (the configuration that passed
// the Phase 2 pass bar). Keep this in sync with the harness for regression
// re-runs after any change.
const PREAMBLE = (
  'You are a policy and procedures assistant for Rochester Hills Public Library (RHPL) staff. ' +
  'Your knowledge base contains the library\'s official policies and guidelines.\n\n' +
  'Before answering, mentally translate the staff member\'s question into library HR/policy vocabulary. Common mappings:\n' +
  '- "time off when someone dies" / "funeral leave" → "bereavement leave"\n' +
  '- "call in sick" / "sick day procedure" → "sick leave"\n' +
  '- "can I work from home" / "remote work" → "telework"\n' +
  '- "got hurt at work" / "on-the-job injury" → "workers compensation"\n' +
  '- "written up" / "in trouble" → "disciplinary action" / "work rules violation"\n' +
  '- "maternity leave" / "pregnancy leave" / "paternity leave" / "parental leave" → "Family and Medical Leave Act (FMLA)" / "leaves of absence"\n' +
  '- "if I serve in the military" / "deployed" → "military leave (USERRA)"\n\n' +
  'Use this translation to identify which policy a retrieved document is answering, even if the wording differs.\n\n' +
  '**If the literal phrase a staff member used isn\'t a policy heading, follow the umbrella policy.** ' +
  'Maternity leave is covered under FMLA, not in a standalone "maternity" document. ' +
  'Disciplinary action covers being "written up" even though no document is titled "written up." ' +
  'Synthesize across the related policies you retrieved.\n\n' +
  'When answering:\n' +
  '- Answer ONLY from the attached policy documents. Do not use outside knowledge or invent policy text.\n' +
  '- Cite the specific policy document name and number.\n' +
  '- Distinguish between a Policy (binding rule) and Guidelines (procedural guidance).\n' +
  '- If no policy in the knowledge base addresses the question — even adjacent ones — say so clearly. ' +
  'Don\'t refuse to engage; tell the staff member what *related* coverage you found, ' +
  'and direct them to the library director for the specific gap.\n' +
  '- Keep answers concise and practical for staff use.\n\n' +
  'Always write in American English.'
);

/** Query Vertex; returns { answer, sources: [{filename, title, snippet}] }. */
function askPolicies_(question) {
  // Per-user answer cache: avoid re-hitting Vertex for the same question
  // within an hour. Cache key includes the script-property CACHE_VERSION so
  // bumping it instantly invalidates every staff user's cached answer.
  const cache    = CacheService.getUserCache();
  const version  = getCacheVersion_();
  const cacheKey = 'ans_v' + version + '_' + sha256_(question);
  const cached   = cache.get(cacheKey);
  if (cached) {
    try { return JSON.parse(cached); }
    catch (e) { /* fall through */ }
  }

  const token = getServiceAccountToken_();
  const props = PropertiesService.getScriptProperties();
  const projectId = props.getProperty('GCP_PROJECT_ID');
  const engineId  = props.getProperty('VERTEX_ENGINE_ID');
  const servingId = props.getProperty('VERTEX_SERVING_CONFIG_ID') || 'default_search';

  if (!projectId || !engineId) {
    throw new Error('GCP_PROJECT_ID or VERTEX_ENGINE_ID not set in Script Properties.');
  }

  const url = 'https://discoveryengine.googleapis.com/v1' +
    '/projects/' + projectId +
    '/locations/global/collections/default_collection' +
    '/engines/' + engineId +
    '/servingConfigs/' + servingId + ':answer';

  const body = {
    query: { text: question },
    answerGenerationSpec: {
      modelSpec: { modelVersion: 'stable' },
      promptSpec: { preamble: PREAMBLE },
      includeCitations: true,
      ignoreAdversarialQuery: false,
      ignoreNonAnswerSeekingQuery: false,
    },
  };

  const resp = UrlFetchApp.fetch(url, {
    method:             'post',
    contentType:        'application/json',
    payload:            JSON.stringify(body),
    headers:            { Authorization: 'Bearer ' + token },
    muteHttpExceptions: true,
  });

  const code = resp.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error('Vertex AI :answer returned HTTP ' + code + ': ' + resp.getContentText());
  }
  const json = JSON.parse(resp.getContentText());
  const ans  = (json && json.answer) || {};

  const result = {
    answer:  (ans.answerText || '').trim(),
    sources: parseReferences_(ans.references || []),
  };

  // Cache answers for 1 hour. Don't cache empty answers (they're transient errors).
  if (result.answer) {
    cache.put(cacheKey, JSON.stringify(result), 3600);
  }
  return result;
}

/** Normalize Vertex references[] into a thin source list.
 *
 * Live-source engines (your-policies-datastore) include structData on each document
 * with drive_url + drive_file_id pointing at the Director's intranet Drive
 * file. We prefer that URL directly — it's authoritative and always reflects
 * the latest published version.
 *
 * For older engines without structData, we fall back to CITATION_MAP_DATA
 * keyed on the title/filename. */
function parseReferences_(refs) {
  const out = [];
  refs.forEach(function(ref) {
    const chunk = (ref && ref.chunkInfo) || {};
    const meta  = chunk.documentMetadata || {};
    const struct = meta.structData || {};
    const uri   = meta.uri || '';
    const title = struct.title || meta.title || '';
    let filename = uri ? uri.split('/').pop() : (struct.filename || '');
    if (!filename && title) filename = title;
    out.push({
      filename: filename,
      title: title || stripExt_(filename) || '(unknown)',
      driveUrl: struct.drive_url || null,     // live-source path
      driveFileId: struct.drive_file_id || null,
      snippet: chunk.content || '',
    });
  });
  return out;
}


// ---------------------------------------------------------------------------
// Citation → Drive URL lookup
// ---------------------------------------------------------------------------

/** Get the best Drive URL for a citation source.
 *
 * Preference order (first hit wins):
 *   1. source.driveUrl  → live-source engine sets this via structData.drive_url
 *   2. source.driveFileId → live-source engine sets this via structData.drive_file_id
 *   3. CITATION_MAP_DATA lookup by base filename → legacy engines without structData
 *
 * The legacy map handles both shapes:
 *   - flat strings (newer regen): `"...": "1KrQ..."`
 *   - {id, ext} objects (older regen): `"...": {"id": "1KrQ...", "ext": ".pdf"}`
 */
function citationToDriveUrl_(source) {
  if (source && source.driveUrl) return source.driveUrl;
  if (source && source.driveFileId) {
    return 'https://drive.google.com/file/d/' + source.driveFileId + '/view';
  }
  const filename = (source && source.filename) || '';
  if (!filename) return null;
  const map = getCitationMap_();
  const hit = map[stripExt_(filename)];
  if (!hit) return null;
  const fileId = (typeof hit === 'string') ? hit : hit.id;
  if (!fileId) return null;
  return 'https://drive.google.com/file/d/' + fileId + '/view';
}

/** Citation map lives in CitationMap.gs (as the constant CITATION_MAP_DATA)
 * because the data exceeds the 9 KB per-property limit on Script Properties.
 * Values are just Drive file IDs (strings), keyed by base filename. */
function getCitationMap_() {
  return (typeof CITATION_MAP_DATA !== 'undefined') ? CITATION_MAP_DATA : {};
}


// ---------------------------------------------------------------------------
// Service-account access token (cached 55 min in script cache)
// Lifted from patron-sync/gmail-addon/Code.gs (scope swapped to cloud-platform).
// ---------------------------------------------------------------------------
function getServiceAccountToken_() {
  const cache  = CacheService.getScriptCache();
  const cached = cache.get('sa_access_token_vertex');
  if (cached) return cached;

  const props   = PropertiesService.getScriptProperties();
  const saEmail = props.getProperty('SERVICE_ACCOUNT_EMAIL');
  const saKey   = (props.getProperty('SERVICE_ACCOUNT_KEY') || '').replace(/\\n/g, '\n');

  if (!saEmail || !saKey) {
    throw new Error('SERVICE_ACCOUNT_EMAIL or SERVICE_ACCOUNT_KEY not set in Script Properties.');
  }

  const now    = Math.floor(Date.now() / 1000);
  const header = Utilities.base64EncodeWebSafe(
    JSON.stringify({ alg: 'RS256', typ: 'JWT' })
  ).replace(/=+$/, '');
  const claim  = Utilities.base64EncodeWebSafe(JSON.stringify({
    iss:   saEmail,
    scope: 'https://www.googleapis.com/auth/cloud-platform',
    aud:   'https://oauth2.googleapis.com/token',
    exp:   now + 3600,
    iat:   now,
  })).replace(/=+$/, '');

  const toSign    = header + '.' + claim;
  const signature = Utilities.computeRsaSha256Signature(toSign, saKey);
  const jwt       = toSign + '.' + Utilities.base64EncodeWebSafe(signature).replace(/=+$/, '');

  const resp = UrlFetchApp.fetch('https://oauth2.googleapis.com/token', {
    method:             'post',
    payload:            { grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer', assertion: jwt },
    muteHttpExceptions: true,
  });
  const json = JSON.parse(resp.getContentText());
  if (!json.access_token) {
    throw new Error('Service account token exchange failed: ' + resp.getContentText());
  }
  cache.put('sa_access_token_vertex', json.access_token, 3300);
  return json.access_token;
}


// ---------------------------------------------------------------------------
// Cache version helpers (bulk invalidation pattern, same as patron-sync)
// ---------------------------------------------------------------------------
function getCacheVersion_() {
  return PropertiesService.getScriptProperties().getProperty('CACHE_VERSION') || '1';
}

/** Run from the Apps Script editor to flush every staff user's cached answers. */
function clearAllCaches() {
  const props = PropertiesService.getScriptProperties();
  const next  = String(parseInt(props.getProperty('CACHE_VERSION') || '1', 10) + 1);
  props.setProperty('CACHE_VERSION', next);
  CacheService.getScriptCache().remove('sa_access_token_vertex');
  console.log('Cache cleared. New CACHE_VERSION=' + next + '. Next answer view will refresh.');
}


// ---------------------------------------------------------------------------
// Sticky-answer stash (per-user, 15 min TTL)
// Lets the most recent answer survive Gmail navigation between the homepage
// and per-message trigger contexts.
// ---------------------------------------------------------------------------
const LAST_ANSWER_TTL_SEC = 900;

function stashLastAnswer_(question, result) {
  try {
    CacheService.getUserCache().put(
      'last_answer',
      JSON.stringify({ question: question, result: result, ts: Date.now() }),
      LAST_ANSWER_TTL_SEC
    );
  } catch (err) {
    console.warn('stashLastAnswer_ failed:', err);
  }
}

function getStashedAnswer_() {
  try {
    const raw = CacheService.getUserCache().get('last_answer');
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

function clearStashedAnswer_() {
  try {
    CacheService.getUserCache().remove('last_answer');
  } catch (err) { /* best-effort */ }
}

function formatAgo_(ts) {
  const mins = Math.floor(Math.max(0, Date.now() - ts) / 60000);
  if (mins < 1)  return 'just now';
  if (mins === 1) return '1 minute ago';
  if (mins < 60) return mins + ' minutes ago';
  const hrs = Math.floor(mins / 60);
  return hrs + (hrs === 1 ? ' hour ago' : ' hours ago');
}


// ---------------------------------------------------------------------------
// Audit logging
// ---------------------------------------------------------------------------

/** Console.log for v1; switch to a Sheet or Cloud Logging in Phase 4. */
function logQuery_(userEmail, question) {
  // Intentionally does NOT log the answer body — just who asked what + when.
  console.log(JSON.stringify({
    ts: new Date().toISOString(),
    type: 'query',
    user: userEmail,
    q: question,
  }));
}


/** Log feedback submissions for audit + analytics. The full feedback
 *  text is emailed to the director, but a structured log line also
 *  lands in Cloud Logging for searchability later. */
function logFeedback_(userEmail, question, feedback) {
  console.log(JSON.stringify({
    ts: new Date().toISOString(),
    type: 'feedback',
    user: userEmail,
    q: question,
    f: feedback,
  }));
}


/** Compose and send the feedback email to the configured recipient.
 *  Sent from the staff member's account (MailApp) so Reply goes back to
 *  them naturally. Subject starts with [RHPL Policies feedback] so it
 *  filters easily in Gmail. */
function sendFeedbackEmail_(userEmail, question, answer, feedback) {
  const props = PropertiesService.getScriptProperties();
  const recipient = props.getProperty('FEEDBACK_RECIPIENT_EMAIL') || 'derek.brown@rhpl.org';
  const tz = Session.getScriptTimeZone() || 'America/Detroit';
  const ts = Utilities.formatDate(new Date(), tz, "yyyy-MM-dd HH:mm z");

  const subject = '[RHPL Policies feedback] ' + (
    question.length > 70 ? question.substring(0, 67) + '...' : question);

  const body = [
    'A staff member submitted feedback through the RHPL Policies add-on.',
    '',
    'User:      ' + userEmail,
    'Timestamp: ' + ts,
    '',
    'Question asked:',
    '   ' + question,
    '',
    'Answer they received:',
    '   ' + (answer || '(no answer recorded)'),
    '',
    'What they were trying to find:',
    '   ' + feedback,
    '',
    '----------------------------------------------------------------',
    'Sent by the RHPL Policies Gmail Add-on. Reply to this email to',
    'follow up directly with the staff member.',
  ].join('\n');

  MailApp.sendEmail({ to: recipient, subject: subject, body: body });
}


// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

function stripExt_(filename) {
  if (!filename) return '';
  const i = filename.lastIndexOf('.');
  return i > 0 ? filename.substring(0, i) : filename;
}

function escapeHtml_(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Minimal markdown → CardService-supported HTML.
 * CardService accepts: <b>, <i>, <u>, <s>, <font color>, <br>, <a href>.
 * Anything else gets escaped as text. Bullet markers `- ` and `* ` survive
 * as plain text characters which is fine. */
function mdToCardHtml_(md) {
  if (!md) return '';
  let s = escapeHtml_(md);
  // Bold: **text** → <b>text</b>
  s = s.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  // Italic: *text* → <i>text</i> (after bold, to not collide)
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>');
  // Inline code: `text` → text in monospace span — CardService doesn't have
  // monospace; render as underlined.
  s = s.replace(/`([^`]+)`/g, '<u>$1</u>');
  // Newlines → <br>
  s = s.replace(/\n/g, '<br>');
  return s;
}

/** Stable-hash a question for cache keying. Apps Script has no crypto.subtle
 * but Utilities.computeDigest works. Hex-encoded SHA-256. */
function sha256_(s) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s);
  let hex = '';
  for (let i = 0; i < bytes.length; i++) {
    const b = (bytes[i] + 256) % 256;
    hex += (b < 16 ? '0' : '') + b.toString(16);
  }
  return hex;
}
