# BantAIProyekto UI/UX implementation record

Plan: `BantAIProyekto-UI-UX-Plan.md`, prepared 16 November 2026. The Desktop copy and the repository copy in `UI UX PLAN/` are byte-identical (checked with `diff`).
This record states what is decided, implemented and verified. Each finding says whether it comes from **source inspection** (S), **live UI observation** (L, rendered in a browser) or **measurement** (M, calculated). User-study targets are never marked as met without participants.

## Contents

1. Phase status and naming
2. Baseline findings
3. Elements visual audit and asset decisions
4. Design direction
5. Plan checklist (every section)
6. Laws of UX (30 rows)
7. P0 implementation record (done earlier on 2026-11-16), then §7a–§7c, §7d Explore (phase 4), §7e Review workspace (phase 5), §7f Regional review (phase 6) and §7g Admin queues and model readiness (phase 7)
8. Phase sequence, commands, blockers and research tasks

---

## 1. Phase status and naming

The request series uses "phase 1" for **audit and design direction** (this document). The plan's own implementation phases are P0–P2 (plan §9). Mapping:

| Work | Plan phase | Status |
| --- | --- | --- |
| Evidence language and semantics | P0 | **Implemented and verified 2026-11-16.** Record in §7. It was done before this audit, at the first request. |
| P0 evidence presentation completion ("phase 2") | P0 | **Implemented and verified 2026-11-16.** Record in §7b. Policy findings in `docs/evidence-policy-inconsistencies.md`. |
| Shared visual system, navigation and states ("phase 3") | P1 | **Implemented and verified 2026-11-16.** Record in §7c. |
| Explore projects ("phase 4") | P1 | **Implemented and verified 2026-11-16 (fixture-backed).** Record in §7d. |
| Audit and design direction (this document) | Prerequisite for P1 shared system | **Complete 2026-11-16.** Doc-only; no application code changed in this step. |
| Shared system and navigation | P1 (P1a) | **Implemented and verified 2026-11-16.** Record in §7a. Built by two parallel agents (theme/brand; Explore), then integrated and re-verified by the lead. |
| Review workspace ("phase 5") | P1 (P1b) | **Implemented and verified 2026-11-16 (fixture-backed; real photos and detector overlays not exercised).** Record in §7e. |
| Regional clarity ("phase 6") | P1 | **Implemented and verified 2026-11-16 (fixture-backed; no local model run; browser not rerun after final fixes).** Record in §7f. |
| Queue and operator experience ("phase 7") | P2 (P2a) | **Implemented and verified 2026-11-17 (fixtures, fake dependencies, real worker threads; no checkpoints in this checkout; browser fixes not rerun in the browser).** Record in §7g. |
| Evaluation and polish | P2 | Not started. No participant research has been run. |

## 2. Baseline findings

### 2.1 Repository state and user changes to preserve

Observed with `git status` on 2026-11-16 (branch `main`, last commit `78b3431`):

| Path | State | Handling |
| --- | --- | --- |
| `Elements/` (4 PNGs) | Untracked, user-supplied | **Preserve originals.** Never edit or move them. Derived assets go to a separate folder (§3.6). |
| `UI UX PLAN/BantAIProyekto-UI-UX-Plan.md` | Untracked, user copy of the plan | Preserve; read-only reference. |
| `ProjectWatchAI/app.py`, `ui.py` | Modified (P0, uncommitted) | Keep. P1 builds on them. |
| `ProjectWatchAI/evidence_language.py`, `test_evidence_language.py`, `docs/` | New (P0, uncommitted) | Keep. |
| `ProjectWatchAI/data/` | **Absent** in this checkout | There are no real reviews, datasets or checkpoints here. Every detector shows "Not installed" in live screens (environment limitation, not a product state). |
| `ProjectWatchAI/dashboard-preview.html` | Tracked static HTML prototype | Not the application. Not used as evidence of app behaviour. |

Nothing has been committed, pushed, deployed, retrained or promoted.

### 2.2 Source-inspection findings (S)

Line numbers refer to the working tree on 2026-11-16, after P0.

| # | Finding | Location | Plan ref | Owner phase |
| --- | --- | --- | --- | --- |
| S1 | Theme is a 50-line CSS block with **15 `data-testid` selectors**. Light mode works by string-replacing dark hex values. Fragile across Streamlit upgrades. | `ui.py:9–68` | §9 constraints, §7 | P1 system |
| S2 | Brand mark is a CSS gradient tile with "BP" initials; no supplied asset is used. | `ui.py:40, 74` | §7 brand | P1 system |
| S3 | Pulsing "live" dot next to "Last checked" (reduced-motion handled). | `ui.py:47–49`, `app.py:1055` | §5A, Law 25 | P1 system |
| S4 | Explore is a `run_every=60` fragment. A periodic rerun can disturb a user mid-task. | `app.py:72, 1011–1012` | §5A, §9 | P1 nav |
| S5 | Explore table selection opens details through `on_select`. A separate "Review a contract" selectbox is the keyboard path, but it is placed under pagination with a generic placeholder. | `app.py:1112, 1126` | §5A | P1 nav |
| S6 | Admin "Contract IDs" splits on commas only; more than 3 IDs gives an info message without naming the rejected IDs. | `app.py:342–346` | Law 24 | P1/P2 |
| S7 | Combined "Move contract and save for YOLO" action still exists inside the photo-labeling panel. | `app.py:~874–899` | §4, §5E | P1 review |
| S8 | `contractor_performance` returns a `Status` column (raw reported status) and `None` for no score. Rendering shows "None" and unformatted totals. | `project_review.py:330`; `app.py:1572+` | §3.12, §5C | P1 regional |
| S9 | Regional dashboard has no coverage or freshness framing (saved count, review date range, stale count). | `app.py:1313+` | §5C | P1 regional |
| S10 | Tester shows raw eligibility `review_only`, renders detector text through `st.write`, and labels the result "YOLO prediction". Title is a bare "BantAIProyekto". | `yolo_test_app.py:66, 103, 138–140` | §5E | P2 |
| S11 | Tester rejects photos above 12 MB, but the uploader widget advertises the default 200 MB limit (confirmed live, L5). | `yolo_test_app.py:100, 108`; `.streamlit/config.toml` has no `server.maxUploadSize` | §8 microcopy | P2 (small fix candidate for P1) |
| S12 | Tester training status "Candidate ready" is not distinguished from evaluated or approved. | `yolo_test_app.py:46–54` | §5E | P2 |
| S13 | `st.set_page_config` uses a Material icon as favicon instead of the brand mark. | `app.py:291` | §7 | P1 system |
| S14 | Regional queue, reruns and tester progress refresh every 3 s (`run_every=3`). Fine for progress; must not reset inputs. | `app.py:1685, 1719`; `yolo_test_app.py:25` | §5D | P2 |
| S15 | Streamlit 1.67 supports per-theme semantic colours (`redColor`, `…BackgroundColor`, `…TextColor` for red/orange/yellow/blue/green/violet/gray), `headingFont`, `fontFaces`, `linkColor`, `buttonRadius`, chart palettes and `st.logo(image, icon_image=…)`. Most of S1 can therefore move into `config.toml`. | installed `streamlit` config options (listed 2026-11-16) | §9 constraints | P1 system |
| S16 | Not displayed but noted: the `project_review.INSTRUCTIONS` Ollama string contains mojibake `Neutral Â·`. | `project_review.py:89` | — | Out of UI scope; owner to decide |

Evidence rules that must stay untouched by all UI work are listed in §5 under "Constraints" (thresholds, latest-date rule, eligibility, scoring, manual overrides, history, provenance, caching, queue semantics).

### 2.3 Live UI observations (L)

Method: `streamlit run` with a disposable fixture database (three seeded reviews and one regional queue). Explore used the **live BetterGov API** (HTTP 200, about 1 s). Pages were captured in headless Chrome 1440×auto and 390×auto through the DevTools protocol, with a 14-step Tab sample per screen. Environment: Windows 11 10.0.26200, Python 3.13.4, Streamlit 1.67.0, Chrome stable. Screenshots are kept outside the repository (session scratchpad).

| # | Screen | Observation |
| --- | --- | --- |
| L1 | Explore 1440 | Four metric cards sit **above** the search (plan: search first). Card labels and the budget value truncate ("Reported completed · this …", "₱985,367,284…"). |
| L2 | Explore 1440 | The results table overflows inside its frame: "Reported status" is cut at the right edge, and project descriptions (all-caps source text) truncate. Contract IDs mix `O` and `0` (e.g. `26OH0086`), which is hard to read in Inter. |
| L3 | Explore 1440 | The keyboard-path picker "Review a contract" reads "Choose an option" and sits below pagination, far from the table. "Last checked … PHT" has a pulsing dot. |
| L4 | Explore 390 | The sidebar opens expanded and covers the content. The four metric cards stack before the search, so the search starts about 800 px down. No page-level horizontal overflow (scrollWidth = 390). |
| L5 | Tester 1440 | The uploader says "200MB per file" (contradicts the 12 MB rule, S11). There is no brand mark. The missing-checkpoint state is an info box. |
| L6 | Regional 1440 | The chart uses one teal gradient for every category (colour carries no meaning), and the count labels at bar ends are barely visible on the dark surface. A table view exists (P0). |
| L7 | Regional 1440 | The score table shows "None" and "3.000000". Positive scores get a green cell tint. The score-level chips use the interaction teal and read as clickable. The project table header still says "Status". |
| L8 | Regional 1440 | A broken photo renders as a bare broken-image icon with no text fallback (the fixture URL is fake, but the missing fallback is real). |
| L9 | Model status 1440 | The table scrolls internally after about 10 rows. The SHA256 column is empty when not installed. Eligibility wording from P0 renders. |
| L10 | All screens | Focus outline visible (2 px) on buttons, inputs, selects and help icons. **Expander summaries** ("Regional totals", "Train with saved corrections") and the page-header block reported `outline: none` when focused; visual focus there is unconfirmed. |
| L11 | All screens | 0 unlabeled inputs. No page-level horizontal overflow at 1440, 1024, 390, 360 or 320 px. |
| L12 | Admin review (P0 check) | The three-region evidence overview renders in dark and light themes. Badges now carry the short kind; the full label wraps (truncation defect fixed in P0). |

## 3. Elements visual audit and asset decisions

All four images were opened individually and colour-sampled with Pillow (region mode values, quantised to 4). They are raster PNGs, so colours vary slightly between boards. Values are measurements, not approved brand specifications.

### 3.1 `Logo.png` (1254 × 1254)

- **Content:** the icon-only mark. A white ant head with two curled antennae and three round red-ringed eyes, a black-rimmed magnifier over the front eye (pale blue lens, white glint), three "alert" strokes at top right, and a white hand holding the handle. All on a red rounded square (large radius, about 20% of the side) with a wide cream margin.
- **Palette (M):**
  - field red `#CB2C1D` / `#C2281B`
  - outline `#282827`
  - lens `#D7ECF5`
  - cream margin `#F6F3F0`
- **Typography:** none.
- **Icon treatment:** flat fills, thick uniform black outline only on the magnifier, no gradients, rounded terminals everywhere.
- **Layout cues:** subject cropped off the bottom edge of the tile (the ant "peeks" into frame).
- **Classification: direct-use asset.** Use it for the favicon, collapsed-sidebar icon and tester header after trimming the cream margin into a derived copy. Fine detail (glint, hand) gets lost below about 32 px, so a simplified favicon is needed at 16 px (see 3.3).

### 3.2 `Logo System.png` (1448 × 1086)

- **Content:** a board with a red title pill "LOGO SYSTEM" and four variants:
  1. **Primary (horizontal):** full ant body + magnifier, a vertical black rule, then the wordmark on two lines "BantAI / Proyekto".
  2. **Stacked:** mark above the wordmark.
  3. **Icon-only:** red tile, same as `Logo.png` but uncropped body.
  4. **Monochrome:** black-only mark, no wordmark.
- **Palette (M):**
  - wordmark ink `#181818`
  - "AI" red `#D02C18`
  - board cream `#FCF8F0`
  - label pill sand `#ECE4D8`
  - lens `#DCF0F8`
  - monochrome ink `#202020`
- **Typography:** the wordmark is a heavy rounded geometric sans, extra-bold, tight letter-spacing, single weight. "Bant" and "Proyekto" are ink and "AI" is red, so the colour split encodes the product name pun. Board labels are small tracked uppercase in pills; that is presentation chrome, not product typography. The face is not identified on the board; no font name is claimed.
- **Layout cues:** generous clear space around each variant; the vertical rule separates mark from wordmark.
- **Classification: reference board.** Do not ship the board or crops of it as the app logo: it carries labels, the wordmark is black-on-cream (invisible on a dark theme), and crops would be raster approximations. The wordmark is re-set as live text (§4.2) until a vector wordmark exists.

### 3.3 `Icon Set.png` (1448 × 1086)

- **Content:** horizontal lockup at top, a red pill "Icon Set & Favicon", then six tiles:
  1. Primary App Icon (ant + magnifier)
  2. Ant Eyes Icon (cropped)
  3. Magnifier Icon on a cream tile
  4. Favicon (ant face only, no tile)
  5. Alternate A (tight crop with lens)
  6. Alternate B (eyes + alert strokes, "Discover / Investigate")
- **Palette (M):**
  - icon red `#C82C1C`
  - magnifier tile `#F4F0E8`
  - caption ink `#202020`
