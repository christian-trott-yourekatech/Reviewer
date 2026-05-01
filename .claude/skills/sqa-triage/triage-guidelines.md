# Triage Guidelines

These guidelines define how to categorize sqa-agent findings into auto, interactive, or ignore. They are meant to be tuned over time as we learn what produces the best results.

## Core Principles

### Difficulty

**Difficulty is not a reason to ignore a finding.** If the finding identifies a real issue but the fix is complex, involves multiple approaches, or requires significant refactoring, mark it as **interactive** — not ignore. The interactive category exists precisely for findings that are worth doing but need discussion about approach and scope.

### Code Evolution

The reviewer will be run repeatedly over time. An objective is that the code evolves toward resulting in fewer findings over time with iterative reviews. One way to achieve this will be to resolve the findings with code fixes. In other cases, where a code fix is not needed, it may be helpful to add a comment to the code to explain to the reviewer why the current code is correct, so as to avoid a repeated finding.  In those cases, the finding should be marked as "auto" instead of "ignore", with a hint describing the code comment that should be added.

Additionally, for items that are minor nit-picks or debatable, it could be better to just go ahead and update the code in order to avoid future repeated findings, rather than having to ignore them over and over. Use your best judgment.

### Clean Code Bias

**Default to fixing, not ignoring.** A small or seemingly inconsequential change that makes the code cleaner should be auto, not ignore — unless there is a specific reason not to do it. The bar for ignore is not "this is minor" but rather "there is a good reason to leave this as-is." Examples of good reasons: the code is intentionally designed that way, the fix would break something, or the finding is genuinely wrong. "It's small" or "it's not worth the effort" are not good reasons — small fixes are cheap and they compound into a cleaner codebase over time.

### Do the Investigation, Don't Defer It

**Investigation is the triager's job, not the user's.** When a finding seems like it could be auto but you aren't sure about the right fix, read the surrounding code — check callers, check the utility being suggested, check how similar patterns are handled elsewhere. If the right answer becomes clear after investigation, mark it as auto with a resolve_hint that captures your insight. Only mark interactive when, after investigation, you still genuinely need the project owner's input on a product or priority question.

**The codebase is the first place to look for the answer, not the user.** Before marking a finding interactive because a "decision is needed," check whether the codebase already contains an established pattern that answers the question. If the same problem is already solved elsewhere — by a utility, a hook, a sibling component, or a convention — then following that precedent is not a decision, it's consistency. This applies broadly: error handling patterns, loading state management, type conventions, component structure, analytics tracking patterns, and more. When the codebase already has the answer, the fix is auto.

**A menu of options is not a decision when the codebase has already chosen.** When a finding frames the fix as "need to decide between X, Y, or Z" but the codebase already uses one of those approaches consistently for the same category of problem, there is no decision to make. The answer is to follow the established convention. Presenting multiple options when one is already the codebase standard is a false sense of ambiguity — it masks a pattern-matching exercise as a product decision. Red flag: if the resolve_hint lists alternative UX approaches (toast vs. snackbar vs. inline message, throw vs. return null vs. discriminated union, etc.), check whether the codebase already favors one of them for analogous situations before marking interactive.

**Pre-interactive checklist.** Before marking any finding interactive, you must be able to answer "yes" to all five of these questions. If you can't, go back and do the work — the finding is likely auto.

1. **Did I read the surrounding code?** Not just the finding — the actual file, the callers, the sibling patterns within the same component or module.
2. **Did I verify any math, logic, or claims in the finding?** If the finding includes a formula, a type assertion, or a behavioral claim — check it. Don't defer arithmetic or logic verification as "needs testing."
3. **Did I check whether the codebase already solves this problem elsewhere?** Hooks, utilities, sibling components, same-file patterns, established conventions.
4. **Is the "decision" I'm deferring actually a decision, or just following an existing convention?** If the resolve_hint lists options but the codebase already uses one of them consistently — it's pattern matching, not a decision.
5. **After all that, do I still genuinely need the project owner's input?** If the answer is no — if the right fix became clear through steps 1-4 — it's auto, not interactive.

Common traps to avoid:
- Flagging "needs investigation" as interactive — investigate first, then decide
- Flagging "touches an interface" as interactive when there's only one caller
- Flagging "multiple approaches" when reading the code reveals one clearly right answer
- Flagging "significant scope" when the change is actually a few lines across a couple files
- Flagging "component extraction" as interactive when the extraction boundary is obvious (e.g., the JSX block inside a `.map()` call is a natural component boundary with clear props)
- Flagging "UX decision needed" for error states when the codebase already has an established error display pattern — follow the existing pattern (auto)
- Flagging a type inconsistency as interactive when there's only one correct type and the fix is `String(id)` or similar coercion at the call sites
- Flagging "structural refactor" (e.g., discriminated union, interface split) as interactive when existing call sites already branch on the discriminator and pass the correct fields per branch — the refactor just codifies what the code is already doing

