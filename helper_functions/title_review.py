"""LLM review of page titles that may overclaim (preview only, never writes to the DB).

Two batched passes over the local SQLite database (opened read-only):
  1. Screen: titles only, many per request. Each title is marked ok or flag.
  2. Rewrite: flagged titles only, sent with page content so the model can see
     what the page actually supports. Each is marked keep or rewrite.

Results go to a CSV (original title -> suggested title) for manual review.
"""

from __future__ import annotations

import csv
import json
import random
import re
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import requests
from django.utils.text import slugify

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
SITE_BASE_URL = "https://vitamindwiki.com"

SCREEN_BATCH_SIZE = 50       # titles per pass-1 request
REWRITE_BATCH_SIZE = 5       # pages per pass-2 request
CONTENT_EXCERPT_CHARS = 6000  # page text sent per page in pass 2
MAX_WORKERS = 4              # concurrent requests
MAX_ATTEMPTS = 4

_PAGE_LINK_RE = re.compile(r"/pages/([A-Za-z0-9_-]+)/?")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_QUANTIFIER_RE = re.compile(r"\b(all|some|none|no|every|most|many|few|not)\b", re.IGNORECASE)
_LABEL_RE = re.compile(r"\b(RCT|meta-analysis|trial|in mice|in rats|in cells|hypothesis)\b", re.IGNORECASE)
_DIRECTIONLESS_RE = re.compile(r"\b(associated with|linked to|and) vitamins? [A-Z]\b(?!\s*(level|deficiency|status))",
                               re.IGNORECASE)

SITE_CONTEXT = """\
VitaminDWiki is a long-running site that summarizes research on vitamin D (mostly D3) and \
related nutrients (magnesium, omega-3, etc.). Each page usually covers one study, review or \
topic, and its title states the headline finding in a plain, compact house style, for example:
  "Depression 30 percent less likely if more than 30 ng of vitamin D"
  "2.4 X fewer burn complications if have more than 20 ng of Vitamin D"
  "Liver Cancer 8 percent less likely for every 4 ng higher level of vitamin D – Meta-analysis"
Some titles overclaim relative to the underlying evidence, which hurts credibility and search \
ranking for health content. The goal is to fix only those, with the smallest possible change."""

ACCEPTABLE_PATTERNS = """\
ACCEPTABLE wording (not overclaims, even for observational studies):
- "N percent less likely if ...", "NX more likely if ...", "N percent fewer ...", "NX fewer ...", \
"lower risk", "higher risk", "associated with", "linked to", "had more/less ...". These describe \
a difference between groups, which is what observational studies show.
- Reporting what a trial found: "... reduced ... in an RCT", "... improved ... – RCT".
- Claims already labelled with their evidence (RCT, meta-analysis, "in mice", "hypothesis", \
"review", "interview") where the claim fits that label.
- Deficiency statistics, dosing information, overviews, questions, lists, news.
- "treat" / "prevent" used for purpose, dose or usage rather than as a finding: "dose needed to \
treat", "more vitamin D to treat than to prevent", "patent to treat", "vaccinations to prevent one \
hospitalization" (number needed to treat), "ways to treat".
- Negative results: "not prevented by ...", "fails to prevent", "did not treat" (only "proven" in \
such titles is a problem).
- "all" / "every" / "none" when they report a fact (e.g. "all mice died", "found in all samples", \
"40% of all produce", "every school shooter had used ...") or make a recommendation (e.g. "all \
patients should be tested").
- Statements about what raises or lowers vitamin D (or other nutrient) levels, absorption or \
activity: "Boron increases vitamin D", "shade reduces vitamin D", "corticosteroids decreased \
vitamin D levels". Also mechanisms, lab measurements, and pages marked "Off Topic".
- Idioms that are not health claims: "later proven wrong", "experts want more proof".
- The name of a book, paper, patent, video, program or article quoted in the title (e.g. \
"Vitamin D in Disease Prevention and Cure – Part I", "... Miracle Cure Book"). Never rename a \
work's own title.

SCOPE: Only claims about health outcomes are targets: disease, symptoms, recovery, death, or \
the risk of these. Everything else is out of scope.

OVERCLAIMS (the actual targets, all about health outcomes):
- Causal or curative verbs stated as settled fact: "prevents", "cures", "reverses", "stops", \
"treats", "eliminates", "fixes", "beats", "fights".
- "proven", "proof", "proves".
- Causal effect wording about a health outcome: "reduces/increases/cuts/lowers/raises risk of", \
"reduced/increased [disease or death]", "causes", "results in", "leads to" (e.g. "Red meat \
increases risk of Diabetes", "Zinc reduces the risk of ..."). Fine for an RCT or a meta-analysis \
of RCTs, which test cause and effect; an overclaim for observational studies, which should say \
"associated with", "linked to", "less/more likely", "lower/higher risk". Screen these as flags; \
the rewrite step checks the evidence type.
- Universal claims that generalize beyond the evidence: "all", "never", "always", "everyone", \
"no more" (e.g. "Vitamin D prevents all cancers").
- An animal, cell, or hypothesis result presented as an established human result.
- These still count when the claim is credited to a person or source (e.g. "... cured with \
Vitamin D – Dr. X", "... – Mercola"); attribution does not make an overclaim acceptable."""