- **Typography:** captions in a bold rounded sans, mixed case, with lighter parenthetical descriptors. Consistent with the wordmark family.
- **Icon treatment:** one consistent language. Red tiles with a large corner radius, white or red body shapes, black magnifier outline, pale-blue lens. The magnifier alone is a recognisable search glyph.
- **Classification: reference board.** The favicon concept (ant face only) is the right 16–32 px simplification but exists only inside the board. **Decision:** derive the favicon from `Logo.png`. Per the owner's B1 decision, no separate export will be supplied. The standalone magnifier is not used as the app's search icon; Material `search` stays, for consistency with the other Material icons Streamlit uses.

### 3.4 `Business Card.png` (1448 × 1086)

- **Content:** a front card with the horizontal lockup and contact row (email icon, domain, globe icon), plus a back with a repeating pattern of ant heads, antennae, magnifiers and alert strokes on cream.
- **Palette (M):**
  - card face `#F8F4F0`
  - pattern red `#D03020`
  - ink `#1C1C1C`
- **Typography:** contact text in a medium-weight rounded sans.
- **Layout cues:** large rounded card corners. The pattern shows the brand tolerates playful repetition in print.
- **Classification: presentation mock-up.** The contact email and domain are not verified product details and **must not appear in the app**. The pattern is decorative and has no place in an evidence interface (it would compete with photo evidence, Laws 25/28).

### 3.5 What the boards do not define

There is no dark-mode treatment, no UI component styling, no semantic or status colours, no named typeface, no spacing scale, no data-visualisation palette and no guidance on red versus error states. The boards describe an **identity**, not a complete visual system. §4 fills those gaps and records the reasons.

### 3.6 Asset-use decisions

| Need | Source | Decision |
| --- | --- | --- |
| Favicon (`page_icon`) | `Logo.png` | Derived, trimmed, square PNG at 64 px (B1: `Elements/` is the complete source). Check legibility at 16 px in P1a. |
| Collapsed sidebar icon (`st.logo icon_image`) | `Logo.png` | Same derived trimmed mark. |
| Expanded sidebar identity | Mark + live text | Derived mark (32 px) + live-text "Bant**AI**Proyekto" set in the UI typeface at 800 weight, with "AI" in brand red. Live text is accessible, adapts to theme and stays crisp. Alt text for the mark: "BantAIProyekto logo". |
| Tester header | Derived mark | Same mark + "Model tester" title. |
| Wordmark image, monochrome, pattern | — | Not used until a vector source exists (wordmark) or a real need appears (others). |
| Derived files location | — | `ProjectWatchAI/static/brand/` (new; generated by a small script from `Elements/Logo.png`; originals untouched). Needs `server.enableStaticServing`, or pass a file path to `st.logo`, which needs no static serving. |

## 4. Design direction

### 4.1 Concept

**"The lens over the record."** The product's subject is public DPWH paperwork (reported facts) looked at through a magnifier (automatic photo observations), with a person deciding (reviewer decision). The identity already supplies all three materials: **paper cream and record ink** for facts and structure, **lens blue** for anything a detector saw, and **sand** (the boards' pill colour) for human annotations. **Brand red** stays in the identity: the mark and the "AI" in the name.

The one memorable element is the identity block plus page titles: the chunky ant mark and an 800-weight title echoing the heavy wordmark. Everything else is quiet. Evidence colours carry meaning only; nothing is decorative.

Rejected defaults, and why:
- **The plan's provisional navy surfaces and teal accent.** Neither appears in any brand asset, and the teal is also used for data, so it reads as both "clickable" and "meaningful" (L6, L7).
- **Brand red as the action colour.** Red buttons would be confused with errors and with flagged evidence, the risk the plan names in §7.
- **A cream page with a terracotta accent and serif headlines.** That is a generic look; this palette instead uses a cream page with ink actions and a cool lens-blue meaning colour, all sampled from the boards.
- **Green for positive evidence or scores.** Plan §7 forbids implying certified completion.

### 4.2 Typography

| Role | Choice | Reason |
| --- | --- | --- |
| Single UI family | **Atkinson Hyperlegible Next** (SIL OFL), weights 400/600/800, fallback `system-ui, "Segoe UI", sans-serif` | Built for character disambiguation (`0/O`, `1/l/I`). Contract IDs such as `26OH0086` mix these (L2). One family keeps Prägnanz; hierarchy comes from weight and size. The plan's "system sans" target is kept as the fallback; the change is recorded as a deliberate deviation. |
| Page title (h1) | 30 px / 800 / letter-spacing −0.02em / line-height 1.15 | Echoes the wordmark's heavy rounded mass without imitating its unknown face. |
| Section (h2/h3) | 22 px / 800 and 18 px / 600 | Plan §7 scale 20–24 px. |
| Body / supporting | 16 px / 400, line-height 1.55; supporting 14 px / 400, line-height 1.5 | Plan §7. Explanatory prose limited to about 70 characters wide. |
| Numbers | `font-variant-numeric: tabular-nums` for amounts and counts | Comparable PHP amounts. |
| Not used | All-caps tracked eyebrows (`PROJECT INTELLIGENCE`, `ui.py:166`) and monospace data labels | They add no information; the breadcrumb already locates the page. |
| Loading | Self-host via `theme.fontFaces` + static serving. Files are vendored in `ProjectWatchAI/static/fonts/` (B5). | Avoids a third-party request from a public-accountability tool and works offline in Docker. |

### 4.3 Colour tokens (measured, M)

Contrast is computed with the WCAG 2.x relative-luminance formula for each pair below. **Rendered contrast is pending** until P1 applies the tokens and the pages are re-measured in the browser.

| Token | Light | Dark | Pair checked → ratio (need) |
| --- | --- | --- | --- |
| page | `#F6F3EE` (paper cream, from `#F6F3F0`–`#FCF8F0`) | `#121110` (ink-derived, warm) | — |
| surface | `#FFFFFF` | `#1C1A18` | — |
| sunken surface | `#EFEAE2` | `#262320` | text on it: 14.23 / 13.29 (4.5) |
| divider border (decorative) | `#D9D2C7` | `#3A3631` | not an essential boundary |
| control border | `#8C857B` | `#7D766C` | on surface 3.65 / 3.87; on page 3.30 / 4.20 (3.0) |
| text | `#1C1C1C` (record ink, from `#181818`–`#202020`) | `#F1ECE4` | on page 15.40 / 16.04; on surface 17.04 / 14.76 (4.5) |
| secondary text | `#57524B` | `#B9B1A6` | on page 6.99 / 8.89; on surface 7.74 / 8.18 (4.5) |
| **primary action** (`primaryColor`) | `#1C1C1C` fill, white text | `#F1ECE4` fill, `#1C1C1C` text | label 17.04 / 14.49 (4.5); fill vs page 15.40 / 16.04 (3.0) |
| focus ring | `#1F6F98` (deep lens) | `#7CC4E6` | on page 5.01 / 9.79; on surface 5.55 / 9.00 (3.0) |
| brand red (identity only) | `#C9291B` (**owner-confirmed 2026-11-16**; matches the median of sampled `#C2281B`–`#D02C18`) | `#C9291B` in the mark image; "AI" text uses `#F0675A` | on page 4.97 / 3.43 (3.0, large text only) |
| "AI" wordmark text | `#C9291B` | `#F0675A` | on surface 5.50 / 5.61; on page 4.97 / 6.10 (3.0 large) |

Links use ink/cream text with an underline, not a colour (`theme.linkColor` = text colour, `linkUnderline = true`).

### 4.4 Semantic colours mapped to evidence meaning

Every state pairs colour with an icon and a word (P0 `KIND_STYLE`). These map onto Streamlit's built-in colour slots through `config.toml`, so `st.badge`, `:color[]`, alerts and dataframe styling share them.

| Meaning | Streamlit slot | Light text / background | Dark text / background | Contrast L / D (4.5) | Chart mark L / D on surface (3.0) |
| --- | --- | --- | --- | --- | --- |
| Automatic observation (lens) | `blue` | `#174E6B` / `#E3F1F8` | `#A8DCF2` / `#16303D` | 7.78 / 9.31 | `#2B7FA8` 4.46 / `#6CC0E6` 8.53 |
| Reviewer decision (sand) | `violet` slot, re-coloured | `#5A4220` / `#F0E8DB` | `#E7D3B3` / `#322A1F` | 7.73 / 9.66 | `#8C6A3A` 4.96 / `#C9A876` 7.72 |
| Needs review / construction cue | `orange` | `#7A4B00` / `#FCEFD3` | `#F5C46B` / `#3A2C12` | 6.50 / 8.39 | `#B7791F` 3.64 / `#E0A647` 8.03 |
| Analysis failed / error | `red` | `#A1271B` / `#FBE4E1` | `#FF9E92` / `#3D1C19` | 6.13 / 7.66 | `#B4382C` 5.93 / `#F0796C` 6.33 |
| Evidence gap / source / neutral | `gray` | `#4D4A45` / `#ECEAE6` | `#CFC8BE` / `#2A2724` | 7.34 / 8.95 | `#8A847B` 3.71 / `#9D968C` 5.93 |
| Saved (persistence only) | `green` | `#1F6334` / `#E2F2E6` | `#8FD6A4` / `#17301F` | 6.25 / 8.33 | — (never used on evidence) |

Rules:
1. Green appears only on "Decision saved" / "Review refreshed" confirmations, never on categories, scores or badges. A P0 test asserts that no category badge is green.
2. Positive contractor scores get **no colour tint** (replaces the green/red cells at `app.py` `point_style`). The sign and number carry the meaning; "No score" is text.
3. Brand red versus error red: they are similar hues, so separation is by context and form. Brand red only appears in the mark and the two wordmark letters in the sidebar. Error red always appears with the `error` icon and the word "failed". Users' ability to tell them apart is a research question (§8, R4).
4. Regional chart: bars coloured by **evidence kind** (table above), count labels in text colour, and the existing table view retained. This replaces the single teal gradient (L6).

### 4.5 Shape, spacing, controls, motion

| Aspect | Decision | Reason |
| --- | --- | --- |
| Spacing scale | 4, 8, 12, 16, 24, 32, 48 px | Plan §7. |
| Radius | Controls and buttons 8 px (`baseRadius`, `buttonRadius`); bordered containers 12 px; badges full pill | Two radii only, keyed to hierarchy. Echoes the rounded logo tile without copying its very large radius. |
| Elevation | No drop shadows on cards (removes `box-shadow` in `ui.py:22`); 1 px borders only | Shadows on every card flatten hierarchy (Law 28). |
| Control height | 44 px minimum for buttons, inputs and selects; 8 px between adjacent actions | Plan §7 / Law 7. |
| Primary vs secondary | Exactly one filled ink button per task area; others outlined | Laws 10 / 28. |
| Motion | No ambient animation. Remove the pulsing dot and replace it with a static freshness label ("Source checked 17:37 PHT"). Keep Streamlit's own transitions that answer user actions. | Law 25; plan §5A. |
| Icons | Material Symbols (already bundled by Streamlit), decorative icons `aria-hidden`; brand mark has alt text | Consistency with framework icons; §7 accessibility. |

### 4.6 Layout direction

```text
Wide ≥1200 px                                      Small <768 px
┌───────────┬────────────────────────────────┐     ┌──────────────────────────┐
│ [ant] Bant│ Workspace / Explore projects   │     │ ☰  [ant]  Explore        │
│ AIProyekto│ Explore public projects (h1)   │     │ Search ……………………………………     │
│           │ one-line purpose               │     │ Region | Year            │
│ Explore ● │ ┌ Search ───────────────────┐  │     │ ▸ More filters           │
│ Regional  │ │ ID/description  Region Year│ │     │ 17,052 matching · reset  │
│ Admin     │ │ ▸ More filters · Reset     │ │     │ [contract card] Open     │
│           │ └────────────────────────────┘ │     │ [contract card] Open     │
│ source    │ 17,052 matching · source 17:37 │     └──────────────────────────┘
│ note      │ table: ID/project | Region |   │
│           │  Reported status | Photo evid. │
│           │  | Last reviewed  [Open]       │
└───────────┴────────────────────────────────┘
```

- Left-aligned content throughout; sentence case; no all-caps eyebrows.
- Page metrics move **below** the search and become a compact line or two cards; the search is the first control (L1, L4).
- Contract detail and review workspace follow plan §5B: identity, then the three-region evidence overview (P0), then a 60/40 evidence/decision split, then timeline/budget and provenance.
- Sidebar: `initial_sidebar_state="auto"`, so it collapses on small screens (L4); compact navigation shows the mark only.

### 4.7 Implementation mechanism (P1)

1. Move colours, fonts, radii, link style and chart palettes into `.streamlit/config.toml` (`[theme.light]`, `[theme.dark]`, semantic slots, `fontFaces`, `headingFont`, `chartCategoricalColors`).
2. Reduce `ui.apply_theme` CSS to a few documented rules (focus ring, 44 px controls, tabular numbers, prose width), and remove the hex string-replacement light mode (S1).
3. Brand: `st.logo(mark, icon_image=mark)` with alt text; `page_icon` set to the mark; the sidebar wordmark as live text.
4. Verification after applying: rendered contrast re-measured in both themes, screenshots at 320/360/768/1024/1440 and at 200% zoom, plus a keyboard pass including expander focus (L10).

## 5. Plan checklist (every section)

Legend: ✅ done and verified · ◐ partial · ☐ not started · 👥 needs participants/human decision.

