# BantAIProyekto UI/UX Plan

Prepared: 16 November 2026  
Project: `C:\Dev\ProjectWatchAI`  
Design reference: [Laws of UX](https://lawsofux.com/), reviewed on 16 November 2026  
Status: Proposed design and implementation plan; no application changes are included.

## 1. Purpose and scope

Help a reviewer find a DPWH contract, understand the available records and photographs, make an attributable decision, and resume interrupted work without losing context. Regional readers should understand the coverage and limitations of saved findings before interpreting counts or contractor scores.

This plan applies all 30 principles currently listed on Laws of UX through concrete interface requirements and validation checks. The site presents design heuristics and psychological concepts, rather than a formal compliance standard. The requirements below are project-specific interpretations; numerical usability targets are proposed acceptance criteria, not numbers mandated by Laws of UX. Validate the design with users before claiming that it works.

### Project evidence reviewed

| Source | What it establishes |
| --- | --- |
| `README.md` | Product purpose, evidence policy, saved revisions, recheck behavior, deployment limits, local inference and constrained regional highlights. |
| `ProjectWatchAI/ui.py` | Existing dark/light theme, teal accent, shared headers and three main destinations. |
| `ProjectWatchAI/app.py` | Search and filtering, saved regional findings, contractor ranking, review queues, manual category changes, photo labeling and model readiness. |
| `ProjectWatchAI/contract_assessment.py` | Assessment categories, date handling, confidence thresholds and discrepancy flags. |
| `ProjectWatchAI/evidence_policy.py` | Evidence limitation, checkpoint eligibility and provenance requirements. |
| `ProjectWatchAI/yolo_test_app.py` | Separate detector testing, corrections and candidate training workflow. |
| `Elements/Logo System.png` | Existing red, black and cream brand direction with horizontal, stacked, icon and monochrome logo treatments. |

Findings are based on source inspection and the logo reference, not a live UI audit or user study. Other files in `Elements/` are available for a later asset audit. Personas and task priorities below are working hypotheses.

## 2. Users, goals and success criteria

| User context | Main task | Successful experience |
| --- | --- | --- |
| Evidence reviewer, primary | Inspect a contract and record a reasoned category decision | Sees source facts, latest dated evidence, limitations and reviewer attribution together; receives a durable save confirmation. |
| Regional analyst | Find patterns and open supporting contracts | Understands which saved records are included, how current they are and how scores are calculated. |
| Administrator/model operator | Refresh reviews and inspect detector readiness | Distinguishes missing, experimental, provisional and validated detectors; can pause and resume without duplicating completed work. |
| Occasional reader | Look up a project and understand its evidence | Finds a familiar search interface and plain language, with no need to understand YOLO or Ollama. |

These contexts do not imply separate authenticated roles. The current application is a single-reviewer application; reviewer names are attribution, not authentication. Do not make the Admin navigation look like an access-control boundary.

Proposed evaluation targets:

- At least 90% of attempted core tasks succeed without facilitator intervention in formative testing; report raw counts alongside percentages.
- Median time to locate a specified contract is at most 30 seconds in a prepared, representative dataset.
- At least 90% of participants correctly distinguish reported completion from visible photo evidence and correctly interpret unavailable analysis.
- Every tested save, retry and pause/resume scenario preserves the applicable prior evidence and reviewer input.
- Visible acknowledgment of ordinary actions targets 400 ms or less on the documented test environment; long inference runs expose real progress separately.
- Every primary workflow is operable by keyboard, with readable focus, state and error feedback.

## 3. Evidence and trust requirements — priority P0

1. Keep three separate concepts visible: **Reported project status**, **Photo evidence assessment**, and **Manual reviewer decision**. A source saying Completed must not become an AI completion verdict.
2. Use **Visible scope evidence** for automatic positive findings. Existing source code still contains labels such as `Completion evidence found`; audit every display, saved group, chart, tooltip and scoring explanation. Introduce a presentation mapping first, preserving stored values and historical provenance. Any later category migration needs its own reviewed migration plan.
3. Place this limitation beside the assessment: “Photos show only the photographed scope. They do not verify full completion, quality, quantities, spending or the actual completion date.”
4. Make the newest dated photo group the default evidence view. Mark older photos as historical context, unknown dates as unknown, and conflicting cues within the newest date group as requiring review. Do not select an attractive older photo as the leading evidence.
5. Label time differences as **Photo captured X days after reported completion**. Never describe them as measured construction delay. Date-only records stay date-only; timestamps display Philippine time explicitly.
6. Distinguish no photos, unavailable photo retrieval, analysis failure, successful but inconclusive analysis and no detected feature. None of these automatically proves absent work.
7. Display detector eligibility next to findings. Experimental observations remain review-only; provisional findings remain qualified. Even validated detector status does not certify a complete contract.
8. Express detector confidence as a model score for a visible feature. Do not compare different model scores as completion probabilities or show a project-wide completion-confidence percentage.
9. Display approved budget, award amount and other reported amounts with source labels, PHP units and a visible comparison basis. Unknown is not zero. Budget flags use the existing policy, including an increase of at least 10% over a positive approved budget; a difference alone does not establish spending or misconduct.
10. Preserve manual decisions during reruns. If underlying evidence changes, show the prior decision, reason, reviewer, timestamp and a clear re-review warning. Save failures must retain the draft.
11. Display regional highlights as selections from recorded facts and link each highlight to its supporting contract. Model failure must leave deterministic facts available.
12. Qualify contractor scores with included/scored contract counts, missing evidence, formula and timing assumptions. Avoid labels such as “best contractor” that imply a comprehensive quality assessment.

## 4. Information architecture

Retain the current three top-level destinations to preserve familiarity:

```text
BantAIProyekto
├── Explore projects
│   ├── Search and filters
│   └── Contract detail
│       ├── Overview: reported facts and evidence status
│       ├── Photos and findings
│       ├── Timeline and budget
│       └── Sources and review history
├── Regional review
│   ├── Saved coverage and freshness
│   ├── Findings and supporting contracts
│   └── Contractor comparison and scoring explanation
└── Admin
    ├── Reviewed contracts: review workspace and saved groups
    ├── Regional reviews: configure, run, pause, resume
    ├── Contract reviews: analyze up to three contract IDs
    ├── Rerun reviews
    └── Model status
```

Keep detector training in the separate tester. Offer an explicit operator link when configured. Contract category decisions and training labels must have separate forms and save actions; the current combined “Move contract and save for YOLO” workflow should be separated to make each outcome understandable.

Persist the active contract, filters, page, selected photo and review position through ordinary Streamlit reruns and navigation. Persistent drafts across browser closure require additional storage and are a planned capability, not an existing guarantee. Back to results must restore the user's result context.

## 5. Screen and interaction specifications

### A. Explore projects

- First screen: title, one sentence of purpose, contract ID/description search, Region and Fiscal year filters, then results. Put infrastructure type, reported status, photos-only and page size in clearly labeled additional filters on compact screens.
- Show active filters and a Reset filters action. Preserve the fiscal-year default explicitly; do not silently broaden the search.
- Use table columns in this order: Contract ID/project, Region, Reported status, Photo evidence, Last reviewed. Put detailed budget and date fields in contract details.
- Keep current dataset counts honest. In photos-only mode, distinguish the total matching records before photo checks from the number actually checked and shown on the page.
- Provide both row selection and a labeled Open contract control. A pointer-only table interaction cannot be the sole path.
- Show source checked time separately from saved review time. A periodic refresh must not steal focus, reset selection or discard input. Replace the pulsing “live” treatment with a quiet freshness label unless work is actually running.

### B. Contract detail

Order content: identity and source link; reported facts; evidence assessment and limitations; latest dated photos; timeline/budget; provenance/history. Use optional sections for technical observations rather than making them prerequisites for understanding the finding.

Proposed desktop arrangement:

```text
Back to results / Contract ID                  Open source record
Project description · Region · Contractor
Reported status | Photo assessment | Last reviewed
Evidence limitation and any stale/conflict warning
┌───────────────────────────┬───────────────────────────┐
│ Latest dated photo group  │ Finding and supporting facts│
│ Photo · date · source     │ Detector eligibility        │
│ Original / overlay toggle │ Reviewer decision + reason  │
│ Previous / next photo     │ Save decision               │
└───────────────────────────┴───────────────────────────┘
Timeline and budget | Sources and review history
```

- Retain access to the original image at all times. Overlay legends name the detected feature and model status. Treat masks and boxes as observations, not verification stamps.
- Keep capture date, photo number and source beside each photo. Default to newest dated evidence with a visible control to inspect historical groups.
- Give each flag a short explanation and the relevant supporting date, amount or photo. Group repetitive flags by reason rather than stacking identical warning banners.
- Provide a text description of each finding so the evidence is understandable without interpreting overlay colors.
- Manual form: current category, eligible target category, required reason, reviewer attribution and Save decision. Do not preselect an unsupported positive outcome.
- Save confirmation: “Decision saved for [contract ID]” with category, reason summary and time. Offer Next contract as a separate action; do not advance before successful persistence. A correction creates a new revision; do not promise Undo until revision restoration is implemented.

### C. Regional review

- Lead with region/year, included saved contract count, review date range and stale/unavailable counts. Explain that this view covers saved reviews and is not a complete regional census unless coverage has been established.
- Show reviewed contracts, distinct contractors and flagged contracts as separate measures with definitions. Clicking a measure should reveal the matching records where feasible.
- Provide counts and text labels with charts, and a table alternative. Selecting a chart category should expose the same filter in the supporting contract list.
- Present contractor comparison after evidence coverage. Show Scored/Included counts next to scores. No-score remains No score, never zero; show the scoring formula and timing qualifications at the point of comparison.
- Highlights are optional. Label the action “Generate evidence highlights”; keep Ollama configuration and diagnostics in operator details.
- Empty state: “No saved regional reviews yet.” Explain where to start one in Admin, with a navigation action.

### D. Admin review queues

- Configure the scope, show the chosen filters and any known workload, then offer Check new and due contracts. Do not fabricate a job duration or count before it is known.
- Distinguish process stages: fetching records, retrieving photos, analyzing evidence, saving results and generating highlights.
- Show completed/total when the denominator is known; otherwise show the current stage and completed count. Keep failures, retry-required records and remaining work separate.
- Pause feedback must distinguish Pause requested from Paused. Resume uses the saved queue. Explain that rechecks happen when a scan is started; there is no unattended scheduler.
- End with completed, failed, pending and preserved-review counts plus Open results and Retry failed actions. Do not label a partially failed run simply Complete.
- Keep model status available when analysis cannot run. Each detector row shows availability, eligibility and the next practical action. Technical hashes and release details live in an expandable section.

### E. Photo corrections and tester

- Separate the concepts “what is visible in the photo” and “what category should this contract receive.” An image label must not silently change a contract decision.
- Correction form: original photo, label, outline, source contract/project ID, save action and training-eligibility explanation.
- Unknown provenance may be saved, but display “Excluded from training until source is identified.” Explain validation failures inline.
- Training results distinguish Candidate ready, Evaluation required and Approved active. A finished training run must not appear promoted automatically.

## 6. Full Laws of UX coverage matrix

Each source link points to the corresponding principle. Requirements and checks are original applications to BantAIProyekto, not copied source definitions.

| # | Principle and source | Required application | Acceptance check |
| --- | --- | --- | --- |
| 1 | [Aesthetic-Usability Effect](https://lawsofux.com/aesthetic-usability-effect/) | Apply a consistent brand, spacing and typography system, while keeping ambiguous evidence visibly qualified. | Test task accuracy independently of visual preference; attractive cards must not make participants overstate certainty. |
| 2 | [Choice Overload](https://lawsofux.com/choice-overload/) | Start with search and essential filters; disclose advanced filters and eligible decision choices progressively. | A novice finds a contract without opening every filter; experienced users can still reach every supported option. |
| 3 | [Chunking](https://lawsofux.com/chunking/) | Group identity, reported facts, photo evidence, reviewer decision and provenance into named sections. | Participants locate the capture date, budget basis and decision reason without scanning unrelated sections. |
| 4 | [Cognitive Bias](https://lawsofux.com/cognitive-bias/) | Show source facts before interpretation, use neutral language, and place counterevidence beside positive findings. | In a same-date conflict scenario, users notice both scope detections and construction cues instead of accepting the first badge. |
| 5 | [Cognitive Load](https://lawsofux.com/cognitive-load/) | Lead with a plain finding and its reason; disclose raw observations and model configuration on demand. | A reader explains the finding without interpreting model hashes or raw JSON. |
| 6 | [Doherty Threshold](https://lawsofux.com/doherty-threshold/) | Acknowledge actions promptly, then show truthful stage/progress feedback for long work. | Measure acknowledgment separately from inference duration; no repeated clicks caused by an apparently unresponsive button. |
| 7 | [Fitts's Law](https://lawsofux.com/fittss-law/) | Use primary controls at least 44 CSS px tall as a project target, with adequate spacing, near the related form/photo. | Touch and keyboard users can reach Save and photo navigation without accidental adjacent actions. |
| 8 | [Flow](https://lawsofux.com/flow/) | Preserve review position, photo and draft; keep a predictable inspect-decide-save-next sequence. | Review five contracts without losing filter context or re-entering a saved reason. |
| 9 | [Goal-Gradient Effect](https://lawsofux.com/goal-gradient-effect/) | Display real completed/remaining queue work and an explicit save outcome. | Progress advances only for actual completed work; failed work remains visible. No fabricated head starts. |
| 10 | [Hick's Law](https://lawsofux.com/hicks-law/) | Give each task area one dominant next action and make secondary actions visually quieter. | Participants identify the next action on search, review and queue screens without prompting. |
| 11 | [Jakob's Law](https://lawsofux.com/jakobs-law/) | Use familiar search, filters, breadcrumbs, tables, back navigation and labeled form actions. | First-time users can open and return from a contract without instruction. |
| 12 | [Law of Common Region](https://lawsofux.com/law-of-common-region/) | Put each photo, capture date, source, findings and overlay legend inside one bounded evidence region. | Users correctly attribute every visible finding to its photo. |
| 13 | [Law of Proximity](https://lawsofux.com/law-of-proximity/) | Keep field errors under their fields and dates/eligibility beside the relevant finding. | No error or limitation can be mistaken for one belonging to a neighboring control. |
| 14 | [Law of Prägnanz](https://lawsofux.com/law-of-pr%C3%A4gnanz/) | Use simple rectangular groups, restrained icons and a clear heading hierarchy. | At a glance, users distinguish reported status, evidence status and the decision form. |
| 15 | [Law of Similarity](https://lawsofux.com/law-of-similarity/) | Reuse badge, button and state styles across Explore, Regional review and Admin. | The same evidence category has the same label and visual treatment everywhere. |
| 16 | [Law of Uniform Connectedness](https://lawsofux.com/law-of-uniform-connectedness/) | Link findings explicitly to photo numbers and join dated events in a labeled timeline. | Selecting a finding identifies its actual supporting photo; timeline connections do not imply causation. |
| 17 | [Mental Model](https://lawsofux.com/mental-model/) | Structure reviews around contract, reported record, dated photo, finding and human decision. | Users explain which facts come from DPWH records, detectors and reviewers. |
| 18 | [Miller's Law](https://lawsofux.com/millers-law/) | Use meaningful groups and recognition aids; do not impose a universal seven-item cap. | Users can compare evidence without remembering hidden values; task-based grouping is tested. |
| 19 | [Occam's Razor](https://lawsofux.com/occams-razor/) | Reuse shared detail/status components and remove duplicate explanations or navigation that serves no task. | Every retained panel supports an observed task or an evidence requirement. |
| 20 | [Paradox of the Active User](https://lawsofux.com/paradox-of-the-active-user/) | Put examples and brief guidance next to first-use search, category decisions and training-source fields. | A novice completes the task without consulting a separate manual. |
| 21 | [Pareto Principle](https://lawsofux.com/pareto-principle/) | Prioritize finding contracts, reading evidence, saving decisions and resuming reviews, then validate this priority with observed use. | Early testing records task frequency and cost; do not assert an unmeasured 80/20 split. |
| 22 | [Parkinson's Law](https://lawsofux.com/parkinsons-law/) | Bound work with explicit scopes and sensible defaults; support small review batches. | Users can finish a defined batch and leave safely; no forced countdown or rushed decision. |
| 23 | [Peak-End Rule](https://lawsofux.com/peak-end-rule/) | Design calm recovery at failures and a precise, useful completion summary at save/run end. | A user leaving after a failure or save knows what persisted and what to do next. |
| 24 | [Postel's Law](https://lawsofux.com/postels-law/) | Normalize harmless ID casing/whitespace and proposed comma/newline separators; enforce the three-ID limit with explicit feedback. | Valid variants work; ambiguous input is rejected clearly; extra IDs are never silently dropped. |
| 25 | [Selective Attention](https://lawsofux.com/selective-attention/) | Reserve strong emphasis for the current task and actionable evidence gaps; avoid decorative live motion. | Users notice stale/conflicting evidence before saving without overlooking the evidence itself. |
| 26 | [Serial Position Effect](https://lawsofux.com/serial-position-effect/) | Put identity and evidence status first, and a clear saved outcome/next step at the end. | After a brief review, users recall the contract, uncertainty and save result. |
| 27 | [Tesler's Law](https://lawsofux.com/teslers-law/) | Calculate comparisons, date grouping and eligibility in the system, while keeping reasons inspectable. | Users need no manual date/budget arithmetic and can still inspect the comparison basis. |
| 28 | [Von Restorff Effect](https://lawsofux.com/von-restorff-effect/) | Distinguish the primary action and significant conflict/stale warnings; avoid making all cards visually urgent. | A quick scan identifies the main action and key exception without a wall of alert colors. |
| 29 | [Working Memory](https://lawsofux.com/working-memory/) | Keep selected contract, comparison facts and draft reason visible; preserve them across ordinary interactions. | Switching photos or opening details does not require remembering dates or rewriting a reason. |
| 30 | [Zeigarnik Effect](https://lawsofux.com/zeigarnik-effect/) | Show resumable unfinished work and the last completed step with an explicit Resume action. | Returning to a paused queue reveals actual remaining work and a safe stopping point, without pressure to continue. |

Apply the principles together. Progressive disclosure must not hide uncertainty; visual simplicity must not erase evidence distinctions; tolerant input must not weaken validation. Motivation-related patterns must reflect actual progress and preserve the user's freedom to stop. Do not add artificial waits or progress simply because a source discusses perceived value.

## 7. Visual system and responsive behavior

### Brand direction

Use the supplied red/black/cream logo family as the identity reference. The current UI uses a teal BP mark and dark navy surfaces, so the design needs an explicit reconciliation. Proposed direction: supplied horizontal wordmark in the expanded sidebar, icon-only variant in compact navigation, neutral content surfaces and restrained teal for interaction. Keep brand red mainly in identity; reserve labeled error treatments for actual failures. Verify that users distinguish the brand from warnings.

Do not use the entire logo-system presentation image as the application logo. Inspect `Logo.png` and other existing assets for a clean, readable mark; prepare appropriate variants only if needed. Preserve proportions and provide alt text for meaningful branding. Decorative icons are hidden from assistive technology; icon-only controls have accessible names.

### Proposed design tokens

| Token | Starting specification |
| --- | --- |
| Page/surface, light | `#F0F5F9` / `#FFFFFF` |
| Page/surface, dark | `#0A0F1A` / `#111B2B` |
| Primary text | `#17253A` light; `#E6EDF7` dark |
| Secondary text | `#465B75` light; `#A1B1C8` dark |
| Action accent | `#08766C`; select verified hover, active, disabled and focus variants for each theme |
| Brand red | Sample and verify from supplied assets; do not treat an estimated hex as an approved brand value |
| Spacing | 4, 8, 12, 16, 24, 32 and 48 CSS px |
| Type | System sans-serif; body 16 px, supporting text 14 px, section 20–24 px, page title 28–32 px |
| Controls | Target minimum 44 px height; 8 px spacing between adjacent actions |
| Shape | 8 px control radius, 12 px card radius, subtle borders and restrained shadows |
| Reading width | Approx. 65–80 characters for explanatory prose; wide tables and photos can use the content width |

These reuse much of the existing theme and require contrast verification before adoption. Define semantic colors for neutral, information, attention, error and saved success; pair every color with text or an icon. Never make green synonymous with certified project completion.

### Responsive layouts

- Wide screens, 1200 px and above: sidebar plus content; evidence and reviewer form in roughly 60/40 columns.
- Medium screens, 768–1199 px: reduce columns and preserve readable photo/detail widths; collapse sidebar when necessary.
- Small screens, below 768 px: stack identity, status, evidence and decision in that order. Use a filter expander, explicit photo controls and a labeled contract-card alternative to wide tables.
- At 320 CSS px and at 200% zoom, content and controls remain reachable. Horizontal scrolling is limited to genuinely two-dimensional tables; avoid page-wide overflow.
- Sticky controls, if added, must not cover content, focused fields or the mobile keyboard. Prefer a normal form footer when Streamlit cannot provide reliable behavior.

### Accessibility acceptance targets

Adopt the relevant Level AA criteria in [WCAG 2.2](https://www.w3.org/TR/WCAG22/) as an additional design target. This plan is not an accessibility certification.

- Verify at least 4.5:1 contrast for normal text, 3:1 for large text, and applicable 3:1 non-text contrast for essential controls and states.
- Preserve native semantics, descriptive field labels, logical headings, keyboard order and visible focus. Do not use hover-only help.
- Expose errors and asynchronous results programmatically. Test whether Streamlit's rendered notifications are announced; add a suitable fallback when needed.
- Provide text/table alternatives for charts and photo findings; status must remain understandable without color.
- Honor reduced motion. No essential information should depend on a pulse or animation.
- Test keyboard-only use, screen-reader reading order, zoom, light and dark themes, and mobile touch. Document framework limitations and fix blockers before release.

## 8. State and microcopy inventory

| State | Required feedback and recovery |
| --- | --- |
| Initial search | “Search by contract ID or project description.” Include one example and visibly selected fiscal year. |
| No matching results | “No contracts match these filters.” Show active filters and Reset filters. |
| No saved regional results | Explain that saved results appear after running a regional review; provide a route to the relevant Admin section. |
| No source photos | “No photos are available in this source record.” Do not imply unfinished work. |
| Photo request failed | “Photos could not be retrieved.” Offer retry and source link; preserve previous evidence. |
| Detector unavailable | Name the unavailable detector, explain that it could not assess the feature, and link operators to Model status. |
| Inconclusive analysis | “These photos do not provide a clear assessment.” Show observations and a manual-review path. |
| Latest-date conflict | “Latest dated photos show both scope evidence and construction cues.” Link to each relevant photo. |
| Historical evidence | “Historical photo — excluded from the current latest-date assessment.” Keep it inspectable. |
| Stale saved review | “Saved review needs a refresh because evidence or assessment rules changed.” Offer Rerun review and retain the last review. |
| Source/API outage | “The source is unavailable. Your previous saved review is retained.” Offer retry and show its timestamp. |
| Missing decision reason | “Enter a reason for this category change.” Place this at the reason field and retain the selected category. |
| Save failed | “Decision could not be saved. Your draft is still here.” Keep input and offer Retry save. |
| Save succeeded | “Decision saved for [ID].” Show category, reviewer and time; offer Next contract. |
| Pause requested/paused | Show the actual state, completed/remaining counts and Resume when safe. |
| Highlights unavailable | “Evidence highlights are unavailable. Recorded facts are still shown.” Keep deterministic results accessible. |

## 9. Streamlit implementation plan

Prefer incremental changes in the current stack; no framework migration is required by this plan.

| Phase | Work and likely code touchpoints | Completion evidence |
| --- | --- | --- |
| P0 — Evidence language and semantics | Audit categories, flags, score labels and unknown states in `app.py`; add consistent presentation helpers in `ui.py`. Review wording against `contract_assessment.py` and `evidence_policy.py` without weakening their rules. | All automatic findings use qualified language; source status and manual decisions remain separate; representative legacy records display consistently. |
| P1 — Shared system and navigation | Consolidate theme tokens, badges, labeled actions, headers, empty/error states and logo treatment in `ui.py`; adjust Explore filters and accessible result selection in `app.py`. | Both themes and compact layouts work; search and back navigation preserve context. |
| P1 — Review workspace | Refactor saved/live contract rendering into shared evidence and provenance sections; split training correction from category saving; preserve draft inputs by contract ID. | Save/retry/rerun scenarios preserve evidence, draft and attribution; no action silently performs both training and review changes. |
| P1 — Regional clarity | Add coverage/freshness framing, comparable score context, chart/table drilldowns and clear highlights language. | Every aggregate can be traced to included records; no-score and unavailable evidence remain explicit. |
| P2 — Queue and operator experience | Improve stage feedback, pause/resume states, partial-failure summaries and model-status guidance; align tester language in `yolo_test_app.py`. | Interrupt/restart trials show reliable recovery and honest progress. |
| P2 — Evaluation and polish | Conduct task testing, keyboard/screen-reader checks, responsive QA and action-latency measurement. Iterate on observed issues. | Core targets are met or open blockers are recorded with owners; all 30 matrix rows have review evidence. |

Implementation constraints:

- Use native Streamlit controls and forms where possible. Consolidate CSS and minimize reliance on internal `data-testid` selectors, which can change between framework versions.
- Use stable widget keys scoped to contract/photo IDs. Do not reset form state on periodic refresh or unrelated reruns.
- Acknowledgment latency may require a smaller fragment or work scheduling changes; a spinner alone is not proof that the 400 ms target was met.
- Reuse current content-hash/checkpoint-aware inference caching. Cosmetic work must not trigger unnecessary inference or imply that cached output is freshly analyzed.
- Escape external record text. Keep raw JSON and model hashes in operator/provenance details, with plain-language summaries in primary views.
- Store new persistent draft or UI preference data separately from evidence decisions. Any storage/schema change needs an explicit migration and recovery check.
- Do not expose this single-reviewer deployment as a multi-user authenticated service through visual changes alone.

## 10. Validation and release checklist

### Prototype and formative study

Prepare a clickable or working prototype of Explore, contract review, Regional review and Admin queue states. Start with approximately five representative participants across reviewing and analysis tasks; use a separate operator walkthrough for training and model readiness. This is formative research, not statistical proof of usability.

Ask participants to:

1. Find a specified contract, inspect its source status and explain what the photo assessment supports.
2. Review an older positive photo and a newer ambiguous photo, then identify the evidence that governs the current assessment.
3. Inspect a same-date conflict and distinguish it from analysis unavailable and no-photo states.
4. Save a reasoned manual category decision, handle a simulated save failure, and locate the resulting history.
5. Pause and resume a regional queue, explaining which work is complete and which needs retry.
6. Interpret a contractor score with a small scored sample and missing evidence without treating it as a comprehensive quality verdict.
7. Return to the original search context after inspecting a contract.

Record completion, time, misinterpretations, assistance, accidental actions and a brief ease rating. Use synthetic or sanitized examples for any usability recordings. Observe behavior before asking for visual preference.

### Functional and visual verification

- Use prepared scenarios for empty, loading, slow source, partial results, unknown dates/amounts, long project names, conflicting photos, stale manual decisions and missing models.
- Check boundary cases around the newest date group and budget threshold; verify that presentation changes preserve the existing evidence policy.
- Run the existing regression suite from `ProjectWatchAI`: `python -B -m unittest test_audit_regressions -v`. It covers underlying evidence behavior; it does not replace UI testing.
- Add focused tests only where new state, mapping, persistence or action separation could cause material errors. Avoid tests that merely repeat decorative CSS values.
- Exercise real Streamlit reruns, page changes, source failures and queue interruptions in integration checks. Confirm refresh does not reset focus or a pending form.
- Visually inspect representative widths around 360, 768, 1024 and 1440 px, plus 320 px reflow and 200% zoom. Verify both themes and long labels.
- Test keyboard navigation and screen-reader output on the chosen supported browser/environment. Record the environment and any limitations.
- Measure action acknowledgment separately from API, YOLO and highlights completion. Report actual timings and failures rather than a blanket responsiveness claim.

### Ready-to-release criteria

- [ ] All 30 Laws of UX rows have a reviewed design treatment and a recorded validation result.
- [ ] Reported facts, automatic observations and manual decisions remain distinct everywhere.
- [ ] No badge, score, chart or overlay implies certified completion or established misconduct.
- [ ] Missing, failed, inconclusive, historical and stale evidence are visibly different.
- [ ] Manual decisions, revisions and drafts survive the tested failure and rerun scenarios as specified.
- [ ] Regional coverage, score sample size, freshness and source provenance are inspectable.
- [ ] Primary flows pass keyboard, contrast, zoom and responsive checks with no unresolved blockers.
- [ ] Core usability targets have been assessed with results and remaining issues documented.
- [ ] Existing evidence regressions pass and required live integration checks are complete.
- [ ] Prototype screens, UI component inventory, coverage matrix and issue log are handed over with owners for unresolved work.

## 11. Source notes

The [Laws of UX index](https://lawsofux.com/) and the 30 linked principle pages in section 6 are the design reference, checked on the preparation date. The site may add or revise principles later, so recheck coverage at implementation kickoff. This document paraphrases the ideas and applies them to the repository; it does not reproduce the site's articles or claim endorsement.

The [WCAG 2.2 specification](https://www.w3.org/TR/WCAG22/) supplies the separate accessibility reference. Product behavior and constraints come from the repository files listed in section 1. Proposed features, layouts and thresholds in this document require implementation and validation; source inspection alone does not establish that the current application meets them.