SCREEN_INSTRUCTIONS = SITE_CONTEXT + "\n\n" + ACCEPTABLE_PATTERNS + """

TASK: You will receive a JSON list of page titles (title only, no content). Flag a title only \
if its wording contains one of the OVERCLAIMS above. Mark everything else ok, including titles \
in the acceptable house style. A later step reads flagged pages before anything changes, but do \
not flag titles just to be safe.

Return one result for every id you were given, using the same ids."""

REWRITE_INSTRUCTIONS = SITE_CONTEXT + "\n\n" + ACCEPTABLE_PATTERNS + """

TASK: You will receive a JSON list of pages whose titles were flagged by a quick title-only \
screen. Each has an id, the current title, the screening concern, and an excerpt of the page text \
(often starting with the study citation and abstract). The screen is often wrong; "keep" is an \
expected, normal answer.

For each page:
1. overclaim: quote the exact words in the current title that overclaim relative to the excerpt. \
If there are none (the wording is acceptable per the list above, or the page supports it), leave \
this empty and choose "keep".
2. action: "keep" or "rewrite". Only rewrite if you quoted an overclaim.
3. new_title: for "keep", the current title unchanged. For "rewrite", a MINIMAL edit of the \
current title.

Minimal edit rules:
- Change only the overclaiming words. Keep the author's structure, word order, voice, \
capitalization style, abbreviations (ng, IU, X) and every number that the excerpt supports.
- Typical fixes: "reduces risk of X" (observational) -> "linked to lower risk of X"; "increases X" \
(observational) -> "linked to more X"; "prevents" -> "associated with less" or "reduced ... in an RCT"; "treats" -> \
"helped treat ... in a trial" or "may help treat"; "proven" -> drop it or name the evidence \
("– RCT", "– meta-analysis"); add "in mice" / "in cells" for non-human results; "Hypothesis –" \
for speculation.
- Keep the direction and meaning of the finding. Never reduce a claim to a directionless \
"associated with vitamin D" or "and vitamin D": say which way ("lower risk", "fewer", "improved", \
"less likely", "helped"). Never reverse or negate the headline finding.
- Never change facts: counts, numbers, "all"/"some"/"none" that describe what happened.
- Only add an evidence label ("– RCT", "– meta-analysis", "in mice") when the excerpt clearly \
shows that is the evidence; never add one that does not match.
- For credited claims (a person, book, interview or outlet), keep the credit and soften the verb \
lightly (e.g. "cured" -> "helped", "slashes" -> "reduced").
- Keep the length within about 15 characters of the current title.
- Do not add commentary or judgements such as "unproven", "claim", "remains unclear", "more \
research needed", "individual needs vary". Do not argue with the page.
- Do not replace plain words with technical ones (keep "vitamin D", not "cholecalciferol" or \
"25(OH)D"; keep the condition names the author used). Do not add study acronyms or cohort names.
- Do not change the subject or headline finding of the title.
- Use "may" at most once, and only when there is no better precise wording.
- Do not invent numbers, study types, populations or doses that the excerpt does not support. \
Do not add a date. Hard limit 200 characters.

Return one result for every id you were given, using the same ids."""

SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["ok", "flag"]},
                    "concern": {
                        "type": "string",
                        "description": "If flagged, a few words on what may be overclaimed; empty if ok.",
                    },
                },
                "required": ["id", "verdict", "concern"],
            },
        }
    },
    "required": ["results"],
}

REWRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "integer"},
                    "overclaim": {
                        "type": "string",
                        "description": "Exact overclaiming words quoted from the current title; empty if none.",
                    },
                    "action": {"type": "string", "enum": ["keep", "rewrite"]},
                    "new_title": {"type": "string"},
                    "evidence_type": {
                        "type": "string",
                        "description": "Short label: RCT, meta-analysis, observational, animal, cell, "
                                       "review, hypothesis, opinion, news, unclear.",
                    },
                    "reason": {"type": "string", "description": "One short sentence."},
                },
                "required": ["id", "overclaim", "action", "new_title", "evidence_type", "reason"],
            },
        }
    },
    "required": ["results"],
}


class TitleReviewAbort(RuntimeError):
    """Raised when the API rejects the key/model/request, so the whole run should stop."""


@dataclass
class PageRow:
    id: int
    title: str
    slug: str
    content_text: str


def implied_tag_slugs(title: str, tag_slugs: List[str]) -> Set[str]:
    """Mirror of Page.update_derived_tags() title matching (see pages/models.py)."""
    title_slug = slugify(title)
    if not title_slug:
        return set()
    haystack = f"-{title_slug}-"
    return {slug for slug in tag_slugs if slug and f"-{slug}-" in haystack}


# rewrite_warnings() results that mean a fact changed; such rewrites are never applied.
FACT_WARNINGS = {"numbers changed", "all/some/no/not changed"}


def rewrite_warnings(old: str, new: str) -> List[str]:
    """Cheap sanity checks on a rewrite, to point the reviewer at risky rows."""
    warnings = []
    if Counter(_NUMBER_RE.findall(old)) != Counter(_NUMBER_RE.findall(new)):
        warnings.append("numbers changed")
    old_q = Counter(w.lower() for w in _QUANTIFIER_RE.findall(old))
    new_q = Counter(w.lower() for w in _QUANTIFIER_RE.findall(new))
    if old_q["all"] > new_q["all"] and not new_q["some"]:
        old_q.pop("all")  # dropping an overclaiming "all" is expected; "all" -> "some" is not
    if old_q != new_q:
        warnings.append("all/some/no/not changed")
    if {m.lower() for m in _LABEL_RE.findall(new)} - {m.lower() for m in _LABEL_RE.findall(old)}:
        warnings.append("evidence label added")
    if _DIRECTIONLESS_RE.search(new) and not _DIRECTIONLESS_RE.search(old):
        warnings.append("no direction")
    if abs(len(new) - len(old)) > 15:
        warnings.append("length")
    return warnings


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def select_pages(conn: sqlite3.Connection, limit: Optional[int], seed: int) -> List[PageRow]:
    """Random sample of `limit` published pages, or all of them if limit is None."""
    rows = conn.execute(
        "SELECT id, title, slug, content_text FROM posts_post WHERE status = 'published'"
    ).fetchall()
    pages = [PageRow(r["id"], r["title"], r["slug"], r["content_text"] or "") for r in rows]
    if limit is None or limit >= len(pages):
        return pages
    return random.Random(seed).sample(pages, limit)


