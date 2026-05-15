"""
Side-by-side evaluation harness for the RHPL Policies & Procedures RAG.

Loads questions.yaml, runs each question against the OWUI backend (local Qwen3-14B
with the your-policies-model model) and the Vertex AI Agent Builder app
(Discovery Engine Conversational Search), captures answer text + citations +
latency for both, and writes a side-by-side markdown report for manual scoring.

OWUI side is complete. Vertex side is stubbed until the Phase 1 datastore exists —
set VERTEX_DATA_STORE_ID in .env to enable it.

Usage:
  cp ../.env.template ../.env  # then fill in OWUI_API_KEY (and later Vertex IDs)
  python3 run_eval.py                # both backends if both configured
  python3 run_eval.py --owui-only    # run OWUI only (Phase 0/1 state)
  python3 run_eval.py --vertex-only  # run Vertex only
  python3 run_eval.py --question F1  # single question
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time  # noqa: F401  (used inside call_vertex)
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = REPO_ROOT / "eval" / "questions.yaml"


# ---------------------------------------------------------------------------
# OWUI backend (existing Qwen3-14B + RAG via Open WebUI)
# ---------------------------------------------------------------------------

def call_owui(question: str) -> dict:
    """Call the OWUI chat completions endpoint for the policies model."""
    base = os.environ["OWUI_BASE_URL"].rstrip("/")
    api_key = os.environ["OWUI_API_KEY"]
    model = os.environ.get("OWUI_MODEL_ID", "your-policies-model")

    body = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    started = time.time()
    r = requests.post(f"{base}/api/chat/completions", json=body, headers=headers, timeout=120)
    elapsed_ms = int((time.time() - started) * 1000)
    r.raise_for_status()
    payload = r.json()

    msg = payload.get("choices", [{}])[0].get("message", {}) or {}
    answer = (msg.get("content") or "").strip()

    # OWUI returns retrieved sources at the top level. Each entry has:
    #   { "source": {"id","name","type"}, "document": [chunk_text, ...],
    #     "metadata": [{...per-chunk metadata incl. source filename...}] }
    # The most useful identifier is metadata[i]["source"] or metadata[i]["name"]
    # (the actual document filename); fall back to source.name (the KB name).
    norm_sources = _normalize_owui_sources(payload.get("sources") or [])

    # Citations the model wrote inline (e.g. "Per CIRC-2 Loan and Renewal Policy...")
    inline_citations = parse_inline_citations(answer)
    refusal = looks_like_refusal(answer)

    return {
        "backend": "owui",
        "answer": answer,
        "sources": norm_sources,
        "inline_citations": inline_citations,
        "refusal": refusal,
        "latency_ms": elapsed_ms,
        "raw": payload,
    }


def _normalize_owui_sources(raw_sources: list) -> list[dict]:
    """Flatten OWUI's nested sources/chunks structure into one dict per chunk."""
    out = []
    for s in raw_sources:
        if not isinstance(s, dict):
            out.append({"source": str(s)[:200], "snippet": ""})
            continue
        kb_name = ""
        src = s.get("source")
        if isinstance(src, dict):
            kb_name = src.get("name") or ""
        docs = s.get("document") or []
        metas = s.get("metadata") or []
        n = max(len(docs), len(metas))
        for i in range(n):
            chunk_text = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            if not isinstance(meta, dict):
                meta = {}
            doc_name = (
                meta.get("source")
                or meta.get("name")
                or meta.get("file_id")
                or kb_name
                or "(unknown)"
            )
            snippet = (chunk_text or "").strip().replace("\n", " ")[:240]
            out.append({"source": str(doc_name)[:200], "snippet": snippet})
        if n == 0:
            out.append({"source": str(kb_name)[:200] or "(no sources)", "snippet": ""})
    return out


# ---------------------------------------------------------------------------
# Vertex AI Agent Builder backend (Discovery Engine Conversational Search)
#
# STUB: filled in after Phase 1 creates the data store + serving config.
# ---------------------------------------------------------------------------

