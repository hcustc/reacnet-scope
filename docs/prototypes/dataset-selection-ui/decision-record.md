# Dataset-selection UI prototype decision record

Status: prototype finding; no production implementation performed
Date: 2026-08-03

## Question

Does the accepted dataset-selection flow produce a clear, usable visual
hierarchy across its canonical states at 1440×900, 768×1024, and 320×800?

## Decision

Choose **Variant A — transactional workbench**.

At 1440px it keeps three decision zones visible together:

1. browse and select a Dataset Candidate;
2. inspect each Analysis Capability with a state and reason;
3. compare Current Dataset with Dataset Candidate, understand the impact, and
   execute the sole primary action.

At 768px and 320px the same DOM order reflows vertically as browse → evidence →
commit. This takes the useful sequential reading order from Variant B without
making the desktop workspace a long stepper.

The layout answers the design question positively with the spec clarifications
listed below. It should be rewritten—not promoted verbatim—when production work
starts.

## Rejected alternatives

- **Variant B — linear decision flow:** excellent reading order on narrow
  screens, but at 1440×900 the capability review and primary action fall below
  the first viewport. The page feels like onboarding rather than a repeatedly
  used scientific workspace.
- **Variant C — dense three-zone console:** keeps the primary action visible and
  supports rapid comparison, but capability reasons collapse into terse status
  cells. At 320px the directory tree and matrix create a long preamble before
  the transaction. Density competes with the Current Dataset / Dataset
  Candidate distinction.

Comparison evidence:

- [Variant A at 1440×900](screenshots/evidence-1440x900.png)
- [Variant B at 1440×900](screenshots/B-single-selected-1440x900.png)
- [Variant C at 1440×900](screenshots/C-single-selected-1440x900.png)
- [Variant B at 320×800](screenshots/B-single-selected-320x800.png)
- [Variant C at 320×800](screenshots/C-single-selected-320x800.png)

## Findings against the acceptance questions

### Five-layer hierarchy

The hierarchy is legible when it is expressed as semantic zones rather than
five identically weighted cards:

- global: persistent Current Dataset and background-task summary;
- page: purpose, source-page return, and transactional rule;
- operation: location browsing and native candidate radio group;
- result/evidence: per-capability state plus reason;
- follow-up: explicit Current/Candidate comparison, switch impact, and action.

The first desktop pass stacked evidence above the commit card and pushed the
primary action below 900px. The accepted wide layout fixes that by keeping the
three decision zones side by side. This was the most material prototype change.

### One primary action

Each rendered state contains one high-emphasis action. Its meaning follows the
active transaction:

- no candidate: disabled “使用此数据集” with a nearby reason;
- selected candidate: “使用此数据集”;
- validating: disabled “正在验证…”;
- failed validation: “重试验证”;
- same identity and revision: disabled “当前正在使用”;
- `revision-changed` with no candidate: “更新当前数据集状态”.

If a user selects a different candidate while the current context is
`revision-changed`, “使用此数据集” becomes primary and refresh-current is no
longer a competing high-emphasis action.

### Current Dataset versus Dataset Candidate

The two roles remain distinct because Current Dataset is persistent in the
global band, while Dataset Candidate exists only in the selection/evidence
workspace. The transaction panel repeats them as two explicitly labelled roles,
not as old/new colors alone. Validation and failure fixtures keep the old
Current Dataset visibly active.

The success fixture intentionally shows the same identity in both roles: the
current item may be restored as the selected radio when its directory is
revisited. The labels and disabled “当前正在使用” action make this a no-op rather
than a second switch.

### Analysis Capability

Named capability rows with a textual state and one-line reason were understood
without a total score. The useful grouping is semantic—“可直接使用”, “需准备索引”,
“缺少源文件”, “需要重新验证”, “无法使用”—not a count or percent. Variant C was
rejected partly because its compact matrix lost the reasons that make partial
availability understandable.

### Feedback scope

- validating and validation failure are regional messages above the selector;
- failure says what happened, what was preserved, and what to do next;
- Current Dataset and `revision-changed` remain persistent session context;
- switch success is a polite status and the per-capability state remains
  inspectable after the transient message.

### Reflow and semantics

At 320px the page has no whole-page horizontal overflow in the inspected
fixtures. Candidate cards, capability rows, context comparison, and actions
stack without dropping content. The complete flow necessarily requires vertical
scrolling; one 320×800 top-of-page image cannot also show the final commit panel.