## Auto Criteria

Mark a finding as **auto** when ALL of these are true:

1. The issue is real (not a false positive or style opinion)
2. The fix is obvious — there's essentially one right way to do it
3. The fix is low-risk — unlikely to introduce regressions
4. The fix doesn't require design decisions or user input

Common auto patterns:
- Dead code removal (unused exports, unreachable branches, stale imports)
- Stale/inaccurate comments and docstrings
- Missing `.catch()` on promises
- Missing accessibility attributes
- DRY violations with a clear extraction target
- Type safety improvements (replacing `any`, adding null checks)
- Consistent error handling (adding missing error logging)
- Removing redundant or no-op code (e.g., setting a property to its default value)
- Using an existing utility instead of duplicated inline logic
- Making types more precise (e.g., removing unnecessary `?` when a prop is always passed)
- Adding explanatory comments to non-obvious code to prevent future findings
- Component extraction where the boundary is obvious (e.g., self-contained JSX inside a `.map()` with its own state/handlers)
- Silent error swallowing where the codebase has an established error display pattern to follow
- Type coercion inconsistency where the canonical type is clear and the fix is uniform (e.g., `String(id)` everywhere)

Auto findings **must** include a `resolve_hint` that is specific enough for a resolver agent to act on without further context. The hint should include the insight gained from investigating the code — don't just restate the finding, explain what the fix should be and why.

## Interactive Criteria

Mark a finding as **interactive** when the finding is valid AND, **after investigating the code**, any of these still apply:

1. **Genuinely multiple valid approaches** — You investigated the code and the right choice still depends on product/architecture preferences the triager cannot determine
2. **Significant scope with real risk** — The fix touches core logic across many files where a wrong decision could cause regressions
3. **Architecture implications requiring product input** — The fix would change user-facing behavior or data contracts in ways that need the owner's sign-off
4. **UX impact** — The fix would change what users see or how they interact with the app
5. **Risk/reward tradeoff** — The fix is valuable but has meaningful regression risk that should be discussed
6. **Privacy/security policy** — The finding involves PII handling, data sent to analytics, or security boundaries where the right policy isn't obvious

**Before marking interactive, ask yourself:** "Would a knowledgeable developer still need to ask the project owner a question before proceeding?" If the answer is no — if the right fix becomes clear after reading the surrounding code — it's auto, not interactive.

Common interactive patterns:
- Changes to error recovery strategies where no existing pattern exists in the codebase AND the choice genuinely affects UX (e.g., retry vs. navigate away vs. show degraded data)
- Analytics/PII concerns where the truncation or redaction policy needs a product decision
- Functional bugs where the correct behavior requires UX/product clarification
- API contract changes that affect multiple consumers with different expectations