**§1 Purpose and scope**
- ✅ Project evidence reviewed: README, `ui.py`, `app.py`, `contract_assessment.py`, `evidence_policy.py`, `project_review.py`, `yolo_test_app.py`, all four `Elements/` images (this audit).
- ✅ Laws of UX list rechecked: the plan's 30 principles are used. The site was not re-fetched in this step. 👥 Recheck at P1 kickoff (plan §11).
- 👥 Personas and task priorities are hypotheses until research R1.

**§2 Users, goals and success criteria**
- ☐ 👥 ≥90% task success, ≤30 s median contract location, ≥90% correct interpretation: need participants (R1).
- ◐ Save/retry/rerun preservation: draft kept on missing reason (AppTest); simulated DB save-failure test is P1 review workspace.
- ☐ 400 ms acknowledgment: measurement is P2.
- ◐ Keyboard operability: Tab sample done (L10). Full workflows are P2.
- ✅ Admin is not shown as an access boundary (no change made; keep it that way).

**§3 Evidence and trust (P0)**
- ✅ Items 1, 2, 3, 6, 7, 8 (see §7).
- ◐ Items 4, 5, 9, 10, 11, 12. Remaining parts are assigned to P1 review/regional (§7 traceability).

**§4 Information architecture**
- ✅ Three top-level destinations retained.
- ☐ Contract detail sub-sections (overview, photos, timeline/budget, sources/history) are P1 review.
- ☐ Separate category-decision and training-correction forms are P1 review (S7).
- ◐ Context preserved through reruns: per-contract keys (P0); Explore filters, page, selection, view and detail columns survive reruns, page switches and Back to results (phase 4).
- ☐ Explicit operator link to the tester when configured: P2.

**§5 Screen specifications**
- A Explore ✅ (phase 4, §7d): search first; Region and Fiscal year beside it; More filters; active-filter chips with the default fiscal year named; Reset filters; plan column order plus opt-in detail columns; row selection and labelled Open contract; selection restored after Back; separate source-check and review times; photos-only counts kept apart; initial, loading, no-results and outage states; List view as the compact alternative. Screen-reader output not verified (B4).
- B Contract detail ◐: evidence overview done (P0). Photo group navigation, overlay legend, text finding per photo and save confirmation placement are P1 review.
- C Regional ✅ (phase 6, §7f): coverage/freshness header (S9), measure definitions, drilldown with visible filter, kind-in-words category rows (L6), No score text and formatted numbers (L7, S8), comparison after coverage with formula, limits and timing note, linked highlights with failure fallback, empty-state route to Admin. Traceability table in §7f.
- D Admin queues ✅ (phase 7, §7g): scope and workload before the action; real stages; completed/total only when known; Pause requested vs Paused vs Interrupted from worker state; one failed contract no longer stops the run; Finished / Finished with problems; scoped Retry; Open results; model readiness with next actions. Open: resume after a scoped retry, due-recheck effect on the reader page.
- E Photo corrections/tester ◐: separate forms and provenance message done (phase 5); Evaluation required / Candidate ready / Approved active are distinct (S12, phase 7, §7g). ☐ 12 MB limit text in the uploader (S11) is P2b.

**§6 Laws of UX matrix**
- ◐ See §6 below: every row has a location, a verification method and a status.

**§7 Visual system and responsive behaviour**
- ✅ Brand assets audited; direct-use versus reference decided (§3).
- ✅ Tokens chosen and contrast **calculated** for all pairs (§4.3–4.4). ☐ Rendered contrast after P1.
- ☐ Logo variants in navigation and favicon (P1 system).
- ☐ Responsive: sidebar auto-collapse (L4), filter expander, contract-card alternative below 768 px, 320 px and 200% zoom checks.
- ◐ Accessibility: labels (0 unlabeled), focus visible on most controls; expander focus (L10) and reduced motion (pulse removal) are open. Screen-reader testing and announcement checks were **skipped by owner decision (B4)** and remain unverified.

**§8 State and microcopy inventory** (status per state)
- ✅ Detector unavailable, inconclusive analysis, no source photos, photo retrieval failed, historical photo, stale saved review, missing decision reason, save succeeded (P0 wording).
- ◐ Save failed (text present; simulated failure test pending). Highlights unavailable (P0 wording; P1 regional layout).
- ☐ Initial search example + visible year (partly present), No matching results with active-filter list, No saved regional results with Admin link, Latest-date conflict links per photo, Source/API outage with retained review timestamp, Pause requested/paused.

**§9 Streamlit implementation plan**
- ✅ P0 complete.
- ☐ P1 ×3, P2 ×2 (sequence in §8).
- Constraints (apply to every phase): native controls first; minimize `data-testid` CSS (S1/S15); stable keys scoped to contract/photo IDs; reuse content-hash/checkpoint inference cache; cosmetic work must not trigger inference; escape external text (P0: `st.text`); raw JSON and hashes only in provenance details; new draft or preference storage kept separate from evidence decisions, with a migration plan if added; no implied multi-user authentication.
- Evidence rules that UI work must not change: latest-date rule, thresholds (roads 35%, buildings 24%, bridges 50%, slope 50% only when validated), eligibility, budget 10% rule, scoring formula, manual override precedence, review history trigger, provenance, caching keys, queue semantics.

**§10 Validation and release checklist**
- 👥 Prototype and formative study (about 5 participants, 7 tasks): not run (R1).
- ◐ Functional scenarios: empty, failed analysis, legacy manual and stale covered by fixtures. Slow source, partial results, conflicting photos and missing models with real checkpoints still needed.
- ✅ Existing regression suite runs (§8 commands).
- ◐ Visual widths: 1440/1024/390/360/320 checked. 768 and 200% zoom pending.
- ☐ Screen reader and latency measurement.
- Release criteria: none of the 10 boxes can be ticked yet. Criteria 2 and 4 are substantially met by P0, but need re-verification after P1 visual changes.

**§11 Source notes**
- ✅ WCAG 2.2 AA used as the contrast/focus reference. The Laws of UX index needs re-checking at P1 kickoff (not fetched in this step).

## 6. Laws of UX (30 rows)

Status: **Done** = implemented and checked automatically or rendered · **Partial** · **Planned** · **Needs participants** for the acceptance check in plan §6 regardless of implementation.

| # | Principle | Implementation location | Verification method | Status |
| --- | --- | --- | --- | --- |
| 1 | Aesthetic-Usability Effect | Tokens in `.streamlit/config.toml` + `ui.py` (P1); qualified wording in `evidence_language.py` (P0) | Rendered screenshots both themes; R1 accuracy-vs-preference task | Partial (P1a tokens applied both themes) · Needs participants |
| 2 | Choice Overload | Explore "More filters" expander (`app.py` `show_live_projects`); eligible-only decision options (`eligible_review_categories`) | AppTest: decision options exclude ineligible; R1 novice search task | Done (P1a: More filters; AppTest) · Needs participants |
| 3 | Chunking | `ui.evidence_overview`, `ui.show_flags`; contract detail sections (P1) | AppTest headings; screenshot review | Done (P0) / P1 for detail sections |
| 4 | Cognitive Bias | Source facts first, neutral wording, counter-evidence beside findings (`evidence_language.CATEGORIES`, `finding_text`) | Unit tests forbid certainty/accusation wording; R1 same-date conflict task | Done (P0) · Needs participants |
| 5 | Cognitive Load | Plain finding first; JSON/hashes in expanders (`show_model_status`, provenance expander) | Screenshot review; R1 "explain the finding" | Partial |
| 6 | Doherty Threshold | Action acknowledgment and stage feedback (`show_regional_queue`, buttons) | Timing harness on the documented environment (P2) | Planned |
| 7 | Fitts's Law | 44 px controls, 8 px gaps (`ui.apply_theme` / config, P1) | DOM measurement of control boxes at 390 and 1440 px | Done (P1a: 44 px text buttons measured in DOM) |
| 8 | Flow | Per-contract keys (`manual_category_*_{contract}`); inspect-decide-save-next (P1 review) | AppTest draft retention; 5-contract walk-through script | Partial |
| 9 | Goal-Gradient Effect | Real completed/remaining counts in queues (`show_regional_queue`, `show_category_reruns`) | Interrupt/resume fixture test (P2) | Planned |
| 10 | Hick's Law | One filled primary per task area (P1 tokens + button `type`) | Screenshot audit: count primaries per screen | Partial (one form action) |
| 11 | Jakob's Law | Breadcrumb, Back to results, labelled Open contract (`app.py` Explore, P1) | Keyboard/AppTest round trip; R1 first-time open/return | Done (P1a: breadcrumb nav, Open contract, Back to results; AppTest + CDP keyboard path) · Needs participants |
| 12 | Law of Common Region | Bounded regions in `evidence_overview`; photo + date + findings panel (`show_training_feedback`, P1 overlay legend) | Screenshot review; R1 attribution task | Partial |
| 13 | Law of Proximity | Eligibility on the same line as each finding; flag reasons under flags; field errors under fields (P1) | Screenshot review; AppTest error placement | Partial |
| 14 | Law of Prägnanz | Single type family, two radii, three evidence regions (§4) | Rendered review both themes | Done (P0 regions; P1a single family, flat surfaces) |
| 15 | Law of Similarity | `ui.category_badge` + one mapping everywhere | Unit test (labels unique, used everywhere); screenshot review across 3 destinations | Done (P0) |
| 16 | Law of Uniform Connectedness | Cue messages cite photo numbers; finding to photo selection and timeline (P1) | AppTest: selecting a finding selects its photo | Partial |
| 17 | Mental Model | Region captions name the source: DPWH record, detectors, reviewer (`evidence_overview`) | AppTest text; R1 "who said this?" question | Done (P0) · Needs participants |
| 18 | Miller's Law | Grouped flags; photo role labels latest/historical/unknown | Screenshot review; R1 comparison task | Partial |
| 19 | Occam's Razor | Shared components replace duplicate captions; CSS reduced to config (P1) | Code review: count of `data-testid` selectors (15 → target ≤ 3) | Done (P1a: `data-testid` selectors 15 → 0) |
| 20 | Paradox of the Active User | Inline examples: reason placeholder (P0), search example (exists), training-source example (P2) | R1 novice tasks without a manual | Partial |
| 21 | Pareto Principle | Prioritised P1 scope: find, read, decide, resume | R1 task frequency and cost log | Needs participants |
| 22 | Parkinson's Law | Explicit scopes and small batches (regional filters, 3-ID limit) | P2 batch walkthrough | Planned |
| 23 | Peak-End Rule | "Decision saved for …" (P0); run-end summary with failed/pending (P2) | AppTest save message; P2 interrupted-run fixture | Partial |
| 24 | Postel's Law | Contract-ID parser accepting case, whitespace, commas and newlines; extra IDs listed and rejected (`show_admin`, S6) | Unit tests on the parser | Planned |
| 25 | Selective Attention | Remove pulse (S3); grouped flags; info versus warning hierarchy | Screenshot audit; reduced-motion check | Done (P1a: pulse removed; static freshness label) |
| 26 | Serial Position Effect | Identity and status first (P0); save outcome placed at the end of the form (P1) | Screenshot review; R1 recall question | Partial |
| 27 | Tesler's Law | System computes timing, eligibility and budget basis (`evidence_language`, `contract_assessment`) | Unit tests on timing and flag text | Done (P0) |
| 28 | Von Restorff Effect | One filled primary; no card shadows; colour reserved for meaning (§4.4–4.5) | Screenshot audit of each screen | Partial (P1a: no card shadows; colour reserved for meaning; one primary per area still to audit in P1b/c) |
| 29 | Working Memory | Current category beside the form; selected contract and draft persist | AppTest across reruns | Partial |
| 30 | Zeigarnik Effect | Resume with remaining counts and last completed step (`show_regional_queue`) | P2 pause/resume fixture | Planned |

## 7. P0 implementation record (2026-11-16)

### What changed

| File | Change |
| --- | --- |
| `ProjectWatchAI/evidence_language.py` (new) | Pure presentation mapping (no Streamlit import) for stored categories, outcomes, flags, eligibility, detector results, photo timing and score levels. Includes reversible `category_label`/`category_value` and `score_level_label`/`score_level_value`, and an idempotent `relabel_text` for saved generated text. |
| `ProjectWatchAI/ui.py` | Adds `evidence_overview` (reported status, automatic assessment and reviewer decision as three bounded regions plus the limitation), `category_badge` (short kind badge, full label as wrapping text), `show_flags` (deduplicated, grouped, with reasons) and `escape_markdown`. |
| `ProjectWatchAI/app.py` | Labels via `format_func` while widget values stay raw stored values. Evidence overview used in saved review, regional detail and Admin contract review. External text via `st.text`. Eligibility and "model score" wording; distinct missing/failed/negative text. Explore "Reported status" wording, and "Completed" no longer green. Contractor score wording and No-score rule. Model status eligibility column. "Generate evidence highlights". |
| `ProjectWatchAI/test_evidence_language.py` (new) | 22 tests, including 4 AppTest runs of the real `app.py` on a seeded disposable database. |

### Raw-value preservation

- Stored categories, outcomes, flags and score levels are unchanged. Scoring prefix checks, `flag_is_discrepancy`, requeue rules, Ollama fact text and cache keys behave as before. `review_project` text was not edited because it feeds the Ollama batch cache key.
- One additive field: `assessment.automatic_evidence_category`, written when a manual decision replaces the automatic category. It sits in `assessment`, which `evidence_fingerprint` excludes. A second decision never records a manual value as "automatic". Older decisions show a recomputed, labelled value. No schema change.
- Unknown and legacy stored values stay visible: `Visible scope evidence · legacy saved label: …`, or `Saved label: …` on a collision. Both round-trip.