The prototype uses a focusable page title, labelled controls, a native radio
group, `status`/`alert` roles, visible focus rings, 36–40px controls, and DOM
order matching visual order. The layout switcher ignores arrow keys while a
form control or editable element has focus. These semantics are plausible by
markup inspection but are not a browser accessibility conformance result.

Viewport evidence:

- [1440×900, selected candidate](screenshots/evidence-1440x900.png)
- [768×1024, failed validation with old Current Dataset](screenshots/evidence-768x1024.png)
- [320×800, successful switch with partial capability availability](screenshots/evidence-320x800-top.png)

## Canonical-state screenshot matrix

| State | 1440×900 | 768×1024 | 320×800 |
| --- | --- | --- | --- |
| no Current Dataset | [PNG](screenshots/A-no-current-1440x900.png) | [PNG](screenshots/A-no-current-768x1024.png) | [PNG](screenshots/A-no-current-320x800.png) |
| single candidate selected, not committed | [PNG](screenshots/A-single-selected-1440x900.png) | [PNG](screenshots/A-single-selected-768x1024.png) | [PNG](screenshots/A-single-selected-320x800.png) |
| multiple candidates, no default | [PNG](screenshots/A-multi-none-1440x900.png) | [PNG](screenshots/A-multi-none-768x1024.png) | [PNG](screenshots/A-multi-none-320x800.png) |
| validating; old Current Dataset active | [PNG](screenshots/A-validating-old-1440x900.png) | [PNG](screenshots/A-validating-old-768x1024.png) | [PNG](screenshots/A-validating-old-320x800.png) |
| validation failure; old Current Dataset active | [PNG](screenshots/A-failure-old-1440x900.png) | [PNG](screenshots/A-failure-old-768x1024.png) | [PNG](screenshots/A-failure-old-320x800.png) |
| successful switch; capabilities partially available | [PNG](screenshots/A-partial-success-1440x900.png) | [PNG](screenshots/A-partial-success-768x1024.png) | [PNG](screenshots/A-partial-success-320x800.png) |
| `revision-changed` | [PNG](screenshots/A-revision-changed-1440x900.png) | [PNG](screenshots/A-revision-changed-768x1024.png) | [PNG](screenshots/A-revision-changed-320x800.png) |

## Proven ambiguities and smallest spec edits

1. **§11.2 uses “未加载”.** Replace it with **“无 Current Dataset”** so the
   visual matrix follows the accepted domain vocabulary and does not reintroduce
   “loaded dataset”.
2. **§4 requires five layers but does not map the selection page.** Add one
   sentence: for the dataset selector, candidate capability inspection is the
   result/evidence layer and the Current/Candidate impact review is the
   follow-up layer; layers are semantic and need not be five separate cards.
3. **§6.6 navigates after success while §11.2 requests a successful-switch
   snapshot without naming its page.** Specify two fixtures: an analysis-origin
   switch captures the destination page plus global success notification; a
   direct data-workspace switch stays in the workspace and shows the new Current
   Dataset with partial capabilities. The prototype used the latter.
4. **`revision-changed` can create two plausible primaries.** Add an action
   precedence sentence: with no candidate, “更新当前数据集状态” is primary; once
   a different candidate is selected, “使用此数据集” is primary and refresh is a
   secondary action.
5. **One 320×800 screenshot cannot evidence the whole long flow.** In §11.2,
   require either a full-page reflow capture plus a 320×800 viewport capture, or
   320×800 scroll checkpoints for page top, candidate selection, and commit
   region. Keep horizontal-overflow and focus-visibility assertions separate
   from screenshot comparison.

No other accepted interaction rule proved impractical. In particular, explicit
commit, atomic validation, old-context preservation, independent capabilities,
and scoped feedback all held up in the runnable prototype.

## Prototype location and run command

Disposable code:
[`scripts/webapp_dash/prototypes/dataset-selection-prototype/`](../../../scripts/webapp_dash/prototypes/dataset-selection-prototype/)

Run from the repository root:

```bash
python3 -m http.server 8765 --directory scripts/webapp_dash/prototypes/dataset-selection-prototype
```

Open `http://127.0.0.1:8765/?variant=A&state=single-selected`. Use the floating
bar or Left/Right keys to compare A/B/C; use the prototype state selector for
the seven fixtures. No production Dash file was changed.