def load_previous_review(csv_path: Path) -> Dict[int, dict]:
    """Page id -> row from an earlier review CSV, so a rerun uses the same pages."""
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        return {int(row["page_id"]): row for row in csv.DictReader(fh)}


def select_pages_by_id(conn: sqlite3.Connection, page_ids: List[int]) -> List[PageRow]:
    placeholders = ",".join("?" * len(page_ids))
    rows = conn.execute(
        f"SELECT id, title, slug, content_text FROM posts_post WHERE id IN ({placeholders})", page_ids
    ).fetchall()
    return [PageRow(r["id"], r["title"], r["slug"], r["content_text"] or "") for r in rows]


def _chunks(items: list, size: int) -> List[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _extract_output_text(data: dict) -> str:
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise RuntimeError(f"model refused: {part.get('refusal')}")
            if part.get("type") == "output_text":
                return part["text"]
    raise RuntimeError(f"no output_text in response (status={data.get('status')})")


def _call_structured(
    session: requests.Session,
    api_key: str,
    model: str,
    instructions: str,
    user_input: str,
    schema_name: str,
    schema: dict,
) -> dict:
    payload = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = session.post(OPENAI_RESPONSES_URL, headers=headers, json=payload, timeout=300)
        if response.status_code in (400, 401, 403, 404):
            raise TitleReviewAbort(f"HTTP {response.status_code}: {response.text[:500]}")
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"HTTP {response.status_code} after {attempt} attempts")
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        return json.loads(_extract_output_text(response.json()))
    raise AssertionError("unreachable")


def _run_batches(
    label: str,
    batches: List[List[PageRow]],
    handler: Callable[[List[PageRow]], Dict[int, dict]],
) -> Dict[int, dict]:
    """Run handler over batches concurrently. Failed batches mark their pages as errors."""
    results: Dict[int, dict] = {}
    if not batches:
        print(f"  {label}: nothing to do")
        return results

    total_pages = sum(len(b) for b in batches)
    started = time.monotonic()
    done_batches = 0
    done_pages = 0
    failed_batches = 0

    def report() -> None:
        elapsed = time.monotonic() - started
        line = (f"  {label}: {done_batches}/{len(batches)} batches, "
                f"{done_pages}/{total_pages} pages, {elapsed:.0f}s elapsed")
        if 1 < done_batches < len(batches):  # batch 1 runs alone, so it would skew the estimate
            remaining = elapsed / done_batches * (len(batches) - done_batches)
            line += f", ~{remaining:.0f}s left"
        if failed_batches:
            line += f", {failed_batches} failed"
        print(line, flush=True)

    # First batch alone so a bad key / model name fails fast.
    print(f"  {label}: sending first batch ({len(batches[0])} pages) to check key/model...", flush=True)
    results.update(handler(batches[0]))
    done_batches, done_pages = 1, len(batches[0])
    report()
    if len(batches) > 1:
        print(f"  {label}: sending remaining {len(batches) - 1} batches, "
              f"{MAX_WORKERS} at a time...", flush=True)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(handler, batch): batch for batch in batches[1:]}
        try:
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    results.update(future.result())
                except TitleReviewAbort:
                    raise
                except Exception as exc:  # keep going; record the failure in the CSV
                    failed_batches += 1
                    print(f"  {label}: batch failed: {str(exc)[:200]}", flush=True)
                    for page in batch:
                        results[page.id] = {"error": str(exc)[:300]}
                done_batches += 1
                done_pages += len(batch)
                report()
        except TitleReviewAbort:
            for f in futures:
                f.cancel()
            raise
    return results