### Wording map (main cases)

| Stored | Displayed |
| --- | --- |
| `Completion evidence found · on reported date / 1–7 / 8–31 / more than 31 days later` | `Visible scope evidence · photo captured on reported completion date / 1–7 / 8–31 / more than 31 days after reported completion` |
| `Completed · manual review` | `Manual decision · marked complete by reviewer` |
| `Neutral · further review needed` | `Further review needed · latest photos not conclusive` |
| `Photos precede reported completion` | `All dated photos precede reported completion` |
| `No photo evidence` / `Photo evidence unavailable` / `Photo analysis unavailable · retry needed` | `No photos in source record` / `Photos could not be retrieved` / `Photo analysis failed · retry needed` |
| `Completion not reported` | `Completion not reported in source` |
| Score levels `Early … More than 31 days late` | `Reported complete before / on / 1–7 … days after contract expiry` |
| `Unscored…` | `No score…` |

### Plan §3 traceability

Updated after the phase 2 completion pass (§7b). "Policy gap" means the display is honest, but the underlying rule conflicts with the documented policy. See `docs/evidence-policy-inconsistencies.md`.

| # | Requirement | Result |
| --- | --- | --- |
| 1 | Three separate concepts | **Done** in saved review, Explore contract detail (read-only saved assessment, or "No saved photo assessment"), regional detail and photo panel, Admin contract review, summaries/comparison. |
| 2 | Visible scope evidence via presentation mapping | **Done**; source-scan test covers every emitted category; raw values appear only where labelled "Stored value". |
| 3 | Limitation beside assessment | **Done**, inside the assessment region. |
| 4 | Newest dated photo default; historical/unknown/conflict | **Done for labelling:** latest/historical/unknown labels in review, regional and Explore views; same-date conflict note (presentation-side, cites photos and the side the rule kept). Default photo ordering is P1b. **Policy gap A4:** the rule records no conflict flag. |
| 5 | Photo timing wording; date-only vs PHT | **Done:** date-only stays date-only; timestamps "16 Nov 2026, 10:00 PHT" via one helper (archive times converted from UTC); unknown says unknown. |
| 6 | Missing/failed/inconclusive/no-detection distinct | **Done:** one standard treatment per state (11 states), tested. |
| 7 | Eligibility beside findings | **Done** (saved, live gallery, overlay, Model status, tester). Experimental wording corrected to match actual rules: "Experimental · not validated … can still set a photo assessment". An assessment note appears when it relies on such a detector. **Policy gaps A1/A2.** |
| 8 | Model score wording | **Done**, including legacy "(NN% confidence)" text and the tester. **Policy gap A3** (undocumented 1% thresholds). |
| 9 | Amount basis, unknown ≠ zero | Partial (flag basis, Unavailable amounts). Full budget layout is P1b. |
| 10 | Manual decision preservation and re-review warning | Done for display. Simulated save-failure test is P1b. |
| 11 | Highlights from recorded facts | Wording done. Contract links are P1c. |
| 12 | Qualified scores | **Done for display:** "No score" text, formatted numbers, "Reported status" header, scoring-convention text. **Policy gap A6** (photo-timing deduction). |

### P0 verification

- `python -B -m unittest test_audit_regressions`: 23/23 before and after.
- `python -B -m unittest test_audit_regressions test_evidence_language`: 45/45.
- Rendered (headless Chrome, CDP, disposable DB) at 1440 dark, 1024 light, 360 and 320:
  - three regions and the limitation present;
  - no raw label;
  - no page overflow;
  - 0 unlabeled inputs;
  - badge truncation found and fixed.
- New reported-Completed cell colours: 9.55:1 dark, 8.24:1 light (M).

## 7a. P1a implementation record — shared system and navigation (2026-11-16)

### What changed

| File | Change |
| --- | --- |
| `.streamlit/config.toml` | Theme tokens from §4.3–4.4 for both themes, including semantic colour slots (`blue` = observation, `violet` slot = sand reviewer decision, orange, red, gray, green), link = text colour with underline, and chart colours by evidence kind. Atkinson Hyperlegible Next self-hosted through `fontFaces` (weight 200–800). Headings 30/22/18 px. `metricValueFontSize = 1.5rem`. Server/client: `enableStaticServing`, `maxUploadSize = 12` (fixes S11), `toolbarMode = "viewer"` (hides Deploy without CSS). |
| `ui.py` | `apply_theme` reduced to a small theme-independent block: focus ring, 44 px controls, tabular numbers, prose width. Removed the hex-swap light mode, card shadows, the pulse animation and **all 15 `data-testid` selectors** (two `data-baseweb` field-height selectors remain). New `brand_logo()` uses `st.logo` with derived marks plus the live-text wordmark. `page_header` drops the eyebrow and puts the breadcrumb in `<nav aria-label="Breadcrumb">`. `category_badge` now shows the evidence kind as coloured icon text, not a pill, because `st.badge` truncated at 1024 px. |
| `scripts/derive_brand_assets.py`, `static/brand/mark-{32..512}.png` | Reproducible trimmed marks from `Elements/Logo.png`; the original is unchanged (hash checked). |
| `app.py` main/styling | Favicon = brand mark; `initial_sidebar_state="auto"` (fixes L4); reported-status cells bold only; no green/red score tints. |
| `app.py` Explore | `show_live_projects` split into `show_live_search` / `show_live_results` / `show_live_table` / `show_live_contract` with helpers: active-filter summary, open/close contract, read-only `saved_review_index`. Changes: search first with the remaining filters under **More filters**; metrics reduced to one caption after the search; plan column order **Contract ID / project (pinned) · Region · Reported status · Photo evidence · Last reviewed**, where photo evidence comes from saved reviews and never triggers inference; labelled **Contract to open** + **Open contract** beside the table; **Back to results** keeps filters and page; Reset restores the default fiscal year; empty state lists active filters; source-outage message keeps filters. |
| `app.py` refresh | **The 60 s timer was removed.** With a contract open, each timed rerun redrew photo analysis and could re-run inference after the 15-minute cache expired. It could also land mid-interaction (S4). Refresh is now on filter change or **Refresh results**, next to a static "Source checked HH:MM PHT" line (pulse removed, S3). |
| `yolo_test_app.py` | Brand mark and "Model tester" title; plain eligibility wording; detector text through `st.text`; overlay caption "not a verification mark". |
| `test_explore_ui.py` (new) | 6 AppTest tests with faked API and disposable DB: search-first order and More filters contents; column order and saved-evidence labels; Reset restores the default year; Open contract then Back to results keeps region and page; literal project text; empty state; no timer. |

### Deviations from §4 (and reasons)

| Planned | Implemented | Reason |
| --- | --- | --- |
| Dark primary button cream `#F1ECE4` | Warm stone `#736B61` | Streamlit 1.67 always draws primary labels white; cream gave 1.18:1. Stone gives a 5.24:1 label, 3.6:1 against the page and 3.31:1 against the surface. |
| Theme-specific focus colours and dark "AI" `#F0675A` | One focus colour `#2A7FAD` (3.53–4.43:1 on all six surfaces); "AI" in owner-confirmed `#C9291B` in both themes | `st.context.theme` can be wrong on first load, so theme-dependent CSS is unreliable. Dark "AI" measures 3.15:1: passes large text (20 px, weight 800) but looks faint. **Open item.** |
| Containers 12 px radius | Theme radius 0.5 rem for everything | 12 px containers would need a `data-testid` selector. |
| Separate divider and control borders | Single `borderColor` = control border `#8C857B` / `#7D766C` | Streamlit has one border colour; inputs need 3:1. |
| Logo alt text "BantAIProyekto logo" | `st.logo` renders `alt="Logo"`, not configurable in 1.67 | The adjacent live-text wordmark names the brand. |
| Contract-card alternative below 768 px | Table with a pinned identity column, scrolling inside its frame | Streamlit cannot detect width server-side. Plan §7 allows horizontal scrolling for genuinely two-dimensional tables. |
| ID links in the results table | ID as plain text; "Open source record" in detail | Avoids a link competing with row selection. |

### Verification (P1a)

- `python -B -m unittest test_audit_regressions test_evidence_language test_explore_ui` → **51/51 OK** after integration.
- Rendered (headless Chrome via CDP; live BetterGov API for Explore; disposable fixture DB):
  - **Explore:** 1440 dark, 1024 light, 390.
  - **Admin:** 1440 dark, 1024 light, 390.
  - **Regional:** 1440 dark and light.
  - **Tester:** 1440.
  - **Results:** no page-level horizontal overflow; 0 unlabeled inputs; all button labels complete at 1024 after integration (two defects fixed by the lead: buttons truncated in narrow side columns, and the badge truncation); fonts load (`document.fonts.check` true, `.ttf` HTTP 200); text buttons 44 px; 2 px focus ring on all sampled elements including expanders (fixes L10).
- Rendered contrast (computed styles):

  | Element | Dark | Light |
  | --- | --- | --- |
  | Body text | 16.04 | 15.4 |
  | Primary button label | 5.24 | 17.04 |
  | Secondary button label | 15.22 | 16.08 |
  | Observation text | 9.31 | 7.78 |
  | Sidebar "AI" | 3.15 (large text) | 4.6 |

- Keyboard path in the browser: Contract to open → Arrow Down → Enter → Open contract loaded the detail view; Back to results returned to the list (Explore agent, fake API).

### Open after P1a

- Dark-theme "AI" letters are faint (3.15:1). Options: accept, or lighten only in dark if a reliable theme hook is found.
- At 390 px, horizontal radio rows (Admin sections) wrap with large gaps because of the 44 px label minimum.
- The tertiary "Refresh results" button looks faint when focused or hovered in dark mode; check it in P1b.
- Long "Photo evidence" labels truncate in the table cell (full text in detail and on hover).
- The mark is busy at 32 px and below.
- Regional chart still uses the teal gradient and clips axis labels at about 1024 px; the score table shows "None"/"3.000000". Both are P1c.

## 7b. Phase 2 record — P0 evidence presentation completion (2026-11-16)

Requested as "phase 2: implement the plan's P0 evidence presentation requirements". Most of P0 already existed (§7), so this pass audited every evidence display again and closed the gaps. Three parallel agents were used, then integrated and re-verified by the lead:
- a presentation agent (`app.py`, `ui.py`, `evidence_language.py`, tests);
- a tester agent (`yolo_test_app.py`, tests);
- a read-only policy audit agent.

### What changed

| Area | Change |
| --- | --- |
| Shared helpers (`evidence_language.py`, `ui.py`) | One standard treatment (kind, icon, plan §8 wording) for: no photos, retrieval failed, detector not installed, analysis failed, inconclusive, no detection, stale review, historical photo, unknown date, same-date conflict, no saved assessment. One date helper: date-only stays date-only; timestamps in PHT; unknown says unknown. Photo role labels. Score formatter ("No score", fixed decimals). Legacy "(NN% confidence)" text reads as "model score NN%, rounded". |
| Corrected wording (policy audit A1) | The P0 text "cannot set a category alone" for experimental detectors was **false**. It now reads "Experimental · not validated … under the current rules its detections can still set a photo assessment". An assessment note appears when supporting/cue photos come from an experimental detector. Test updated to assert the corrected claim. |
| Same-date conflict | Detected presentation-side from saved observations in the newest dated group. The note cites scope and cue photo numbers, which side the rule kept, and that no flag was recorded. No rule change. |
| Explore contract detail | Separate reported status, saved photo assessment (read-only; never runs inference; a test asserts the DB file is byte-identical) and reviewer decision. Distinct gallery-fetch failure vs empty gallery. Live detections labelled not saved. |
| Regional | Stale-review count up front. "No score" and formatted numbers. "Reported status" header. Readable dates. The three-part layout in the photo panel. Broken photos keep their caption and date. The category chart was replaced by labelled progress rows (kind · label · count in text; dark-theme Altair labels were unreadable); the table view is kept. |
| Admin contract review and comparison | Same three-part layout; generated text escaped. |
| Tester (`yolo_test_app.py`) | Model-score wording; "No target detected. This does not show that the feature is absent."; "Analysis failed"; "Not installed — …". Eligibility in plain words + "No detector status certifies that a contract is complete." Training states distinguish **Candidate ready for evaluation**, **Evaluation passed · not promoted**, and **Approved active** (the last only when the active checkpoint hash matches and is validated). The labelling form says it does not change a contract's category; a blank source ID shows "Excluded from training until source is identified." (save still allowed). Upload text matches 12 MB / 25 MP. |
| Policy findings | Documented separately in `docs/evidence-policy-inconsistencies.md`, with read-only reproductions in `docs/evidence-policy-repro.py`. 16 confirmed (5 high), 2 refuted. No rule changed. |

### Verification

- From `ProjectWatchAI/`:
  - `python -B -m unittest test_audit_regressions -v` → **23/23 OK** (evidence rules unchanged).
  - All suites (`test_audit_regressions test_evidence_language test_explore_ui test_evidence_presentation test_tester_ui`) → **84/84 OK**, run by the lead after integration.
- New tests:
  - `test_evidence_presentation.py` (22): PHT and date-only formatting, every standard state, conflict detection, experimental note, score tables, seeded Admin and Explore AppTest views, and a no-write check for Explore detail.
  - `test_tester_ui.py` (11): prediction wording, source-ID exclusion, every training state (including copied-but-unapproved and hash-mismatch cases not shown as Approved active), and 3 tester AppTest runs.