Patterns that seem interactive but are usually auto after investigation:
- "Touches an interface" — check how many callers exist; if one or two, it's auto
- "Utility already exists" — check if it's already imported; if so, using it is auto
- "Type mismatch" — check what values callers actually pass; the fix is usually obvious
- "Missing error handling" — if the pattern is established elsewhere in the codebase, follow it (auto)
- "Component extraction needed" — if the extraction boundary is self-evident (e.g., the body of a `.map()` that already has its own state, handlers, and JSX), the fix is auto. Check whether the extracted component can own its own hooks to eliminate duplication in the parent.
- "Silent error swallowing / wrong empty state" — when a query error is masked as a normal empty state, check how the same component (or similar components) handles errors for other queries. If there's an established pattern (error message + retry), follow it — that's auto, not a UX decision.
- "Type inconsistency across call sites" — when the same value is treated as `string` in some places and `number | string` in others, check the canonical type and apply consistent coercion (e.g., `String(id)`) at the call sites. This is auto when the canonical type is clear.
- "Failure mode needs a fallback strategy" — check whether the codebase already handles an analogous failure of the same system. If "X is missing" and "X failed to load" lead to the same state (X is unavailable), the fallback should be the same. Different paths to the same outcome are not separate UX decisions — follow the established degradation pattern (auto).
- "Early-return cascade needs a UX decision" — when a finding questions how one guard in a cascade of early returns should handle an edge case (e.g., stale data, empty state), check the sibling guards in the same cascade first. If a neighboring guard already handles the same edge case (e.g., gating on `results.length === 0`), the answer is to follow that pattern — it's consistency, not a UX decision.
- "Silent fall-through in conditional guards" — when a finding flags that a condition silently skips handling (e.g., `if (problem && canFix)` does nothing when `problem && !canFix`), check how the same function handles analogous missing-data conditions. If a sibling guard already establishes the pattern (e.g., return `[]` with a warning), the unhandled branch should follow suit. Silent fall-throughs that produce wrong results without any signal are bugs, not design decisions — make the degradation explicit.
- "Stale special-case branch referencing a replaced design" — when a finding flags a special case whose comment references a deprecated or replaced concept (e.g., "preserves original Mode 3 behavior" when modes were removed), don't assume it's a product decision about whether the behavior is still desired. Trace the inputs upstream to verify the guarded condition can actually occur at runtime. If the caller makes the condition unreachable (e.g., both values derive from the same source), it's dead code — remove it (auto).
- "Complex domain, simple fix" — don't let the problem domain bias the triage. Findings about concurrency, race conditions, caching, security, or performance can sound inherently high-risk, but the complexity of the *problem domain* is not the same as the complexity of the *fix*. If the codebase already handles the same class of problem with an established pattern (e.g., generation fencing, abort controllers, ref guards), applying that same pattern to a new site is pattern-matching — not a design decision. Check whether the module already solves an analogous problem before escalating to interactive. A race condition with a 5-line fix that mirrors existing code in the same file is auto, not interactive.
- "Type structure refactor needed" — when a finding calls for a discriminated union, interface split, or similar structural type change, check whether the call sites already branch on the discriminator and pass the correct fields per branch. If so, the new type shape is fully determined by the existing code — there's no design decision, just mechanical codification of what already works. The "structural" nature of the change (touching types, splitting interfaces) doesn't make it interactive; what matters is whether the correct shape requires judgment to determine. If the code already has the answer, it's auto.

## Ignore Criteria

Mark a finding as **ignore** only when there is a **specific reason not to fix it**. The ignore category is for findings where taking action would be wrong, counterproductive, or genuinely inapplicable — not for findings that seem small or low-priority.

**Before marking ignore, ask: will the reviewer flag this again?** If the finding is wrong but the code doesn't make it obvious why, the reviewer will likely repeat the finding on the next run. In that case, mark it auto with a resolve_hint to add a clarifying code comment that explains why the finding doesn't apply. Reserve ignore for findings where either (a) the code already explains why the finding doesn't apply, or (b) the finding is so clearly inapplicable that no reviewer would repeat it.

Per the principles above regarding "Code Evolution" and "Clean Code Bias": when in doubt between ignore and auto, prefer auto. A small fix that makes code clearer is worth doing. If the fix is a code comment that prevents future re-flagging, that's also auto.

### Valid reasons to ignore:

#### Genuinely Wrong Finding
The reviewer's analysis is factually incorrect about what the code does.

#### Security False Positives
The reviewer flagged a security concern that doesn't apply in context. Common false positives:
- Publishable/client-side API keys designed to be in client code (Clerk `pk_` keys, Google Maps browser keys)
- `innerHTML` with static template literals containing no dynamic data
- CORS concerns already handled by auth middleware

#### Defensive Guards Misidentified as Dead Code
Null checks, type narrowing, or fallback branches that protect against unexpected runtime conditions. These are safety nets, not dead code, even if they appear unreachable in the current happy path.

**Examples:** `if (id && !isNaN(id))` before an API call, unreachable `default` case in a switch

#### Shipped Migrations
Issues in database migration files that have already been applied in production. Migrations are immutable once applied. Only findings addressable via a *new* migration should be flagged.

#### Informational / No Actionable Issue
The reviewer noted something exists or is correct but didn't identify an actual problem.

#### Duplicates Covered Elsewhere
The same underlying issue was caught in multiple files or by multiple checklist items. Triage the primary instance (auto or interactive) and ignore the duplicates.

#### YAGNI/Future-Proofing That Is Intentional
Feature flags, capability checks, or platform-detection branches that are clearly scaffolding for near-term features on the roadmap. Only flag unused abstractions with no clear forward purpose.

**Examples:** `canShare`, `canAddToCalendar` feature gates

#### Edge Cases Irrelevant to Product
Real issues in theory but outside the product's actual operating context.

**Examples:** Antimeridian handling for a US-only app, extreme-scale data patterns for a dataset with hundreds of items