def _results_by_id(batch: List[PageRow], raw: dict) -> Dict[int, dict]:
    expected = {p.id for p in batch}
    out = {r["id"]: r for r in raw["results"] if r["id"] in expected}
    for page_id in expected - out.keys():
        out[page_id] = {"error": "missing from model response"}
    return out


def screen_titles(session, api_key: str, model: str, batch: List[PageRow]) -> Dict[int, dict]:
    user_input = json.dumps([{"id": p.id, "title": p.title} for p in batch], ensure_ascii=False)
    raw = _call_structured(session, api_key, model, SCREEN_INSTRUCTIONS, user_input,
                           "title_screen", SCREEN_SCHEMA)
    return _results_by_id(batch, raw)


def rewrite_titles(session, api_key: str, model: str, batch: List[PageRow],
                   concerns: Dict[int, str]) -> Dict[int, dict]:
    user_input = json.dumps(
        [
            {
                "id": p.id,
                "title": p.title,
                "screening_concern": concerns.get(p.id, ""),
                "content_excerpt": p.content_text[:CONTENT_EXCERPT_CHARS],
            }
            for p in batch
        ],
        ensure_ascii=False,
    )
    raw = _call_structured(session, api_key, model, REWRITE_INSTRUCTIONS, user_input,
                           "title_rewrite", REWRITE_SCHEMA)
    results = _results_by_id(batch, raw)
    titles = {p.id: p.title for p in batch}
    for page_id, res in results.items():
        if "error" in res:
            continue
        new_title = res["new_title"].strip()
        if (res["action"] == "keep" or not res["overclaim"].strip() or not new_title
                or new_title == titles[page_id].strip()):
            res["action"] = "keep"
            res["new_title"] = titles[page_id]
        else:
            res["new_title"] = new_title[:200]
            fact_changes = [w for w in rewrite_warnings(titles[page_id], res["new_title"])
                            if w in FACT_WARNINGS]
            if fact_changes:
                # Keep the page's title; the suggestion is still shown in the CSV for review.
                res["action"] = "blocked"
                res["reason"] = f"auto-blocked ({', '.join(fact_changes)}): {res['reason']}"
    return results