- Legacy and unknown coverage:
  - legacy Completion labels;
  - legacy prose observations without detections;
  - legacy manual decision without provenance;
  - unknown stored values;
  - category round trips (known, unknown, collision).
- Rendered (headless Chrome, disposable DB copy):
  - Admin 1440 dark shows the conflict note, PHT time and historical label (lead inspected);
  - Regional 1024 has no overflow, and its category rows are readable in dark (lead inspected);
  - tester 1440.
  - Explore detail with the live API was not screenshotted (needs real IDs); it is covered by mocked-API AppTests.
- Wording sweep (lead, `grep` over `app.py`, `ui.py`, `yolo_test_app.py`, `evidence_language.py`, `project_review.py`): the remaining hits are stored keys on the raw side of mappings, or the items below.

### Remaining misleading or weak wording

| Where | Text | Why it remains |
| --- | --- | --- |
| `project_review.plain_photo_finding` (live gallery; Admin contract review findings) | "AI detection found X." / "AI detection did not find X." | Rule/narrative module outside UI scope. The standard "does not show that the work is absent" note is shown beneath a no-detection result. Should become "Detected X (model score …)" in a reviewed narrative change; the narrative text also feeds Ollama fact text and cache keys. |
| README "Evidence policy" | Claims experimental detectors cannot assign categories, and slope protection is gated on release | **Contradicted by the rules** (A1, A2). The UI no longer repeats the false claim. README correction or a rule change is the owner's decision. |
| Contractor "Photo timing points" | A deduction for late photography | The display labels it a scoring convention and repeats the timing note, but the arithmetic still treats photo timing like delay (A6). Owner decision. |
| Tester | "Run YOLO", "Fine-tuning YOLO"; raw "Held-out mask mAP50" log lines | Accurate but technical; P2 operator pass. |
| Saved Ollama highlight text | Raw score levels and categories in old saved reports | Relabelled at display for known phrases; unknown phrases show as stored. |

## 7c. Phase 3 record — shared visual system, navigation and states (2026-11-16)

Two parallel agents did this phase, then the lead integrated and re-verified it:
- **Navigation and system agent:** `ui.py` styling, `config.toml`, `main()`, brand derivation.
- **States and actions agent:** `app.py` page bodies and `yolo_test_app.py`.

The lead first added the shared state API to `ui.py`, so both agents built on the same interface.

### Elements (re-audited)

`Elements/` now also contains owner-supplied vector exports that §3 did not cover:
- `wordmark_dark.svg` / `wordmark_light.svg`;
- `icon_only.svg` / `icon_only_1024.png`;
- `favicon_ant_face.svg` with 32/64 px PNGs and `favicon.ico`.

These are direct-use assets and replace the live-text wordmark from the B1 interim decision. `scripts/derive_brand_assets.py` derives the following without modifying the originals:
- `static/brand/wordmark-ink.svg` and `wordmark-ai.svg`. `st.html` strips inline SVG, so the wordmark is split into two image layers. The ink layer is drawn in the theme text colour; "AI" keeps the supplied red.
- `favicon-32/64.png` from the ant-face. It replaces the busy mark at ≤32 px (P1a open item closed).

The `mark-*.png` hashes are unchanged. No presentation board is used anywhere.

**Owner decision needed:** the vectors use red `#D93829`, while `#C9291B` was confirmed from the raster logo (B3). Both are kept as supplied. Brand red never marks errors: errors use the theme red slot with an icon and words.

### What changed

| Area | Change |
| --- | --- |
| Navigation | Native `st.navigation` with the three destinations kept:<br>• Explore projects at `/`;<br>• Regional review at `/regional-review`;<br>• Admin at `/admin`.<br>The active page gets `aria-current="page"`, plus a filled row, a 4 px bar and bold text (never red). Deep links and browser Back work. `go_to()` and the `primary_navigation` key still switch pages, and context survives round trips (tested). The breadcrumb names the current page. When collapsed (<768 px), the sidebar shows the ant-face, and the expand control keeps its "Expand sidebar" name at 44×44 px. |
| Colour without theme detection | Streamlit sets CSS `color-scheme` per active theme, so `light-dark()` in `ui.py` picks the focus ring, active row, borders and disabled text. `st.context.theme` is not used. |
| Buttons | One primary per task area (audited; Save decision, Open contract, Analyze contract evidence, Start/Resume review, Generate overview, Build summaries, Save label, Run YOLO).<br>• Secondary: outlined.<br>• Tertiary: text colour, underline on hover/focus (fixes the faint hover in P1a).<br>• Disabled: legible text, dashed border, not-allowed cursor.<br>All controls, choice rows, expanders, nav rows and header icons are 44 px; the 390 px radio gaps are fixed. |
| Shared states (`ui.py`) | `empty_state`, `error_state` (says what is kept, offers retry), `notice`, `field_feedback`, `loading(stage)`, `freshness`, `go_to`. They are adopted across Explore, Regional review, Admin and the tester (§7c adoption map below). `st.info`/`st.warning` are gone from `app.py` and `yolo_test_app.py`. `st.error` is used only for real failures; green only for saved confirmations ("Rerun finished" and "Candidate trained" are now neutral). |
| Field feedback | Decision category and reason messages sit under their own fields and the draft is kept. On save failure: "Your draft is still here." Contract IDs are normalised (case, spaces, `,` `;` newline, duplicates); invalid characters are rejected by name; more than 3 IDs are rejected with the extras listed, and nothing runs on a partial list. Tester source ID: invalid IDs disable Save label, and a blank ID explains the training exclusion. |
| Freshness and activity | Explore "Source checked … PHT". The regional queue shows real counts and keeps **Pause requested · finishing the current contract** separate from **Paused · N of M saved**. Tester training activity comes from the log's last-write time and says "Working" only while a run is unfinished. |
| Motion and focus | A global `prefers-reduced-motion: reduce` rule drops transitions and animations to about 0 s. One 2 px focus ring covers every focusable element, including nav links, expanders and header icons. |
| CSS | Still a single block in `ui.py` with a spacing scale of 4/8/12/16/24/32/48. **No `data-testid` selectors**: only `.st*` classes, ARIA attributes and plain elements. Colours stay in `config.toml`. |

### Adoption map (summary)

| Screen | Empty | Error (kept + retry) | Notice | Loading |
| --- | --- | --- | --- | --- |
| Explore | No results (Reset filters); no confirmed photos | Source outage (filters and page kept; Refresh results); contract outage (previous saved review retained; Retry loading contract); gallery failure (Retry loading photos) | Record notes; missing dependency | Gallery checks; photo analysis |
| Regional review | "No saved regional reviews yet." → **Go to Admin · Regional reviews** | Load failures; highlights unavailable ("Recorded facts are still shown") | Stale reviews; rerun finished | Fetching records; highlights |
| Admin | No saved reviews, matches or group | Saved-groups DB failure (retry); re-check failure (previous review still saved); analysis, summary and comparison failures | Construction cues; possible unfinished work, with the timing caveat | Rerun; photo load; saves |
| Tester | No training run | Training, analysis and save failures | Missing checkpoint; candidate trained | Training (freshness) |

### Measured rendered contrast (Chrome, forced rest/hover/focus/active states, both themes)

| Element | Light | Dark |
| --- | --- | --- |
| Wordmark ink | 14.23 | 14.76 |
| Wordmark "AI" (logotype; large-text 3:1 met) | 3.86 | 3.76 (was 3.15) |
| Active nav link | 12.4 | 11.14 |
| Inactive nav link, lowest state | 7.52 | 6.88 |
| Focus ring vs page (sidebar) | 5.0 (4.62) | 9.27 (8.53) |
| Secondary button text, lowest state | 13.24 | 9.87 |
| Secondary button border (non-text 3:1) | 3.3 | 4.2 |
| Primary button label | 17–21 | 5.24–9.48 |
| Tertiary "Refresh results", all states | 15.4 | 16.04 |
| Disabled button text | 4.62 (was 2.45) | 5.38 (was 3.43) |
| Error text on error background | theme slot | 7.66 (calculated) |

### Verification

- All suites, run by the lead after integration from `ProjectWatchAI/`, pass **110/110**: `test_audit_regressions test_evidence_language test_explore_ui test_evidence_presentation test_tester_ui test_states_ui test_navigation_ui`.
- New test files:
  - `test_navigation_ui.py`: destinations; active page; `go_to`; context preserved across pages.
  - `test_states_ui.py` (16 tests):
    - Regional empty state routes to Admin;
    - Explore no-results; outage keeps filters with a retry;
    - reason feedback placement keeps the category;
    - save failure via a SQLite trigger on a disposable DB keeps the draft, writes no row and shows no green;
    - contract-ID normalisation and the over-3 rejection;
    - one primary per area;
    - no bare spinners or claimed durations.
- Rendered screens:
  - Admin: 1440 light and dark; 390 light; 390 dark with the sidebar expanded.
  - Regional review: 1440 light and dark.
  - Explore: 1440 light and dark; 390 light.
- The lead inspected Admin dark 1440, Regional light 1440 and Explore light 390. Results: active location clear; wordmark legible; no horizontal overflow at 390; no radio gaps.
- Lead fixes at integration:
  - the Regional "Flagged contracts" metric label exposed raw markup in its help name;
  - the tester favicon now uses the ant-face.

### Framework limitations (recorded)

- `/explore` shows "Page not found": the default page is served only at `/`.
- `st.logo` renders `alt="Logo"`; its link is named "Navigate to home page". The wordmark image carries `aria-label="BantAIProyekto"`.
- Native navigation always sits at the top of the sidebar, so the wordmark, tagline, page description and footer come below the links. The sidebar page description repeats the page caption.
- Field feedback is adjacent text: Streamlit cannot attach `aria-describedby` to a widget.
- Streamlit forms cannot contain extra buttons, so after a failed save the form's **Save decision** acts as the retry.
- Reduced motion is applied by CSS; a running spinner was not captured to confirm it.
- Screen reader: not verified (B4 skipped by owner).

### Open after phase 3

- Brand red `#D93829` (vectors) vs `#C9291B` (B3): owner choice.
- ~~"Save training example and move contract" is relabelled and secondary, but the decision and training actions are not yet split (P1b).~~ Split and removed in phase 5 (§7e).
- Two primaries remain on some Admin screens, one per separate step panel (Regional reviews steps 1 and 2).
- Repeated separators in contract IDs ("A,,B") are accepted as harmless.
- A save failure inside the regional background thread still cannot show on screen (pre-existing).
- "AI detection found …" (`project_review.py`) and the "Show AI photo findings" label are unchanged.

## 7d. Phase 4 record — Explore projects (2026-11-16)

Scope: plan §5A, the Explore rows of §8 and the §7 compact-layout rule. Visual system unchanged from §7c; one CSS block added for the filter row.

### What changed (`ProjectWatchAI/app.py`, `ui.py`)

| Area | Change |
| --- | --- |
| Search panel | Contract ID/description search, Region and Fiscal year stay first; other filters stay under **More filters**. The old "Showing …" caption is now an **Active filters** row of chips (`st.html`, HTML-escaped; the search text stays literal). The fiscal-year chip reads "Fiscal year 2026 (default)". **Reset filters** has help text naming the default year. |
| Reset | `reset_project_filters` assigns explicit defaults (`""`, `None`, current fiscal year, `False`, 100) instead of popping widget keys. **Bug fixed:** with popped keys, the browser sent the old search and region back on the next rerun, so the cleared filters returned. This was found in the rendered check; AppTest cannot show it. View and detail-column preferences are kept. |
| Initial state | With default filters only: "Showing every contract for fiscal year 2026. Search by contract ID or project description to narrow the list." The placeholder keeps its example. |
| Loading | `loading("Loading matching contracts from the DPWH source")` around the list request. Filters stay usable and earlier results are dimmed by Streamlit. |
| API outage | `fetch_live_results` keeps the last successful result for the **same** filters and page. On failure it shows the error, keeps those results and says they are from the last successful check (with its time); freshness reads "Source last checked". New filters with no earlier result show the error and a **Refresh results** retry. Malformed pagination is treated as an outage. |
| Counts | The heading is the source's matching-record total. In photos-only mode it reads "N matching contracts before photo check", followed by four labelled metrics: **Matching records**, **Checked on this page**, **With photos, shown** and **Could not be checked** (shown only when non-zero). The page line reads "Page P lists X of N matching records". Photo checks run only in photos-only mode (a test asserts no gallery calls otherwise). |
| Columns | Default columns follow the plan: Contract ID / project (pinned) · Region · Reported status · Photo evidence · Last reviewed. Header help separates Reported status (source) and Last reviewed (saved review time, not the source check). **Add detail columns** (toggle) appends Reported completion, Reported budget and Category, taken only from source fields. A missing value shows "—", which the caption explains; nothing is inferred. |
| Open and return | Row selection still opens a contract. The labelled **Contract to open** picker plus **Open contract** remains the keyboard path; long names in the picker are shortened with "…". Opening records `live_last_opened`. **Back to results** restores filters, page, view, picker value and the table row (`selection_default`, applied only when the table has no stored selection), and shows a one-time note "You were viewing ID." |
| Compact alternative | **Show results as** Table or List. List shows one bordered card per contract: ID, the full project text (`st.text`, literal), labelled facts and a button **Open contract ID**. Cards render 25 at a time with "Show N more on this page"; the last-opened contract is always rendered and marked "last opened". Streamlit cannot read the viewport width, so List is a user choice, not automatic below 768 px. |
| Reruns | No timers on Explore (unchanged). New keys (`live_view`, `live_detail_columns`, `live_list_shown`) join `LIVE_STATE_KEYS`; `main()` now re-pins the whole tuple, so state survives other pages too. |