_VERTEX_PREAMBLE = """You are a policy and procedures assistant for Rochester Hills Public Library (RHPL) staff. Your knowledge base contains the library's official policies and guidelines.

Before answering, mentally translate the staff member's question into library HR/policy vocabulary. Common mappings:
- "time off when someone dies" / "funeral leave" → "bereavement leave"
- "call in sick" / "sick day procedure" → "sick leave"
- "can I work from home" / "remote work" → "telework"
- "got hurt at work" / "on-the-job injury" → "workers compensation"
- "written up" / "in trouble" → "disciplinary action" / "work rules violation"
- "maternity leave" / "pregnancy leave" / "paternity leave" / "parental leave" → "Family and Medical Leave Act (FMLA)" / "leaves of absence"
- "if I serve in the military" / "deployed" → "military leave (USERRA)"

Use this translation to identify which policy a retrieved document is answering, even if the wording differs.

**If the literal phrase a staff member used isn't a policy heading, follow the umbrella policy.** Maternity leave is covered under FMLA, not in a standalone "maternity" document. Disciplinary action covers being "written up" even though no document is titled "written up." Synthesize across the related policies you retrieved.

When answering:
- Answer ONLY from the attached policy documents. Do not use outside knowledge or invent policy text.
- Cite the specific policy document name and number (e.g., "Per CIRC-2 Loan and Renewal Policy..." or "Per BENR-8 Leaves of Absence (FMLA) Policy...").
- Distinguish between a Policy (binding rule) and Guidelines (procedural guidance).
- If no policy in the knowledge base addresses the question — even adjacent ones — say so clearly. Don't refuse to engage; tell the staff member what *related* coverage you found, and direct them to the library director for the specific gap. Example: "There is no policy specifically about hanging up on abusive callers. The closest related guidance is in [policy]. For specific situations, contact the library director."
- Keep answers concise and practical for staff use.

Always write in American English ("color" not "colour", "organize" not "organise")."""


_VERTEX_SESSION_CACHE = {"session": None}


def _vertex_session():
    """Lazy-build a Google AuthorizedSession from the SA key file."""
    if _VERTEX_SESSION_CACHE["session"]:
        return _VERTEX_SESSION_CACHE["session"]
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    key_path = os.environ.get("VERTEX_SA_KEY_PATH") or os.path.expanduser(
        "~/.config/policies-addon/sa-key.json"
    )
    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    sess = AuthorizedSession(creds)
    _VERTEX_SESSION_CACHE["session"] = sess
    return sess


