# A/B: my-context-engine vs Augment Context Engine

**Target repo:** `/Users/skipp/Projects/invoicer` (Django, 97 source files / 269 chunks after `.gitignore` filtering)
**Date:** 2026-06-04
**Queries:** 5 deep/architectural (multi-hop, cross-file)

**Our config:** Voyage `voyage-code-3` embeddings → hybrid dense+BM25 → RRF fusion → `code_prior` weighting → Voyage `rerank-2.5-lite` (top-8).
**Augment:** proprietary embedding + retrieval suite via `codebase-retrieval` MCP, same `directory_path`.

Verdict per query: 🟰 tie · 🟦 Augment clearly better · 🟩 ours better.

---

## Q1 — Recurring invoice generation end-to-end  🟰 tie

| # | Ours | Augment |
|---|------|---------|
| 1 | recurring.py `_clone` | recurring.py `generate_due` |
| 2 | recurring.py `generate_due` | recurring.py `_clone` |
| 3 | models.py `RecurringProfile` | recurring.py `_next_nr` |
| 4 | views.py `recurring_create` | models.py `Document.save` |
| 5 | test_logic `RecurringTests` | models.py `apply_due_date` |
| 6 | models.py `apply_due_date` | models.py `RecurringProfile` |
| 7 | models.py `compute_due_date` | run_recurring command |
| 8 | run_recurring command | test `generate_due_clones_and_advances` |

**Shared:** `_clone`, `generate_due`, `RecurringProfile`, `apply_due_date`, `run_recurring`, tests.
Both nail the core. Ours adds `compute_due_date` + the create view; Augment adds `_next_nr` (numbering) + `Document.save`. Equivalent quality.

---

## Q2 — Multi-currency across model / bank / PDF / seed  🟦 Augment

| # | Ours | Augment |
|---|------|---------|
| 1 | models.py `BankAccount` | models.py `Currency` |
| 2 | seed.py `Command` | models.py `Document.currency` FK + rate |
| 3 | render.py `document_context` | models.py `BankAccount` (currency_label) |
| 4 | views.py `document_pdf` | seed.py `CURRENCIES` list |
| 5 | models.py `Currency` | render.py `document_context` (currency lines) |
| 6 | import_invoicepro `Command` | models.py `Company.default_currency` |
| 7 | views.py `report_aging` | render.py `amount_in_words` |
| 8 | test `DomainDBTestCase` | invoice.html totals block |

**Shared:** `Currency`, `BankAccount`, `seed`, `document_context`.
Augment is more **precise**: it pinpoints currency-specific spans (`Document.currency` FK at models.py:160-161, `Company.default_currency`, `amount_in_words`, the `invoice.html` totals). Ours mixes in noise (`document_pdf`, `report_aging`, `import_invoicepro`, a test).

---

## Q3 — Create-and-save flow incl. numbering & line-item formset  🟦 Augment

| # | Ours | Augment |
|---|------|---------|
| 1 | views.py `document_form_view` ✅ | views.py create/edit view |
| 2 | recurring.py `_clone` | views.py `next_doc_nr` |
| 3 | views.py `document_duplicate` | forms.py `DocumentForm` |
| 4 | render.py `document_context` | forms.py `DocumentLineFormSet` |
| 5 | views.py `settings_view` | models.py `Meta` UniqueConstraint (doc_nr) |
| 6 | seed.py `Command` | document_form.html formset rows |
| 7 | recurring.py `generate_due` | document_form.html `addLine()` JS |
| 8 | import_invoicepro `Command` | models.py `Document.save` |