def run_title_review(
    db_path: Path,
    output_dir: Path,
    api_key: str,
    model: str,
    limit: Optional[int],
    seed: Optional[int] = None,
    previous_csv: Optional[Path] = None,
) -> Path:
    """Review a random sample of `limit` pages (all if None), or the pages in `previous_csv`."""
    assert db_path.exists(), f"database not found: {db_path}"
    assert limit is None or limit > 0, "limit must be positive"
    seed = seed if seed is not None else int(time.time())
    previous = load_previous_review(previous_csv) if previous_csv else {}

    conn = _connect_readonly(db_path)
    try:
        pages = select_pages_by_id(conn, list(previous)) if previous else select_pages(conn, limit, seed)
        tag_slugs = [r["slug"] for r in conn.execute("SELECT slug FROM tags_tag")]
        existing_slugs = {r["slug"] for r in conn.execute("SELECT slug FROM posts_post")}
        explicit_tags: Dict[int, Set[str]] = {}
        for r in conn.execute(
            "SELECT pt.page_id, t.slug FROM posts_post_tags pt JOIN tags_tag t ON t.id = pt.tag_id"
        ):
            explicit_tags.setdefault(r["page_id"], set()).add(r["slug"])
        # Number of distinct pages linking to each slug.
        inbound: Counter = Counter()
        for r in conn.execute("SELECT content_md FROM posts_post"):
            for linked_slug in set(_PAGE_LINK_RE.findall(r["content_md"] or "")):
                inbound[linked_slug] += 1
    finally:
        conn.close()

    if previous:
        print(f"\nRe-reviewing {len(pages)} pages from {previous_csv.name}.")
    else:
        how = "all published pages" if limit is None else f"random sample, seed {seed}"
        print(f"\nSelected {len(pages)} pages ({how}).")
    session = requests.Session()

    print(f"Pass 1: screening titles ({SCREEN_BATCH_SIZE} per request)...")
    screen = _run_batches(
        "screen",
        _chunks(pages, SCREEN_BATCH_SIZE),
        lambda batch: screen_titles(session, api_key, model, batch),
    )
    flagged = [p for p in pages if screen[p.id].get("verdict") == "flag"]
    print(f"  {len(flagged)} flagged, "
          f"{sum(1 for p in pages if screen[p.id].get('verdict') == 'ok')} ok, "
          f"{sum(1 for p in pages if 'error' in screen[p.id])} error")

    print(f"Pass 2: reviewing flagged titles with page content ({REWRITE_BATCH_SIZE} per request)...")
    concerns = {p.id: screen[p.id].get("concern", "") for p in flagged}
    rewrite = _run_batches(
        "rewrite",
        _chunks(flagged, REWRITE_BATCH_SIZE),
        lambda batch: rewrite_titles(session, api_key, model, batch, concerns),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"title_review_{datetime.now():%Y%m%d_%H%M%S}.csv"
    fieldnames = [
        "page_id", "result", "original_title", "suggested_title", "overclaim", "reason",
        "evidence_type", "screen_concern", "check", "length_change", "old_slug", "new_slug", "slug_conflict",
        "inbound_links", "tags_added", "tags_removed", "url",
    ]

    def result_of(page: PageRow) -> str:
        s = screen[page.id]
        if "error" in s:
            return "screen_error"
        if s["verdict"] == "ok":
            return "ok"
        r = rewrite.get(page.id, {"error": "not reviewed"})
        return "rewrite_error" if "error" in r else r["action"]

    if previous:
        fieldnames[4:4] = ["previous_result", "previous_suggestion"]

    counts: Counter = Counter()
    order = {"rewrite": 0, "blocked": 1, "keep": 2, "rewrite_error": 3, "screen_error": 4, "ok": 5}
    with out_path.open("w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig so Excel shows dashes
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for page in sorted(pages, key=lambda p: (order[result_of(p)], p.id)):
            result = result_of(page)
            counts[result] += 1
            s = screen[page.id]
            r = rewrite.get(page.id, {})
            row = {
                "page_id": page.id,
                "result": result,
                "original_title": page.title,
                "suggested_title": r.get("new_title", "") if result in ("rewrite", "blocked") else "",
                "reason": r.get("reason") or r.get("error") or s.get("error", ""),
                "evidence_type": r.get("evidence_type", ""),
                "overclaim": r.get("overclaim", ""),
                "screen_concern": s.get("concern", ""),
                "old_slug": page.slug,
                "inbound_links": inbound.get(page.slug, 0),
                "url": f"{SITE_BASE_URL}/pages/{page.slug}/",
            }
            if previous:
                prev = previous.get(page.id, {})
                row["previous_result"] = prev.get("result", "")
                row["previous_suggestion"] = prev.get("suggested_title", "")
            if result == "blocked":
                row["check"] = "; ".join(rewrite_warnings(page.title, r["new_title"]))
            if result == "rewrite":
                new_slug = slugify(r["new_title"])
                explicit = explicit_tags.get(page.id, set())
                old_implied = implied_tag_slugs(page.title, tag_slugs) - explicit
                new_implied = implied_tag_slugs(r["new_title"], tag_slugs) - explicit
                row.update({
                    "check": "; ".join(rewrite_warnings(page.title, r["new_title"])),
                    "length_change": len(r["new_title"]) - len(page.title),
                    "new_slug": new_slug,
                    "slug_conflict": "yes" if new_slug != page.slug and new_slug in existing_slugs else "",
                    "tags_added": " ".join(sorted(new_implied - old_implied)),
                    "tags_removed": " ".join(sorted(old_implied - new_implied)),
                })
            writer.writerow(row)

    print("Results: " + ", ".join(f"{counts[k]} {k}" for k in order if counts[k]))
    return out_path