def call_vertex(question: str) -> dict:
    """Call Vertex AI Discovery Engine `:answer` endpoint for the policies engine.

    Returns the same dict shape as call_owui() so the report renderer doesn't
    need to know which backend produced a row.
    """
    project = os.environ["GCP_PROJECT_ID"]
    engine = os.environ.get("VERTEX_ENGINE_ID", "your-policies")
    serving = os.environ.get("VERTEX_SERVING_CONFIG_ID", "default_search")

    url = (
        f"https://discoveryengine.googleapis.com/v1"
        f"/projects/{project}/locations/global/collections/default_collection"
        f"/engines/{engine}/servingConfigs/{serving}:answer"
    )
    body = {
        "query": {"text": question},
        "answerGenerationSpec": {
            "modelSpec": {"modelVersion": "stable"},
            "promptSpec": {"preamble": _VERTEX_PREAMBLE},
            "includeCitations": True,
            # Iteration 2: disable adversarial-query filter. In iteration 1 it ate
            # F7 ("policy on hanging up on abusive calls?") with "summary could
            # not be generated." Letting the model answer honestly per the
            # preamble is preferable.
            "ignoreAdversarialQuery": False,
            "ignoreNonAnswerSeekingQuery": False,
        },
    }

    session = _vertex_session()
    started = time.time()
    r = session.post(url, json=body, timeout=120)
    elapsed_ms = int((time.time() - started) * 1000)
    r.raise_for_status()
    payload = r.json()

    ans = payload.get("answer") or {}
    answer = (ans.get("answerText") or "").strip()

    # references[] each contain { chunkInfo: { content, documentMetadata } }
    norm_sources = []
    for ref in ans.get("references") or []:
        chunk = ref.get("chunkInfo") or {}
        meta = chunk.get("documentMetadata") or {}
        title = meta.get("title") or ""
        page = meta.get("pageIdentifier")
        uri = meta.get("uri") or ""
        # Prefer the human-readable filename; fall back to title/uri.
        filename = uri.rsplit("/", 1)[-1] if uri else title
        label = filename or "(unknown)"
        if page:
            label = f"{label} (page {page})"
        snippet = (chunk.get("content") or "").strip().replace("\n", " ")[:240]
        norm_sources.append({"source": label, "snippet": snippet})

    return {
        "backend": "vertex",
        "answer": answer,
        "sources": norm_sources,
        "inline_citations": parse_inline_citations(answer),
        "refusal": looks_like_refusal(answer),
        "latency_ms": elapsed_ms,
        "raw": payload,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# RHPL policies use prefixes like CIRC-2, HR-3, PERS-1, etc.
_CITATION_RE = re.compile(r"\b([A-Z]{2,6})-(\d+[A-Z]?)\b")

# Per the system prompt, refusals say things like "not in the knowledge base" or
# "contact the library director" — we use a few phrases as a coarse signal.
_REFUSAL_MARKERS = (
    "not in the knowledge base",
    "is not covered",
    "do not have",
    "could not find",
    "contact the library director",
    "speak with the library director",
    "ask the library director",
    "is not available in the policy",
    "no policy",
    "i'm not able to",
    "i am not able to",
    "i cannot answer",
    "i can't answer",
    "outside the scope",
)


def parse_inline_citations(text: str) -> list[str]:
    if not text:
        return []
    seen = set()
    out = []
    for m in _CITATION_RE.finditer(text):
        key = f"{m.group(1)}-{m.group(2)}"
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def looks_like_refusal(text: str) -> bool:
    if not text:
        return True
    lower = text.lower()
    return any(marker in lower for marker in _REFUSAL_MARKERS)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def load_questions(path: Path) -> list[dict]:
    with path.open() as f:
        data = yaml.safe_load(f)
    return data["questions"]


def run(questions: list[dict], backends: list[str]) -> list[dict]:
    runners = {"owui": call_owui, "vertex": call_vertex}
    results = []
    for q in questions:
        row = {"question": q, "results": {}}
        for backend in backends:
            print(f"  [{backend:6s}] {q['id']:3s}  {q['text'][:80]}", file=sys.stderr)
            try:
                row["results"][backend] = runners[backend](q["text"])
            except Exception as e:
                row["results"][backend] = {
                    "backend": backend,
                    "error": f"{type(e).__name__}: {e}",
                    "answer": "",
                    "sources": [],
                    "inline_citations": [],
                    "refusal": False,
                    "latency_ms": None,
                }
        results.append(row)
    return results


def write_report(results: list[dict], out_dir: Path) -> tuple[Path, Path]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md_path = out_dir / f"results-{ts}.md"
    json_path = out_dir / f"results-{ts}.json"

    json_path.write_text(json.dumps(results, indent=2, default=str))

    backends_present = sorted({b for row in results for b in row["results"]})

    lines = [
        f"# Side-by-side eval — {ts}",
        "",
        f"Backends compared: **{', '.join(backends_present)}**",
        f"Questions: **{len(results)}**",
        "",
        "Score each row manually in the table below:",
        "- `fact` (1-5): factual accuracy vs. the policy text",
        "- `cite` (1-5): citation accuracy (correct policy, human-verifiable)",
        "- `ref?` (✓/✗/—): refusal correctness — ✓ if it correctly refused or "
        "answered, ✗ if it got refusal wrong, — if N/A",
        "- `hall` (✓/✗): hallucination present? ✓ = clean, ✗ = hallucinated",
        "",
    ]

    for row in results:
        q = row["question"]
        lines.append(f"---")
        lines.append(f"## {q['id']} · `{q['category']}` · {q['text']}")
        lines.append("")
        meta_bits = []
        if q.get("expected_topic"):
            meta_bits.append(f"**Expected topic:** {q['expected_topic']}")
        if q.get("expected_translation"):
            meta_bits.append(f"**Expected translation:** {q['expected_translation']}")
        if q.get("expected_policy_area"):
            meta_bits.append(f"**Expected policy area:** {q['expected_policy_area']}")
        if q.get("refusal_reason"):
            meta_bits.append(f"**Refusal reason:** {q['refusal_reason']}")
        if meta_bits:
            lines.extend(meta_bits)
            lines.append("")

        for backend in backends_present:
            r = row["results"].get(backend, {})
            lines.append(f"### {backend.upper()}")
            if r.get("error"):
                lines.append(f"_ERROR: {r['error']}_")
                lines.append("")
                continue
            lat = r.get("latency_ms")
            lines.append(f"_latency: {lat} ms · refusal-detected: "
                         f"{'yes' if r.get('refusal') else 'no'} · "
                         f"inline citations: {', '.join(r.get('inline_citations') or []) or '(none)'}_")
            lines.append("")
            ans = (r.get("answer") or "").strip()
            if ans:
                lines.append("```")
                lines.append(ans[:3000])
                if len(ans) > 3000:
                    lines.append(f"... [truncated, {len(ans)} chars total]")
                lines.append("```")
            else:
                lines.append("_(no answer text)_")
            srcs = r.get("sources") or []
            if srcs:
                lines.append("")
                lines.append(f"**Sources retrieved ({len(srcs)}):**")
                for s in srcs[:6]:
                    name = s.get("source", str(s))
                    snippet = s.get("snippet") or ""
                    if snippet:
                        lines.append(f"- `{name}` — {snippet}")
                    else:
                        lines.append(f"- `{name}`")
                if len(srcs) > 6:
                    lines.append(f"- _(+{len(srcs) - 6} more)_")
            lines.append("")

        # scoring stub the reviewer fills in
        lines.append("**Score:**")
        lines.append("")
        lines.append("| backend | fact (1-5) | cite (1-5) | ref? | hall | notes |")
        lines.append("|---|---|---|---|---|---|")
        for backend in backends_present:
            lines.append(f"| {backend} |   |   |   |   |   |")
        lines.append("")

    md_path.write_text("\n".join(lines))
    return md_path, json_path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--owui-only", action="store_true", help="Run only the OWUI backend")
    p.add_argument("--vertex-only", action="store_true", help="Run only the Vertex backend")
    p.add_argument("--question", help="Run only the question with this id (e.g. F1)")
    args = p.parse_args()

    backends = ["owui", "vertex"]
    if args.owui_only:
        backends = ["owui"]
    elif args.vertex_only:
        backends = ["vertex"]

    if "owui" in backends and not os.environ.get("OWUI_API_KEY"):
        print("ERROR: OWUI_API_KEY not set. Copy .env.template to .env and fill it in.",
              file=sys.stderr)
        return 2
    if "vertex" in backends and not os.environ.get("VERTEX_DATA_STORE_ID"):
        print("NOTE: VERTEX_DATA_STORE_ID not set — Vertex side not configured yet. "
              "Pass --owui-only to skip.", file=sys.stderr)
        if not args.owui_only:
            return 2

    questions = load_questions(QUESTIONS_PATH)
    if args.question:
        questions = [q for q in questions if q["id"] == args.question]
        if not questions:
            print(f"No question with id {args.question}", file=sys.stderr)
            return 2

    print(f"Running {len(questions)} question(s) against: {', '.join(backends)}",
          file=sys.stderr)
    results = run(questions, backends)

    out_dir = QUESTIONS_PATH.parent
    md_path, json_path = write_report(results, out_dir)
    print(f"\nReport:  {md_path}")
    print(f"JSON:    {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