### Verification

- **AppTest** (`test_explore_ui.py`, 16 tests, 10 new; fake API and disposable DB): photos-only counts kept apart (9 matching / 3 checked / 1 shown / 1 not checked); no gallery calls outside photos-only; Back restores picker and table selection, and the note shows once; reruns keep search, choice, toggle and page; detail-column order with no invented category; List view (full long name, labelled card button, open/back keeps List and the marker); List renders in steps of 25; outage keeps labelled last results; outage without earlier results retries; chip text is literal. `FakeApi` photo data is opt-in because `test_evidence_presentation` imports it.
- All suites, from `ProjectWatchAI/`: `test_explore_ui test_states_ui test_navigation_ui test_evidence_presentation test_evidence_language test_audit_regressions test_tester_ui` → **120/120 OK**, after the Reset fix.
- **Rendered, fixture-backed** (fake BetterGov API patched over `requests.get`; 120 synthetic contracts; disposable review DB; headless Chrome via CDP; Streamlit 1.67.0; Windows 11). Not the live API. Harness and screenshots are in the session scratchpad, outside the repository.

| Check | Result |
| --- | --- |
| Widths 1440 (light and dark), 1024, 768, 390, 320 | No page-level horizontal overflow (`scrollWidth == innerWidth`); chips wrap (two lines at 320). The old toggle label truncated at 1024 and 320, so it was shortened to "Add detail columns". The new label has not been re-measured (the probe returned no size). The search placeholder is clipped at 320 (the label is intact). |
| Search, filter, Reset | Chips follow search and Region (Region set by keyboard). Reset failed in the first pass (see Reset above). Re-run after the fix: chips, count, search field and Region stay reset through Refresh, the toggle, and a blur; a new search then works normally. |
| Keyboard open/back | Picker → ArrowDown → Enter → Open contract → detail; **Back to results** with Enter restores chips, page, picker value, row selection and the one-time note. Focus lands on `<body>` after open and after back (Streamlit cannot place focus). The first Tab after a choice reaches the picker's native "Clear value" button. |
| Mouse row selection | Opens the contract. After Back, the restored selection does **not** reopen it, through Refresh, toggling and switching Table/List and an 8 s wait. To reopen the same row by mouse, the user clicks twice: the first click clears the restored tick. |
| List view | At 390 and 1440, Tab reaches each "Open contract ID" button with the 2 px focus ring. Long all-caps names wrap fully; markup-like names render literally. Open/back keeps List and "last opened"; Show more goes 25 → 100. |
| Detail columns | The grid scrolls inside its frame (1322 px of content in 978 px at 1440; in 562 px at 1024) with the first column pinned; no page overflow. |
| Photos only | 100 matching before photo check / 50 checked / 10 shown / 4 not checked, which matches the fixture exactly; the error names 4 contracts. |
| Loading | The spinner names the stage, with elapsed time, under the filters; filters stay enabled. In one capture, Region briefly showed the "Choose an option" placeholder while loading. |
| Outage | Kept results with the time of the last successful check; a new filter shows the error and retry with no table; after recovery the retry restores results. |
| No timer reruns | A typed search draft (no Enter) and its focus held for 10 s; the script stayed idle. |
| Console | 0 errors. |

### Open after phase 4

- Focus after Open contract and Back to results goes to `<body>` (framework limit); keyboard users re-enter from the top. A skip link or a focus script was not added.
- The native "Clear value" button is the first Tab stop after a picker choice (framework).
- Mouse users must click a restored row twice to reopen it.
- The List view is not selected automatically on narrow screens (no viewport signal in Streamlit).
- Region placeholder flash during loading: seen once, not reproduced.
- Screen-reader announcement of the chip list, the one-time note and the outage error: **not verified** (B4).
- The "Add detail columns" label has not been re-measured at 1024 and 320.
- Live BetterGov API not exercised in this phase; field names `category`/`infraType`/`budget` on list rows are read when present and shown as "—" otherwise.

## 7e. Phase 5 record — Review workspace (2026-11-16)

Scope: plan §5B (contract detail), §5E (corrections), §4 (decision and training forms split). Plan phase P1b. Built by the lead with two parallel helpers (evidence links; training correction), then checked by a real-browser agent and an adversarial reviewer. Nothing was committed, deployed, retrained or promoted.

### What changed