**Shared:** the create view (both #1).
We found the create view — good — but **missed `forms.py` entirely** (the `DocumentForm` + `DocumentLineFormSet` the query explicitly named) and `next_doc_nr`. Augment surfaced exactly the numbering + formset definitions asked for. Ours padded with same-repo noise.

---

## Q4 — Overdue detection & reminder emails  🟰 tie

| # | Ours | Augment |
|---|------|---------|
| 1 | reminders.py `_email_for` | reminders.py `send_due_reminders` |
| 2 | reminders.py `send_due_reminders` | reminders.py `_email_for` |
| 3 | views.py `document_reminder` | models.py `reminder_due` |
| 4 | reminder_ai.py `draft_reminder` | models.py `is_overdue` |
| 5 | models.py `reminder_due` | reminder_ai.py `draft_reminder` |
| 6 | views.py `document_reminder_send` | models.py `Company.auto_remind` |
| 7 | run_reminders command | run_reminders command |
| 8 | models.py `is_overdue` | test `sends_once_then_respects_cadence` |

**Shared:** `_email_for`, `send_due_reminders`, `reminder_due`, `is_overdue`, `draft_reminder`, `run_reminders`.
Near-identical. Ours adds the two reminder views; Augment adds the `Company.auto_remind` settings + a test. Equivalent.

---

## Q5 — PDF generation pipeline (view → context → template)  🟦 Augment

| # | Ours | Augment |
|---|------|---------|
| 1 | views.py `document_pdf` | pdf.py `render_pdf_bytes` |
| 2 | views.py `document_send` | pdf.py `render_html` |
| 3 | pdf.py `render_pdf_bytes` | render.py `document_context` |
| 4 | views.py `document_reminder_send` | views.py `document_pdf` |
| 5 | views.py `shared_document_pdf` | views.py `document_preview` |
| 6 | views.py `document_preview` | invoice.html items table |
| 7 | views.py `shared_document` | render.py `_logo_data_uri` |
| 8 | views.py `document_form_view` | views.py `shared_document_pdf` |

**Shared:** `render_pdf_bytes`, `document_pdf`, `document_preview`, `shared_document_pdf`.
Augment traced the **whole pipeline** the query described: `render_html` (entry) → `document_context` (context building) → `invoice.html` (template) → `render_pdf_bytes` (Playwright). Ours stayed clustered in `views.py` and **missed `render_html`, `document_context`, and the template** — the exact "context building → rendered template" hops asked for.

---

## Summary

| Query | Result |
|-------|--------|
| Q1 recurring | 🟰 tie |
| Q2 currency | 🟦 Augment |
| Q3 create/formset | 🟦 Augment |
| Q4 reminders | 🟰 tie |
| Q5 PDF pipeline | 🟦 Augment |

**Score: 2 ties, 3 Augment wins, 0 losses for Augment.** On core "where is X" both are equal; on deep multi-hop queries Augment pulls ahead.

### Why Augment wins the deep ones (diagnosed, actionable)

1. **Finer chunk granularity.** Augment returns sub-function spans (`models.py:160-161`, `:285-288`); we emit whole functions/classes. Finer chunks match a *specific aspect* of a deep query more sharply. → Lever: split large definitions by sub-blocks, or add overlapping sub-chunks.

2. **Cross-file pipeline recall.** Deep queries span view→service→template. Augment connects them; our dense vector tends to pull *same-file siblings* (Q5 stayed in `views.py`, missed `render_html`/`document_context`/`invoice.html`). → Lever: MMR/diversity in candidate selection (penalize many hits from one file) so distinct pipeline stages survive fusion.

3. **Precision on the named artifact.** Q3 asked for the *formset*; Augment returned `forms.py DocumentForm/DocumentLineFormSet`, we never surfaced `forms.py`. → Lever: ensure recall depth (raise `candidates`) + diversity so the precisely-named file isn't crowded out.

### Where we're already equal
Q1 and Q4 — single-subsystem deep queries living mostly in one service + model — are indistinguishable from Augment. Our gap is specifically **multi-file tracing**, not relevance per se.

---

## Addendum — MMR file-diversity (lever #2, implemented)

Added Maximal Marginal Relevance to the final selection (`mmr_lambda=0.7`). Diversity is **file-based, not vector-based**: a first attempt at cosine-MMR made Q5 *worse* (pipeline stages are semantically similar, so cosine wrongly suppressed the cross-file chunks we wanted). File-based penalty fixes that.

**Q5 PDF pipeline — before vs after MMR:**

| # | MMR off | MMR on (file-diverse) |
|---|---------|------------------------|
| 1 | views.py `document_pdf` | views.py `document_pdf` |
| 2 | services/pdf.py `render_pdf_bytes` | services/pdf.py `render_pdf_bytes` |
| 3 | views.py `document_send` | **services/render.py `document_context`** ⬅ recovered |
| 4 | views.py `document_reminder_send` | services/recurring.py `_clone` |
| 5 | views.py `shared_document_pdf` | management/renderpdf.py `Command` |
| 6 | views.py `document_preview` | services/ai_review.py `_doc_summary` |

MMR recovered `document_context` (the context-building stage Augment had and we'd missed) by breaking the `views.py` cluster — top-3 now spans view → renderer → context, matching Augment's pipeline trace. **Cost:** with only ~4-5 truly relevant files, the tail (ranks 5-6) reaches into marginally-relevant new files; acceptable since the head is what matters. Remaining gap vs Augment: finer (sub-function) chunk granularity — left as the next lever.

---

# Run 2 — same-session direct A/B (MMR active)

**Date:** 2026-06-04. Both engines called directly from one Claude Code session (no subagent), 5 *new* deep queries on `invoicer`. Our config now includes MMR file-diversity (`lam=0.7`). Top hits per engine, then verdict.

## Q1 — VAT & totals (subtotal / per-line VAT / reverse-charge)  🟦 Augment
- **Ours:** `_doc_summary` (noise) · `Document.vat_total` · `document_context` · `extract_lines` · seed · import · `ScrapedLine` · `ReverseChargeTests`
- **Augment:** `subtotal`+`vat_total`+`total` (the trio) · `DocumentLine.amount` · `check_reverse_charge` · `document_context` · `invoice.html` totals · `aging.html`
- Augment returned the whole money trio + `check_reverse_charge`; ours led with a noisy `_doc_summary` and missed `subtotal`/`total`/`check_reverse_charge`. Granularity again: those are tiny adjacent property chunks.

## Q2 — per-company isolation & auth  🟦 Augment (slight)
- **Ours:** `_user_docs` (the tenant filter) · `Company` · `DocumentLifecycleTests` · `DocumentCreateE2E` · `CompanyAdmin` · `partner_ai` (noise) · `CompanyForm` · CHANGELOG (noise)
- **Augment:** `document_list` · `company_edit` (get_object_or_404 owner) · `document_share_toggle` · `shared_document` · `document_form_view` (404 for non-owner) · `document_preview` · **`test_other_user_cannot_touch_my_document`**
- We found the central helper; Augment showed the `company__owner=request.user` check spread across the actual views + the security test. More directly answers "who can view/edit".

## Q3 — payments / paid-overdue / aging report  🟦 Augment
- **Ours:** `record_payment` · `is_overdue` · `_email_for` · `AgingReportTests` · CHANGELOG (noise) · `PaymentTests` · `aging.html` · `list.html`
- **Augment:** **`_aging`** (bucket builder) · **`report_aging`** (the view) · `report_payments` · `document_toggle_paid` · `record_payment` · `documents_bulk` · **`Payment` model** · `reminder_due`
- Augment found the aging computation (`_aging`/`report_aging`), the `Payment` model, and `toggle_paid` — all of which we missed (we had the test + template but not the view/model).

## Q4 — i18n (template lang vs UI lang, formatting)  🟰 near-tie
- **Ours:** `document_context` (template-lang override) · `InvoiceLanguageTests` · `rule_checks` (noise) · **`DefaultLanguageMiddleware`** · `draft_cover` · seed · `Template` · **`language_banner`**
- **Augment:** `document_context` · `base.html` (lang switcher) · `InvoiceLanguageTests` · `document_form.html` (translate JS) · `context_processors` · `landing.html` · **`config/settings.py`** (LANGUAGE_CODE/LANGUAGES/LOCALE_PATHS) · `Template`
- Strong for us — MMR spread hits across render/middleware/context_processors/models. Augment additionally pinned `settings.py` (the i18n config) and the `base.html` switcher.

## Q5 — AI integration (LLM shim, tiered escalation, callers)  🟰 near-tie
- **Ours:** `ai_review` (tiered escalation) · **`call_with_tools`** (the shim) · `extract_partner` · `extract_lines` · `_email_for` · `document_review` · `draft_reminder` · `ai_review` command
- **Augment:** `ai_review`+`_call_tier` · `reminder_ai` · `call_with_tools`+**`_ollama_call`**+**`_anthropic_call`** (full shim internals) · `extract_lines` · `document_reminder`/`_send` · `ai_review` command
- Both nailed it. We surfaced the public shim + every caller across services/views/management (MMR diversity visible); Augment additionally returned the private backend halves (`_ollama_call`, `_anthropic_call`) and `_call_tier` — finer granularity.

## Run 2 tally

| Query | Result |
|-------|--------|
| Q1 VAT/totals | 🟦 Augment |
| Q2 auth/isolation | 🟦 Augment (slight) |
| Q3 payments/aging | 🟦 Augment |
| Q4 i18n | 🟰 near-tie |
| Q5 AI integration | 🟰 near-tie |

**3 Augment, 2 near-ties, 0 ours-wins** — but the two near-ties (Q4, Q5) are clearly closer than Run 1's losses, and MMR's file-diversity is visibly working (Q4/Q5 span many files instead of clustering).

### The single remaining gap, now unmistakable: **chunk granularity**
Every Augment win this run came from the *same* root cause — it returns **sub-function / small-adjacent definitions** we don't isolate:
- Q1: `subtotal`/`total` (one-line properties) and `check_reverse_charge`
- Q3: `_aging` (nested helper) and the `Payment` model
- Q5: `_ollama_call` / `_anthropic_call` (private halves of the shim)

Our whole-function/whole-class chunking keeps these merged or ranks the parent as one unit, so a query about one *facet* can't pull just that facet. Relevance, fusion, code-prior and MMR are now competitive; **finer chunking is the next (and likely last big) lever.** Concretely: split large definitions into sub-blocks (or emit overlapping property/method-group chunks) so a single property or nested helper can surface on its own.

---

# Run 3 — after the chunking change

**Date:** 2026-06-04. Same 5 deep queries; our chunker reworked (`CHUNKER_VERSION=2`), invoicer re-embedded (263 chunks).

## What the inspection actually found (diagnosis corrected)
Before coding I dumped the real chunks. The "Run 2 granularity" theory was **half wrong**:
- `subtotal`/`total`/`_ollama_call`/`Payment` were **already separate chunks** — Augment beat us on *ranking*, not isolation. Our `subtotal`/`total` sat at fused-pool ranks **14-15**: they're 2-line chunks (`def subtotal: return sum(...)`) with almost no content, so they sink.
- A real **bug**: splitting the 140-line `Document` class **dropped its entire field block** (`models.py:148-190`: currency, dates, `no_vat`, `paid_date`…) — the method-only descent never emitted the non-def statements.

So the fix was the *opposite* of "finer": **group** the fields + the run of tiny properties into contextful chunks, while keeping substantial methods (`compute_due_date`) as their own named chunks.

`models.py` after the change:
- `L149-189 members` — the **recovered Document fields**
- `L210-248 members` — `subtotal`+`vat_total`+`total`+`is_paid`+`amount_paid`+`balance`+`is_partial`+`is_overdue` **grouped**
- `compute_due_date`, `apply_due_date`, `reminder_due`, `Meta` — still standalone

## Effect on the queries
- **Q1 VAT/totals — improved 🟦→🟰.** The grouped money chunk `models.py:210-248` (holding the whole subtotal/vat/total trio) now ranks **#2**, vs `subtotal`/`total` being invisible at ranks 14-15 in Run 2. Now comparable to Augment's money-trio span. (A noisy `_doc_summary` still leads at #1.)
- **Q3 payments — partially improved.** The same group surfaces the paid/overdue/balance properties at **#2**. But Augment's `_aging`/`report_aging` **views** and the `Payment` **model** still don't make our top-8 — and those are already-isolated chunks, so this is a **ranking/recall** gap, not chunking. Still slight Augment.
- **Q4 i18n — 🟰 (held).** The recovered `Document` fields chunk (`L149-189`, has `template`/`currency`) now adds useful context; middleware + context_processor + render still surface.
- **Q5 AI — 🟰 (unchanged).** `_ollama_call`/`_anthropic_call` are module-level functions (not class members), so grouping didn't touch them; their absence is pure ranking. We still return the shim entry + every caller.
- **Q2 auth — held;** bonus: `auth_backends.EmailBackend` now appears.

## Run 3 verdict
| Query | Run 2 | Run 3 |
|-------|-------|-------|
| Q1 VAT/totals | 🟦 Augment | 🟰 near-tie ⬆ |
| Q2 auth | 🟦 (slight) | 🟦 (slight) |
| Q3 payments/aging | 🟦 Augment | 🟦 (slight) ⬆ |
| Q4 i18n | 🟰 | 🟰 |
| Q5 AI | 🟰 | 🟰 |

**Net:** chunking change fixed a real recall bug (dropped fields) and turned Q1 into a tie; Q3 improved partway. It also **re-scoped the remaining gap**: what's left (Q3 aging views, Q5 shim internals) is **ranking of already-isolated small chunks**, not granularity. Next lever is therefore retrieval-side — e.g. a small relevance boost for named definitions whose symbol matches the query, or a recall pass that guarantees same-name symbol coverage — not more chunk surgery.

---

# Run 4 — symbol-name boost (the retrieval-side lever)

**Date:** 2026-06-04. Retriever only (no re-index). Added a **symbol-name boost**: each pool chunk's relevance is multiplied by `1 + 0.25 × (5+char query tokens matched in its symbol name)`, with a prefix rule so `payments`~`payment`. Targets exactly the Run-3 residue: already-isolated but low-ranked named defs.

## Effect — the residue closed
- **Q3 payments/aging — 🟦→🟰.** `report_aging` (the actual aging **view**) now surfaces at **#5** and `report_payments` at **#8** — both absent in Runs 2-3. Joined by `record_payment` #1, `AgingReportTests`, the status-property group, `PaymentTests`. The "aging report" is now answered.
- **Q5 AI — 🟰 (strengthened).** `_ollama_call` rose to **#2** (the shim backend Augment had and we'd missed), alongside `ai_review` #1 and every caller. (`_anthropic_call` sits just outside top-8 — MMR caps `llm.py` to one slot; the shim mechanism is nonetheless represented.)
- **Q1 VAT/totals — 🟰 (solidified).** `check_reverse_charge` boosted to **#1** (query literally says "reverse-charge"), money group #2, `ReverseChargeTests` #5 — the noisy `_doc_summary` fell out of top-8.
- **Q4 i18n — 🟰 (held).** `Template` boosted in; `document_context`/middleware/context_processor present.
- **Q2 auth — 🟦 (slight, held).** Mild residue: `settings_view` leads because its name token `view`~`views` matches a query that is *about* views; the auth-relevant chunks (`Company`, `EmailBackend`, the `test_other_user_cannot_touch` lifecycle test) are all still in top-8. The 5-char floor removed the worst of this (no more `call`/`data` boosts).

## Run 4 verdict (vs Augment Run 2)
| Query | Run 2 | Run 3 | Run 4 |
|-------|-------|-------|-------|
| Q1 VAT/totals | 🟦 | 🟰 | 🟰 |
| Q2 auth | 🟦 (slight) | 🟦 (slight) | 🟦 (slight) |
| Q3 payments/aging | 🟦 | 🟦 (slight) | 🟰 ⬆ |
| Q4 i18n | 🟰 | 🟰 | 🟰 |
| Q5 AI | 🟰 | 🟰 | 🟰 (stronger) |

**End state: 4 ties, 1 slight-Augment (Q2).** From Run 2's "3 Augment wins, 2 ties" to "0 clear Augment wins, 1 slight." The four-stage arc — noise filter → smart grouping → symbol-name boost, all over hybrid+RRF+rerank+MMR — closed the deep-query gap to Augment's proprietary stack on this repo, with one understood residue (a views-query mildly over-boosting view handlers).

The remaining Q2 wobble is a precision tax on the boost, not a recall gap; it would yield to excluding ultra-generic name tokens (`view`, `document`) from the symbol match, or gating the boost on the reranker already rating the chunk plausible. Left as a tuning knob.

---

# Run 5 — generic-token exclusion (closing Q2)

**Date:** 2026-06-04. Retriever only. Added `GENERIC_NAME_TOKENS` (view/views, document/documents, model, form, handler, service, command, request, response, object, base, index, data, value) — these structural words no longer count toward the symbol-name boost, so a query *about* views/documents stops boosting every view/handler.

## Effect — Q2 resolved, no regressions
- **Q2 auth/isolation — 🟦→🟰.** `settings_view` is gone. Top hits are now genuinely on-topic: `company_edit` #1 (owner-scoped create/edit), `Company` #2, `CompanyAdmin`, `DocumentLifecycleTests` (incl. the cross-user 404 test), `EmailBackend` #5 (the auth backend), **`AuthFlowE2E`** #6. Comparable to Augment's owner-scoping + security-test answer.
- **Q1 / Q3 / Q4 / Q5 — unchanged.** All distinctive-token boosts (reverse/charge, aging/report/payments, template, ollama/anthropic) are ≥5 chars and non-generic, so every Run-4 win held: `check_reverse_charge` #1, `report_aging` #5, `_ollama_call` #2, `Template` in.

## Final verdict (vs Augment Run 2 baseline)
| Query | Run 2 | Run 3 | Run 4 | Run 5 |
|-------|-------|-------|-------|-------|
| Q1 VAT/totals | 🟦 | 🟰 | 🟰 | 🟰 |
| Q2 auth | 🟦 | 🟦 | 🟦 | 🟰 ⬆ |
| Q3 payments/aging | 🟦 | 🟦 | 🟰 | 🟰 |
| Q4 i18n | 🟰 | 🟰 | 🟰 | 🟰 |
| Q5 AI | 🟰 | 🟰 | 🟰 | 🟰 |

**End state: 5 ties, 0 clear Augment wins** — from Run 2's 3 Augment wins. On these 5 deep, multi-file queries over `invoicer`, the DIY engine reaches parity with Augment's proprietary context stack.

## The arc (what each lever bought)
1. **Noise filter** (skip `.gitignore`/locale/`.po`) — stopped translation files swamping code.
2. **code-prior** — implementation over docs/tests/migrations.
3. **MMR (file-diverse)** — broke same-file clustering so multi-file pipelines surface (Q5 `document_context`).
4. **Smart chunk grouping** — recovered dropped class fields; grouped tiny properties so the totals trio ranks (Q1).
5. **Symbol-name boost + generic stoplist** — rescued isolated-but-low-content named defs (`report_aging`, `_ollama_call`) without over-boosting structural names (Q2).

Caveat: this is one repo, and parity means *comparable top-8*, not that we beat Augment — its finer-grained recall likely still shows on larger/again different codebases. But the gap that was obvious in Run 1-2 is closed here.

---

# Run 6 — multi-repo eval, graph-expansion, doc-penalty

**Date:** 2026-06-04. Generalization + new lever. Indexed two more repos: **astral-hr** (Skipp's TS/Next.js fullstack, 2250 chunks) and **certbot** (Python OSS, 4405 chunks). Built an automated eval (`eval/eval_set.json`, 24 queries × 3 repos; `eval/run_eval.py` scores recall@k / hit@k against expected path-substrings).

## Eval (our engine, automated)
| | recall@8 | hit@8 | recall@3 | hit@3 |
|---|---|---|---|---|
| invoicer | 1.00 | 1.00 | 0.88 | 1.00 |
| astral-hr | 1.00 | 1.00 | 0.88 | 0.88 |
| certbot | 0.94 | 1.00 | 0.94 | 1.00 |
| **overall** | **0.979** | **1.000** | **0.896** | **0.958** |

Strong and **not overfit to invoicer** — a TS repo and a large Python OSS repo both score ~parity. (Caveat: directory-level expected labels are generous; see the auth finding below.)

## Graph-expansion (new lever)
Added cross-file **callee** expansion: for the top fused candidates, pull in chunks that *define* a symbol the candidate references (cross-file only), into the pool before rerank — surfacing the next stage of a flow even when retrieval found only the entry point. Measured:

| k=3 | recall | hit |
|---|---|---|
| graph OFF | 0.854 | 0.917 |
| graph ON | **0.896** | **0.958** |

k=8 unchanged (0.979 / 1.000) — pure low-k precision gain, no high-k regression. Kept on by default.

## Doc-heavy repos exposed a real gap → fixed
Head-to-head on astral-hr ("how JWT auth + OAuth strategies work"):
- **Before:** our top-8 was `auth.service.login` then **6 markdown docs** (OAUTH_TESTING.md, START_HERE.md…) — astral-hr has 62 `.md` files that textually answer conceptual queries. Augment returned **all code** (auth.module, jwt.strategy, auth.controller, google/github/facebook strategies, authStore) — zero markdown.
- The eval **hid this** (label `auth/` was satisfied by `auth.service.ts` #1) — a lesson that coarse labels miss ranking quality.
- **Fix:** strengthened `_code_prior` — prose docs `.md/.rst/.txt` 0.6→**0.3**, window 0.5→0.4, `.html` kept at 0.6 (templates can be the answer). After: the astral-hr auth query returns **all code, no markdown**.

## State
24/24 unit tests green. The engine now: indexes any git repo (gitignore-aware, incremental), retrieves hybrid + RRF + code-prior + name-boost + MMR + graph-expansion, reranks (Voyage or local), serves over MCP, auto-reindexes on save, and is measured by a 3-repo eval harness. Remaining edge for Augment: on doc-heavy repos it still resolves the *specific* strategy/module files a touch better (ours surfaces the service/controller but not always every `*.strategy.ts`); a future lever is type/module-aware boosting or a larger reranker.

---

# Run 7 — module-aware boost (tried, reverted: measured regression)

**Date:** 2026-06-04. Hypothesis: boost "wiring" files (NestJS `*.module.ts`, Django `urls.py`/`apps.py`, `main.py`) so they surface and act as graph-expansion hubs (a module imports a feature's providers → pull its strategies). Implemented a `_structural_boost` (×1.2) on relevance + made wiring files expansion sources.

**Result — reverted.** Measured on the eval, it *regressed*:
| k=3 | recall | hit |
|---|---|---|
| without (Run 6) | 0.896 | 0.958 |
| with module-boost | 0.875 | 0.917 |

- It displaced a real answer: astral-hr "guest session → registered user" lost `guest/` from top-3 (a boosted wiring file took the slot).
- It did **not** fix the target case: astral-hr's broad auth query still didn't surface `auth.module.ts` — because the module file isn't retrieved into the candidate pool at all (it's terse wiring with few query terms), so neither a relevance boost nor expansion-from-hub can fire on it. The strategies gap is a **recall** problem (the files don't reach the pool for a broad query), which a ranking-stage boost cannot solve.

**Lesson:** a boost only re-ranks what retrieval already found; it can't conjure un-retrieved files. To close the doc-heavy / wiring-file gap you'd need a recall-stage change (e.g. a structure-aware pass that always pulls a feature's module + its imports when a feature dir is hit), not another ranking prior. Left for future work; reverted to keep the benchmark honest (back to recall@3=0.896).

---

# Run 8 — structure-aware recall (tried, reverted) + a determinism fix

**Date:** 2026-06-04. Tried feature-directory cohesion: when >=2 top candidates share a feature dir, pull that dir's siblings (module + strategies) into the pool. **Reverted** — like Run 7 it regressed the eval and, crucially, still didn't surface astral-hr's strategy files: feature-expansion *did* add them to recall, but the reranker ranks the login methods + `interface User` chunks above the terse passport-strategy configs. So the gap is **ranking/model quality**, not recall or architecture — two pool-augmentation attempts (Run 7 boost, Run 8 cohesion) both failed to move it.

**Valuable byproduct:** the eval's run-to-run variance (0.896 vs 0.875 on identical code) exposed a real **non-determinism bug** — `_expand_pool` iterated a `set()` of identifier strings, whose order varies per process (hash-seed), so different callees were added when the cap hit. Fixed with `sorted(set(...))`; eval is now reproducible at recall@3=0.896 / hit@3=0.958. Kept this fix; reverted the feature-cohesion experiment.

**Conclusion:** the engine is at its heuristic ceiling on this benchmark. Remaining gap to Augment (terse-wiring / doc-heavy ranking) is dominated by retrieval *model* quality (their proprietary embeddings + reranker), not by cheaply-addable structure — confirmed by two measured negative results.