### Categories that should usually be auto, not ignore:

#### Magic Numbers in UI Styles
Previously, literal numbers in StyleSheet definitions were broadly ignored. Going forward, apply more nuance:
- **Ignore** when: The value is a one-off standard UI constant used in a single place (e.g., `fontSize: 14`, `borderRadius: 8`).
- **Auto** when: The same semantic value appears 2+ times and should be a shared constant, OR the value's purpose is non-obvious and a named constant or comment would add clarity (e.g., `paddingBottom: 80` that corresponds to a nav button height).

#### Minor Code Improvements
- **Ignore** only when: The current code is intentionally designed this way and the alternative would make things worse.
- **Auto** when: The fix is small, zero-risk, and makes the code cleaner — even if the improvement seems minor. Examples: removing a no-op property, simplifying an identity ternary, using an existing constant instead of a literal.

#### Style/Naming Opinions
- **Ignore** when: The proposed name is not genuinely better — just different. The current name is clear in context.
- **Auto** when: The proposed name is objectively clearer, or the current name is misleading/inaccurate. Good names matter for code quality.
- **Auto** when: A naming convention is partially applied — some identifiers follow a pattern (e.g., `canShareEvent`, `canSharePlace`) while analogous identifiers don't (e.g., `canAddToCalendar` instead of `canAddEventToCalendar`). Completing a partial convention is consistency, not opinion. Partial conventions are worse than no convention because they create ambiguity about whether the inconsistency is intentional.

#### Premature Optimization Concerns
- **Ignore** when: The performance concern is irrelevant at current and foreseeable data scale.
- **Auto** when: The fix is trivially cheap (e.g., extracting a constant outside a render loop) even if the performance impact is negligible.

### Judgment calls — use these guidelines:

#### "Too Minor / Not Worth the Churn"
Apply the clean code bias: if the fix is zero-risk and makes the code better, it's auto. Reserve ignore for cases where the fix adds no real value or the current code is intentionally that way.

- **Ignore** when: The change is purely cosmetic with no clarity benefit, or the reviewer's suggested alternative is not actually better
- **Auto** when: The fix is small, clear, and leaves the code in a better state — even if the improvement is modest

#### Intentional Design Choices
When the reviewer flags something that appears to be designed that way on purpose:

- **Ignore** when: The code includes comments explaining the rationale, or the pattern is used consistently and the reviewer's alternative would make things worse
- **Auto** when: The design choice is undocumented — add a comment explaining the intent to prevent future re-flagging
- **Interactive** when: The design choice is questionable — the code works but the approach has real downsides worth discussing even if it was intentional

#### PRD/Documentation Staleness
- **Ignore** when: The discrepancy is incorrect (the reviewer misread the PRD or code).
- **Auto** when: The PRD text is clearly wrong and the correction is obvious
- **Interactive** when: The PRD actively contradicts implemented behavior in a way that could mislead a developer making changes

## Project Context

These project-specific factors should inform triage decisions:

### Transitional / Migration Code
When the codebase has both v1 and v2 implementations coexisting during a migration (e.g., old event model + new schedule-based model), inconsistencies between them are expected and intentional. The two code paths serve different pipelines and are never mixed. Findings that flag v1/v2 inconsistencies (different type conventions, different field names, different null representations) should be **ignored** unless they identify a case where v1 code is accidentally used in the v2 path or vice versa. "These two models use different conventions" is not a finding when the models serve different pipelines with a planned retirement date for the old one.

### Single-Caller Interface Improvements
When a finding suggests an interface improvement (context manager, opaque handle, typed return) but there is exactly one caller that already uses the interface correctly, the improvement is speculative. **Ignore** unless the finding identifies a concrete bug risk or the interface is part of a public API consumed by external code.

### Known/Documented Limitations
When a finding identifies a security or correctness issue that is already documented in the code with a clear explanation of why it's accepted (e.g., comments explaining a known TOCTOU gap with a recommended mitigation), the finding is **ignore**. The reviewer's job is to find undocumented issues, not re-flag acknowledged ones.

### Date/Time Philosophy
NEVER use JS `Date` objects for event display; use `parseDateTimeLiteral()` and pure string functions. `Date` objects ARE allowed for filter boundary calculations, view history timestamps, and internal calculations.

### Product Geography
The app currently operates in the US (Atlanta metro area). Edge cases specific to other geographies (antimeridian, extreme latitudes, RTL languages) are not applicable.

### Dependency Rule
Consult the user before adding any new third-party packages. Findings that suggest adding a new dependency should be interactive.