| Area | Change |
| --- | --- |
| Shared rendering | New `review_workspace.py` renders one workspace for the Admin saved-review page and the Explore contract detail. Order: identity and source → reported facts → assessment and limitations (+ linked flags, latest-date conflict) → latest dated evidence and reviewer decision (side by side above 1100 px) → timeline and budget → provenance and history. `app.py` passes app-specific work in (`Services`): database, overlay, training form, Next contract, Rerun review, satellite and documents. Explore uses the workspace only when a saved review exists; otherwise its earlier read-only view is unchanged. The live gallery moved into an expander "Live source gallery with detector observations (not saved)". |
| Compact and desktop layout | The evidence and decision sections sit in a keyed container. They are side by side above 1100 px and stack below it, evidence first (CSS in `ui.py`, class selectors only; the plan's "no `data-testid`" rule still holds). |
| Photo viewer | `review_evidence.photo_groups` groups photos by capture day in PHT and agrees with `assess_contract`'s decision day. The newest dated group is the default. Older groups read "Earlier · date · N photos"; undated read "Date unknown". Each photo keeps date, source, photo number, plain detector text, detector eligibility and its citations in one bordered region. Previous photo / Next photo, a group selector and a photo selector; selection survives reruns and leaving the contract. "Open original photo" is always present. Overlay is a radio (Original photo / Detector overlay), computed only on request; when no detector or photo is available it says so and keeps the original. |
| Flag links | `review_evidence.flag_links` ties each flag to photos (tertiary buttons that select the photo and its group), dates and amounts. Counterevidence is linked for the cue/scope pairs. The same-date conflict has no flag under the current rule (policy audit A4), so it gets its own block linking both sides. Photos say why they are cited ("Cited as visible scope evidence…", "Counterevidence: …"). |
| Decision form | `review_store.py` (no Streamlit). Fields: current category (read-only), eligible target (only categories the saved evidence allows; nothing preselected), reason (8–600 characters, required), reviewer (required; placeholders such as "local reviewer" are refused; prefilled from `PROJECTWATCH_REVIEWER` or the name used earlier in the session). Field messages sit under the field. Primary action "Save decision". |
| Persistence | One transaction per decision: an immutable row in the new `manual_decision_history` (the revision), then the current decision tables the rest of the app reads (`manual_categories`, `manual_review_metadata`, `contract_assessments`). Any failure rolls everything back. The summary also stores `manual_review_provenance`, so the overview attributes the decision at once. |
| Confirmation | "Decision saved for ID" is read back from the stored revision (category, reason summary, reviewer, time, revision number). A new save attempt retires the previous confirmation, and so does leaving the contract. After a save the contract stays on screen even though its group changed. |
| Next contract | A separate button inside the confirmation, shown only after a successful save and only when a queue exists. It is navigation only. The browse buttons are now "Previous in list" / "Next in list". |
| Drafts | Category, reason and reviewer are kept per contract ID in session state through reruns, photo switches, leaving and returning, and failed saves. The form is emptied after a successful save. |
| Stale evidence | If the saved evidence changed after the form was shown, the first save is blocked once (draft kept) and the second save records against the current evidence. The store repeats the check inside the transaction. Rerun review resets the baseline. After a decision, an evidence change shows "Check this decision against the current evidence". Explore states differences between the live record and the saved review (status, completion date). |
| Overrides on rerun | Unchanged behaviour (`assess_with_manual_review`), now covered by a test: category, reason, automatic category and reviewer provenance survive a rerun. |
| History | "Reviewer decisions" (number, revision, category, changed from, reason, reviewer, time) and "Earlier automatic reviews". Decisions made before revisions were kept say so. **No Undo is offered or promised**; the history says a correction is a new revision. Restoring an earlier revision is not implemented. |
| Training correction | New `training_correction.py`, structurally unable to touch decisions (imports neither the review store nor the dashboard; a test checks the source). One form per selected photo: detector, load photo, label, outline, source contract ID (prefilled, editable), one primary "Save training example". Outcome message states "This contract's decision was not changed." A cleared source ID saves the example as "Excluded from training until source is identified." `yolo_feedback.outline_problem` is the single outline validator for both forms; the tester uses the same helpers. |
| Removed | "Save training example and move contract", "Save YOLO training example", "Save and go to next contract", "Also move this contract…", and the old `show_manual_category` / `show_training_feedback` / `advance_saved_review`. |
| Other | `train_candidate` now leaves unknown-provenance corrections out of a run instead of aborting it (found in review). `Dockerfile` copies the four new modules (same failure as B6 would have recurred). `open_assessment_db` also creates the history table. A read-only connection is used for confirmations and history, so opening a contract in Explore still does not change the database file (a test compares bytes). |

### Deviations from the plan and from the request

- "Beside each image": date, source, photo number, observations and eligibility sit directly under the image in the same bordered region, not in a second column. A nested column was too narrow beside a 550 px image.
- A reviewer name is now required. Existing scripts that called `save_manual_category(record, category, note)` need a fourth argument or `PROJECTWATCH_REVIEWER`; three tests were updated.
- The reason is a text area (was a single-line input); three tests were updated for the new widget type.
- "Review view" radio (Review workspace / Saved groups) was kept; both modes now use the same detail rendering.

### Verification (actual results)

All suites from `ProjectWatchAI/` with the venv Python, disposable SQLite files and a temporary training-feedback folder. Baseline before the change: 120/120.

| Run | Result |
| --- | --- |
| `test_review_store` (new) | 16/16 OK. Validation per field, only eligible targets, atomic save, forced failure rolls back every table and recovers, stale evidence refused, second decision is a new revision and keeps the automatic category, reviewer name normalised, warnings, old databases without the new tables. |
| `test_review_evidence` (new) | 23/23 OK. Group order and labels, timezone day boundary, conflicts both ways, budget/date flags, unknown flags, no mutation. |
| `test_training_correction` (new) | 18/18 OK. Includes source-text guard and unknown provenance. |
| `test_review_workspace` (new, rendered through `app.py` with AppTest) | 37/37 OK. Section order; evidence before decision in source order; newest group default and labels; photo switching and Previous/Next; context survives leaving; conflict links both ways; flag link with date and amounts; overlay only on request; missing reason / category / reviewer; save writes a revision and the confirmation reads it back; Next contract absent before and after a failed save, present after a success and moves to the next contract; simulated persistence failure keeps the draft and recovers; failed second save leaves no old confirmation; drafts across reruns and contracts; second decision; stale evidence blocks once; overrides survive a rerun; evidence changed after a decision is flagged; legacy decisions; training save leaves decisions untouched and the reverse; training validation separate; unknown provenance; training failure leaves the decision draft; Explore uses the same workspace and save path; live/saved difference stated; reviewer text not interpreted as Markdown. |
| Regression, all suites | `test_review_store test_review_evidence test_training_correction test_review_workspace test_explore_ui test_states_ui test_navigation_ui test_evidence_presentation test_evidence_language test_audit_regressions test_tester_ui` → **214/214 OK** (120 existing + 94 new). |
| Static | No remaining references to the removed functions; every local module imported by `app.py` is in the Dockerfile `COPY` list. The only gap is `evaluate_checkpoint`, imported lazily by training, which is not part of the dashboard image (unchanged from before). |

**Rendered, fixture-backed** (Streamlit 1.67.0, headless Chrome through CDP, Windows 11, disposable DB, fake S3 photo URLs; not the live API, no detector checkpoints):

| Check | Result |
| --- | --- |
| Page overflow at 1440, 1024, 768, 390, 320 and 720 px at 2× | None (`scrollWidth == innerWidth`). Console: 0 exceptions, 0 `console.error`. |
| Evidence before decision | Stacked at ≤1100 px with evidence first (first pass at 1024: decision column 190 px, fixed by the stacking rule; re-run: 554 px each). Side by side at 1440: 550 / 356 px. |
| Photo buttons | First pass: "Pre…" / "Ne…" at 200% zoom. After stacking the pair under the selector: full labels, 267 / 554 / 348 / 678 px per button at 1440 / 1024 / 390 / 200%. Labels seen in screenshots at 1440 and 390; measured only at 1024 and 200%. |
| Keyboard | Tab order: group, photo, Next photo, image view, Open original photo, training expander, target category, reason, reviewer, Save decision. 2 px focus ring on every stop. Save decision, Next photo, Previous photo measure 44 px. |
| Missing reason | Message under Reason; database unchanged; no confirmation; no Next contract. |
| Save | Confirmation shows reviewer and revision 1; Next contract is a separate button; the contract stays on screen; Next contract moves to the next contract in the original group; the history row, `manual_categories` and the contract's category are written. |
| Defects found and fixed in the browser | (1) After a save the overview said "Reviewer and time were not recorded for this older decision" (provenance not written; AppTest could not see it). (2) The old Target category stayed selected in the browser after popping the widget key (AppTest passes; needed an explicit reset). (3) Decision column 190 px at 1024. (4) Truncated photo buttons. (5) "Photo 1 of 4" for a 2-photo group. (6) The conflict sentence appeared twice. (7) Empty "Reported amounts" column. All re-checked. |
| Browser reload (F5) | The saved decision persists. The page returns to the first group, the confirmation is gone and unsaved drafts are not kept. Drafts are not durable across a browser reload; this matches the plan, which calls durable drafts a planned capability. |

**Adversarial review** (read-only, probes with disposable storage): save atomicity held under injected failure at each step, a lock held by a second writer and a read-only database; read-only URI works with spaces and `#` in paths; the newest group agreed with `assess_contract` across timezone boundaries. It found, and I fixed, a stale confirmation beside a failed second save, unescaped reviewer/source/status text, and unknown-provenance labels aborting a training run.

### Open after phase 5

- Photos did not load in the browser check (fake URLs), so how a real photo and a real overlay look beside the new layout is **not verified**. No checkpoints exist in this checkout (B2), so overlay drawing is verified only with a stub renderer.
- The unknown-provenance fix in `train_candidate` is not run end to end (needs `data/`); only the code path was read and the rest of the form is tested.
- Screen-reader announcement of confirmations, field messages and the new buttons is not verified (B4). Streamlit still cannot link a message to its field programmatically.
- Dark theme: the Save decision fill looks pale beside the page; contrast not re-measured in this phase.
- Visual notes from the browser check, not fixed: the decision column is shorter than the evidence column at 1440; the three assessment cards differ in height; the page is long at 390 px (about 6,400 px).
- "Next contract" falls back to the first contract in the group if the queued one moved to another group in another session. Hand-corrupted saved JSON can still crash a few helpers (`eligible_review_categories`, `photo_citations`, `flag_links`).
- Restoring an earlier revision is not implemented, so no Undo exists. Decision-time reviewer identity is a typed name, not an authenticated user.
- Drafts do not survive closing the browser or a hard reload.
- Explore can now save a decision. If the owner prefers Explore to stay read-only, the form can be switched off there without touching Admin.

---

## 7f. Phase 6 record — Regional review (2026-11-16)

Scope: plan §5C and the P1 "Regional clarity" row. Nothing was committed, deployed or retrained. The scoring rules in `contractor_performance` were not changed.

### What changed

| Area | Change |
| --- | --- |
| Module | New `regional_view.py` renders the saved Regional review body. Pure helpers (coverage, focus matching, CSV, budget summary) have no Streamlit state; `render_*` functions take a `Services` bundle so the module never imports `app.py`. `app.py` keeps data loading, scoring and the highlights call. The Dockerfile copies the new module (no Docker build was run). |
| Coverage first | After the Region/Year selectors the page opens with "What these figures cover": region and year, saved reviews included, the date range they were saved (PHT), out-of-date count, evidence-unavailable count, the queue (reviewed vs still queued, when a queue is recorded), and a sentence that this is not a census. Fewer than 10 reviews adds a "Small sample" notice that says the threshold is a display convention. No-photo, retrieval-failed, failed-analysis and inconclusive counts are listed as separate situations. |
| Definitions | Three cards: Reviewed contracts (distinct contracts), Contractors (distinct reported names; contracts with no contractor counted separately, not as a contractor), Flagged contracts (not a finding of wrongdoing). |
| Drilldown | One function (`focus_matches`) defines every filter. Buttons on the flagged card, every category row, every reported-status line, "No reported project budget" and "No reported completion date" set it; a select box lists the same filters with counts. "Supporting contracts" shows a visible line "Filter · … · N of M included saved reviews", a Clear filter button, a table (contract, project, contractor, status, photo assessment, flags, reviewer decision, review state, saved time), a row-select details view with Open in Explore projects, and a CSV of the included records (formula-safe). A toast, a "Filter active · … Go to the matching contracts" line and the "· showing" button text say where the result went. The filter resets when the region or year changes. |
| Not colour alone | Category rows read "kind · category · N contracts" with the kind icon; progress bars are secondary; the existing table view is kept. The active filter says "showing" in words. |
| Comparison | After coverage, categories, facts and supporting contracts. Shows "N contractors compared · M included contracts · X scored · Y with No score", the formula (ranking score = total ÷ √scored), the limits and the photo-timing sentence beside the table. New columns: No score, Sample (Small sample = fewer than 3 scored; "No score" when none), Budget gap not checkable (missing approved budget or award amount; never read as zero). Contracts with no contractor sit in one holding row that is explained and not counted as a contractor. |
| Highlights | "Generate evidence highlights" is the reader action. The report now records `selected` (contract + fact text), `generated_for` and, from the app, `generated_at`. Each highlight links to its contract; older saved highlights without links say so; a "may be out of date" notice appears when reviews changed after generation. If the model fails (or returns no batch), an error says what failed and what is unaffected; counts, facts, table and scores stay on screen, nothing is saved, and the same button retries. Model name, batch errors, queue entries, filters and raw totals are in "Operator details". |
| Empty route | "No saved regional reviews yet." with Go to Admin · Regional reviews; it also says how many contracts are queued but unreviewed. A region/year with no saved reviews has its own route. |
| Other | Reported project budget total says how many reviews have an amount and keeps "no amount" apart from zero. The Admin regional workflow is unchanged apart from the improved comparison block. |

### Plan §5C traceability

| §5C requirement | Where | Evidence |
| --- | --- | --- |
| Lead with region/year, included count, date range, stale/unavailable | `render_scope` | `CoveragePageTests`, `PureTests.test_known_fixture_counts`; browser: block first under filters, values 8 / 16 Nov 2026 / 2 / 1 |
| Not a census unless coverage is established | `SCOPE_STATEMENT`, queue caption | page test; browser |
| Reviewed, contractors, flagged defined separately | `render_definitions` | `test_separate_definitions…`; browser: 8 / 4 / 6 |
| Measures reveal matching records, with the filter visible | `focus_matches`, `render_supporting` | `PureTests.test_every_count_equals_its_drilldown`, `DrilldownPageTests` (flagged, every category, status, no budget, stale, unavailable); browser: flagged 6, category 3, status 1, budget 1 rows |
| Counts and text/table alternative; not colour alone | category rows, table view, CSV | `test_category_rows_name_their_kind…`; browser (dark 390) |
| Aggregates trace to included records | CSV, "N of M" line, `focus_choices` counts | `test_csv_has_one_row_per_record…` |
| Comparison after coverage; Scored/Included next to scores; formula, limits, timing | `show_contractor_performance` | `test_section_order…`, `ComparisonPageTests`; browser heading order |
| No score never zero; unknown budget not zero; scoring unchanged | comparison table, `budget_gap_checkable` | `test_no_score_is_text…`, `test_unknown_budget…`, `test_scoring_calculation_is_unchanged` |
| Highlights optional, named "Generate evidence highlights", facts linked, failure keeps facts, diagnostics in operator details | `render_highlights`, `generate_regional_highlights` | `HighlightsPageTests`; browser failure run |
| Empty state routes to Admin | `show_saved_regional_dashboard` | `EmptyRoutePageTests`; browser |

### Verification (actual results)

| Run | Result |
| --- | --- |
| `test_regional_view` (new) | 43/43 OK. Pure helpers, page coverage, drilldowns, comparison, highlights success and failure, empty routes, small samples. |
| All 12 suites (`test_review_store test_review_evidence test_training_correction test_review_workspace test_explore_ui test_states_ui test_navigation_ui test_evidence_presentation test_evidence_language test_audit_regressions test_tester_ui test_regional_view`) | **257/257 OK** (214 before + 43 new). |
| Defects caught by tests | A fifth "contractor" (the no-contractor holding row) in the comparison while the card said four; a generated-at timestamp that broke an existing repeat-run equality test (moved to the app). |

**Rendered** (Streamlit 1.67.0, headless Chrome 157 over DevTools, disposable DB of 8 + 1 reviews, no network, no local model): lead block and the three cards read clearly at 1440 and 390; flagged 6 / category 3 / status 1 / budget 1 rows matched their counts and Clear filter restored 8; row select showed details and Open in Explore landed on the contract; comparison states, formula, limits and Gamma Corp "No score" text (Scored 0, never 0); highlights failure showed one error box, kept every count, retried, and Operator details held the diagnostics; empty route reached Admin › Regional reviews; no horizontal overflow at 1440/1024/768/390/320; every app text button ≥ 44 px; 2 px focus ring on the 12 stops tried; dark 390 text 16:1.

Found in the browser and fixed: the result list was off screen after clicking the flagged card or a category row (toast, "Filter active" line and anchor link added; the status and budget lines were already visible); "Generated …" shown after a failed run; "1 contracts reviewed"; "and appear" grammar; centred category rows (now left-aligned). The fixes are covered by tests; the browser was **not rerun** after them.

### Open after phase 6

- The browser was not rerun after the final fixes; the toast, anchor link and left alignment are verified by tests (toast, text) and CSS inspection only.
- Real highlight output is unverified: no local model ran, so `selected` linking was tested with a faked report.
- Console shows four 404s (`/regional-review/_stcore/health`, `…/host-config`) when the page is opened at `/regional-review`, not at `/`; cause not investigated.
- Icon-only built-ins (help icons 16 px, table toolbar 24 px, select arrows 32×38) are under 44 px; the app's own text buttons pass.
- The last two comparison columns need sideways scrolling inside the table at 1440.
- Light theme, 1024/768/320 screenshots and the stale/manual/no-photo/no-completion drilldowns were not exercised in the browser (they are covered by tests).
- Screen-reader output is unverified (B4). The table body is a canvas, so its cells are not readable from the DOM; the CSV is the text alternative.
- "Small sample" thresholds (10 reviews, 3 scored contracts) are display conventions, not statistics.
- The Admin regional overview still uses the earlier layout (with raw totals JSON); only its comparison block changed.

---

## 7g. Phase 7 record — Admin queues, model readiness and tester states (2026-11-17)

Scope: plan §5D (Admin queues), the model-status part of §5D and §5E (tester states, Candidate ready / Evaluation required / Approved active), and the contract-ID input rules. Plan phase P2a. Built by the lead plus one parallel helper (model readiness and tester states), then reviewed by an adversarial reviewer and a real-browser agent. Nothing was committed, trained, evaluated or promoted; no source API call was made by any test.

### What changed

| Area | Change |
| --- | --- |
| Worker | New `queue_worker.py` (no Streamlit). One queue = one `regional_queue.filters` key. A run walks pending contracts in four real stages (Fetching regional records, Retrieving photos, Analysing photos, Saving review) with app work injected through `Deps`. Completed rows are never reset by a pause, interruption or failure. Tables added: `queue_failures` (contract, message, time, attempts, retry flag) and `queue_runs` (state, stage, current contract, owner, heartbeat). A `queue_failures` table made before retries is migrated in place. |
| Run truth | State comes from the live worker and the stored run, never from a button click: Running, Pause requested (worker alive, finishing its contract), Paused (worker stopped on request), Interrupted (stored "running" but no worker: restart or crash), Running in another dashboard process (fresh heartbeat, other owner), Stopped, Finished, Idle. A worker heartbeat keeps the "other process" window honest. A late Pause cannot overwrite a finished run. |
| Failures and outages | One failed contract is recorded and skipped; the rest continue; its earlier saved review is kept. An unreadable gallery is a failure, not a stop. After 5 contracts in a row fail or return "analysis unavailable", the run stops (state Stopped, remaining work pending). A detector outage never replaces a good saved review with "Photo analysis unavailable · retry needed". Fetch failure stops cleanly and changes nothing. The previous all-or-nothing stop on the first error is gone. |
| Panel | New `queue_view.py` renders the Admin panel for regional reviews and category reruns. Before any button: "Scope for this check" (region, year, category, status, search), the saved-queue workload (saved / failed / need a retry / pending / due now) or "no saved queue yet; the count is not known until the fetch finishes", and the explanation of what a check does, including "There is no unattended scheduler". One primary button (Start / Resume / Check new and due contracts), Pause only while running, Retry N contracts only when something needs it, Open results only when results exist. Stage line names the current stage and contract; the progress bar appears only when the queue total is known. Highlights are stated to be a separate step. |
| Outcome | Counts are mutually exclusive: Saved, Failed, Need a retry (saved with analysis unavailable), Pending. "Finished · all N contracts saved" appears only when nothing failed, needs a retry or is pending; otherwise "Finished with problems." Preserved line: earlier reviews kept for failed contracts and manual decisions kept. A list of contracts needing attention gives the reason. Stopped uses the single error box; everything else is neutral. |
| Retry | Retry reruns only this queue's failed and need-a-retry contracts (`only` scope); completed, current reviews and other pending work are untouched; other queues are untouched. Attempts accumulate. Retry is not offered while another process owns the run. |
| Starting safely | `qw.start` takes a lock, refuses if a worker is alive here or fresh elsewhere, and only then runs the rerun seeding, so a second click cannot rewrite a queue a worker is walking. |
| Open results | Opens Regional review with the region and year preselected, but only when that region/year is listed there. |
| Compatibility | `app.run_regional_queue(filters, key, total, done, job)` remains as a synchronous wrapper over the worker; `review_due` accepts a precomputed model signature. The Admin "Queued checks" metric no longer counts failed contracts as queued. |
| Model readiness | New `model_readiness.py` (no Streamlit). Admin › Model status: "N of M detectors available · K validated"; per detector Availability, Eligibility (Not installed, Experimental, Provisional, Validated stay separate), how observations are used, and a practical Next action. A neutral notice (not an error) when no checkpoint is installed. Expander: full SHA256, path, size, approval file, trained-candidate state and evaluation-report presence; release commands only for scripts that exist. |
| Tester states | `training_status` now returns distinct states with a next action each: Training in progress, Training error, **Evaluation required** (trained, not evaluated), **Candidate ready** (hash-bound evaluation passed, not promoted), Evaluation did not pass, **Approved active** (digest equals the active checkpoint and its quality is validated). A report with only `accepted: true` is not enough for Candidate ready (it must match the checkpoint and pass `release_failures`, as `promote_checkpoint.py` requires). Reading status never creates an active checkpoint. The post-training notice reads "Evaluation required · candidate saved, not active." |
| Contract IDs | `parse_contract_input` (and the older `parse_contract_ids`): accepts any casing, spaces around an ID, comma / semicolon / new-line separators and repeats; a duplicate is kept once and **said so** ("A1 was entered 2 times and is reviewed once."), counted once against the limit of three; rejects a space inside an entry (could be one ID or two), non-ASCII lookalikes ("straße" never becomes STRASSE), anything over 40 characters and more than three distinct IDs, naming the entries and never truncating or partly accepting. The Admin field is now a text area so new lines can be typed. |
| Other | The Dockerfile copies `model_readiness.py`, `queue_view.py`, `queue_worker.py` (no Docker build was run). |

### Verification (actual results)

All from `ProjectWatchAI/` with the venv Python, disposable SQLite files, fake dependencies and real worker threads. No network, no detector, no training.

| Run | Result |
| --- | --- |
| `test_queue` (new) | 58 tests OK. Worker: full run, stage order, fetch/enqueue once, unknown gallery, partial failure keeps the earlier review, retry-required counted apart, manual decisions untouched and counted, scoped retry runs only the failed contract, retry never touches another queue, outage breaker, success resets the streak, fetch failure, pause requested → paused → resume without redoing work (each contract saved exactly once), pause on the last contract is Finished, second start refused, interruption vs paused vs running elsewhere, resume after interruption, due counts, attempts across retries, crash between retry and run, heartbeat. Panel: scope and explanation before any action, no inert buttons (Start → failure → Retry runs only the failed one), partial failure is not success, retry-required is not clean, paused/interrupted/stopped follow the worker, running-elsewhere disables Start and hides Pause/Retry, Open results only with results, due count, fetching stage shows no fabricated total. IDs: boundaries (3 ok, 4 rejected naming the extra, empty/separator-only), casing/whitespace/separators, duplicates explicit, ambiguous rejected, invalid and over-length rejected, upper-casing hazards, huge input truncated in the message, nothing partially accepted. Admin page wiring. |
| `test_model_readiness` (new) | 23 tests OK (pure module and Admin › Model status rendering with fake detector tables). |
| `test_tester_ui` | 14 tests OK, updated for the new state names. |
| All 14 suites | `test_review_store test_review_evidence test_training_correction test_review_workspace test_explore_ui test_states_ui test_navigation_ui test_evidence_presentation test_evidence_language test_audit_regressions test_tester_ui test_regional_view test_model_readiness test_queue` → **341/341 OK** after the browser fixes (257 before this phase). |
| Adversarial review | Probed with disposable databases. Found and fixed: a detector outage overwrote good reviews with "analysis unavailable" and never tripped the breaker; a second click could reseed a rerun queue under a live worker (a contract marked done without being reviewed); one malformed staged value blocked a queue for good; a late Pause could make a finished run read "interrupted"; Retry offered while another process owned the run; attempts never accumulated; a heartbeat only moved on stage changes; unused prefetches kept running after a stop; upper-casing could rewrite "ß"/"ﬁ" into valid IDs and a huge invalid entry filled the message; non-object saved JSON crashed the due count. Found sound: counts put every row in exactly one bucket; a failed contract never overwrites its earlier review; SQLite locking held under 300 saves with concurrent readers and a manual writer (0 "database is locked"). |

**Rendered, fixture-backed** (Streamlit 1.67.0, headless Chrome 157 over DevTools, disposable DB copies, network blocked with a dead proxy so the source API could not be reached, no detector checkpoints; 100 tool steps by an independent agent after one usage-limit interruption):

| Item | Result |
| --- | --- |
| Fresh scope | PASS. "Scope for this check" (region, year, category, status), "count not known until fetching finishes", the no-unattended-scheduler explanation, one primary "Start regional review" (44 px), no Pause/Retry/Open results. |
| Start with the source unreachable | PASS. Running with a Fetching stage and no fabricated total, then one red box saying fetching failed and nothing was changed, Start usable again, no traceback. **Defect fixed:** the box showed the raw HTTPS/proxy exception text; messages are now summarised ("The source could not be reached or did not respond"). |
| Paused | PASS. "Regional review paused", counts and Preserved line (manual decision kept), Resume (primary), Retry, Open results. Resume ran: stage line "Retrieving photos · now", progress "8 of 10 … now on FX-QUEUED-1", the 7 saved reviews unchanged, the 2 unreachable contracts failed and "Finished with problems" (not success). The run finished in under 1.3 s, so stage change speed was not measurable. |
| Interrupted / running elsewhere | PASS. Interrupted says it stopped without being paused and never says "paused"; with a live heartbeat, running-elsewhere disables Start, hides Pause and Retry. (The fixture's own timestamp was stale, so it first rendered as interrupted, which is the correct behaviour for a stale heartbeat.) |
| Stopped | PASS. One red box with the stop message, saved and pending kept, failures listed with reasons and attempts. Retry re-ran only the failed and need-a-retry contracts (attempts 1 → 2); the pending contract and the other saved reviews were untouched. |
| Problems / clean | PASS. "Finished with problems." with counts and Retry; "Finished · all 7 contracts saved." with no Retry. |
| Model status (no checkpoints) | PASS. "0 of 17 detectors available · 0 validated", a neutral notice (not an error), the Next action column, repo-relative paths, release commands. Full hashes could not be seen (nothing installed). |
| Contract IDs | PASS. "24CK 0181" rejected as unclear; "a1, A1, b2, c3, d4" gives the duplicate note and "Remove 1 to continue: D4", the same on separate lines. |
| Layout, five widths | No horizontal overflow at 1440, 1024, 768, 390, 320; every text button 44 px; 8 Tab stops each with a 2 px focus ring; 6 console messages per load, all the known `/_stcore/health` and `/host-config` 404s. Dark 390: error text 7.66:1, body about 16:1, primary button 5.24:1; the state is carried by icon and words as well as colour; light 390 also fine. |

Defects found in the browser and fixed (then covered by tests; the browser was **not rerun** after the fixes): "Open results" did nothing until some other rerun happened (a click inside the 3-second fragment recorded the page switch but caused no full rerun; it now reruns the app); raw exception text in the error box and failure list; the primary and Retry buttons truncated at 1024 px (primary now has its own row, the other actions share the next); progress text said "8 of 10 saved" beside a Saved metric of 7 (now "8 of 10 contracts reviewed (1 needs a retry)"); "1 are still pending"; the running-elsewhere notice promised progress without showing any.
Not fixed (minor): the Admin top metrics ("Saved contract reviews", "Queued checks") sit outside the live panel and update only on a full rerun; for one sample, the panel showed "Finished with problems" beside a still-disabled primary button; the metric help icons are 16 px built-ins; a single transient "queue is unavailable" panel appeared once on one DB and did not reproduce.
Not verified: stage-change speed during a long run, real API timings, full hashes, a Resume that reaches the repeated-failure stop in the browser (only 2 contracts were pending against the limit of 5; the worker test covers it), light theme beyond 390.

### Decisions and deviations

- Candidate-state mapping: Evaluation required = trained, not evaluated; Candidate ready = evaluation passed, awaiting the separate promotion step; Approved active = promoted. The plan's three names are treated as successive states, not synonyms.
- Experimental and provisional detections are *not* described as unable to support a completion category: the evidence policy (audit note A1) lets them set a photo assessment, so the readiness text says that instead.
- Spaces no longer separate IDs ("24CK 0181" is rejected as unclear); previously it was silently read as two IDs.
- Failed contracts no longer abort a run; the earlier all-or-nothing behaviour is replaced by the breaker above.

### Open after phase 7

- Resume after pausing a **scoped Retry** continues every pending contract in the queue, not only the retried ones.
- A manual decision saved in the few milliseconds between a contract's analysis and its save can be overwritten by that save (tiny window; a guard on newer manual metadata would close it).
- A due recheck puts its contracts back to pending, so their saved results leave the Regional review reader page and **Open results hides** until they are saved again (the reader requires a completed queue row). This predates the phase.
- The failure streak is counted per run: each Resume after an outage can mark up to 5 more contracts failed (then needs Retry).
- No detector checkpoints exist in this checkout (B2): model states, eligibility and candidate states were tested with fakes and fixture files only; the release scripts were never run.
- The Docker image was not built. Screen-reader output is unverified (B4).
- Highlights are a separate stage in step 2 of Admin, not in the queue panel's stage line; they were not re-verified with a real local model.
- Tester upload limit text (S11) and the tester's operator link remain P2b.

---

## 8. Phase sequence, commands, blockers and research

### Sequence

1. **P1a Shared system and navigation (next).**
   - Derive brand assets into `static/brand/` and apply the §4 tokens in `config.toml`.
   - Shrink `apply_theme`; remove the pulse; set the sidebar to `auto`.
   - Rework Explore: search first, metrics after, More filters, plan column order, labelled Open contract next to the table, Back to results.
   - Fix the tester upload limit text.
   - Verify: rendered contrast, widths 320–1440, 200% zoom, keyboard incl. expanders.
2. **P1b Review workspace (done, §7e).** Contract detail sections, newest-group-first photo navigation, overlay legend, text finding per photo, separate decision and training forms, save-failure simulation test, Next contract after successful save.
3. **P1c Regional clarity (done, §7f).** Coverage/freshness header, measure definitions with drill-down, kind-coloured chart, No-score text and formatted numbers, "Reported status" header, highlights linked to contracts, empty state with an Admin route.
4. **P2a Queue and operator (done, §7g).** Stages, Pause requested/Paused, partial-failure summary, Retry failed, model-status next actions, tester candidate states and eligibility wording.
5. **P2b Evaluation and polish.** Research R1–R4, screen reader pass, latency measurements, issue log with owners.

### Commands (from `ProjectWatchAI/`, Windows)

```powershell
.\.venv\Scripts\Activate.ps1
python -B -m unittest test_audit_regressions -v          # evidence regressions (plan §10)
python -B -m unittest test_evidence_language -v          # P0 mapping + AppTest UI checks
python -m streamlit run app.py                           # dashboard
python -m streamlit run yolo_test_app.py --server.port 8502   # tester
```

For fixture-based UI checks without touching real data, set `PROJECTWATCH_PHOTO_REVIEW_DB` to a temporary file seeded with `test_evidence_language.seed_review_db(path)`.

### Blockers and dependencies

Owner decisions received 2026-11-16 are recorded in the Status column.

| ID | Blocker | Status | Consequence |
| --- | --- | --- | --- |
| B1 | Logo source files | **Resolved by owner decision:** `Elements/` is the complete asset source; no further exports will be supplied. | Mark, favicon and collapsed-sidebar icon are derived from `Logo.png` (trimmed copies under `static/brand/`; originals untouched). `Elements/` has no standalone wordmark (it exists only inside labelled, black-on-cream boards), so the expanded sidebar keeps the **live-text wordmark** (§3.6). A board crop can replace it if the owner prefers. |
| B2 | No checkpoints or `data/` in this checkout | **Open.** No reply yet. | Cannot render validated/provisional findings, overlays or real inference states; Model status shows only "Not installed". Mainly affects P1b verification. Needs a sanitized copy of `data/` (checkpoints + a review DB with mixed states), used only through `PROJECTWATCH_PHOTO_REVIEW_DB`. |
| B3 | Brand red value | **Resolved:** owner confirmed **`#C9291B`**. | Brand red is now an approved token, no longer "working". The dark-theme "AI" text tint `#F0675A` remains a derived accessibility variant (§4.3). |
| B4 | Screen-reader verification | **Owner decision: skipped.** | No screen-reader testing is planned. Announcement of Streamlit alerts and reading order stay **unverified**; the plan §7 screen-reader check and the related release criterion cannot be marked met. Automated label/focus checks continue but are not a substitute. |
| B5 | Self-hosted typeface | **Resolved:** owner approved. Fonts vendored 2026-11-16. | `ProjectWatchAI/static/fonts/`: Atkinson Hyperlegible Next variable upright + italic (weight 200–800), `OFL.txt`, and a README with the source commit (`google/fonts` `95f4904f`) and SHA-256 hashes (verified to match the download). Not yet wired into the theme; that happens in P1a. |
| B6 | Docker image omitted `evidence_language.py` (P0 regression, found while checking B5) | **Fixed 2026-11-16.** | `Dockerfile` copies an explicit module list; P0's new module was missing, so the image would fail at import. Added it, plus `COPY static ./static`. A Docker build has not been run in this environment. |

### Human research tasks

| ID | Task | Method |
| --- | --- | --- |
| R1 | Formative study, about 5 participants (reviewers/analysts) on plan §10 tasks 1–7 | Record success, time, misinterpretations, assistance, accidental actions and ease rating. Use sanitized fixtures. |
| R2 | Operator walkthrough of training/model readiness and queue pause/resume | Separate session (plan §10). |
| R3 | Interpretation check: do participants read "Visible scope evidence" as completion? Do they distinguish reported status, observation and decision? | Part of R1 (target ≥90%). |
| R4 | Brand red versus error red discrimination, and green-free scoring comprehension | Part of R1; observe before asking about preference. |
| R5 | Task frequency log for the Pareto prioritisation (Law 21) | During R1 and early use. |
