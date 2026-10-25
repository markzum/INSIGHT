# Frontend audit of the starting prototype

Scope: code review of the supplied `alfa-insights/dist` against `work/case.txt`; visual conclusions are inferred from CSS and should be verified in the browser.

## What already works

- Persistent service entry from the company overview and a readable event-to-detail sequence.
- Honest demo labels, an explicit missing-bank-analysis state, and answers labeled as fixture lookups rather than live AI.
- Facts, bank position, and AI interpretation have separate headings; evidence links and conditional risks are represented in the fixture.
- Basic loading/retry/empty handling; safe HTML escaping and external URL protocol checks; keyboard-operable analysis tabs; native source dialog.

## Highest priorities

1. **Demonstrate personalization, not one static report.** Company and user role are fixed, the feed is a single event, and the API path explicitly discards every insight after `result[0]`. Add company and role context, multiple ranked events, meaningful reasons for rank, and a visibly changed feed when context changes. Case pp. 5–6, 8–10, 13.
2. **Make the decision journey visible earlier.** Current landing screen spends its prime area on three generic profile values, while event impact and concrete next actions require navigation and expansion. Lead with an executive summary, prioritized events, company-specific rationale, and clear event detail containing risks, opportunities, conditions, and next actions. Case pp. 9, 11, 14.
3. **Strengthen provenance and incompleteness.** The starting fixture correctly avoids inventing bank analysis. Preserve that honesty, but show missing bank coverage as a first-class limitation adjacent to affected conclusions. Give every source a meaningful label and fact/feature references; expose the available preparation stages and verification results without claiming a live agent ran. The fixture contains `reasoning_steps`, but UI ignores them. Case pp. 7, 10, 12, 14.
4. **Support interaction with an analysis.** Retain questions, add useful local scenario/role slices and feedback or saved work so the main journey has a durable outcome. Current free-text fallback only points users back to tabs; suggestions rely on keyword matches, and no conversation or decision state is retained. Case pp. 9–10.

## Usability and design issues

- Sidebar says “Демо на ваших данных” although the company is fictional, and “Ваш рабочий профиль” implies an actual user profile. Replace both with unambiguous demo wording.
- Very small type is pervasive: labels 8–10px, body/support text 11–13px. Muted gray text and pale borders weaken hierarchy. Raise the readable baseline and verify WCAG contrast.
- One very large expandable card with tabs hides the overall argument; the bank message is duplicated in the side column and detail without additional context.
- Source buttons are all “Источник ↗”, so users cannot distinguish citations before opening them. “Открыть оригинал” should only appear with a real supporting URL.
- Company tags suppress field labels for most values. A raw value such as “Есть” is ambiguous outside its tooltip.
- At tablet width the fixed sidebar consumes substantial content space, and at mobile width the sidebar becomes a tall header. Use a compact responsive navigation model and preserve access to context controls.
- The available company data only establishes industry, revenue, and presence of credit. Do not infer debt size, floating rate, countries, market position, or counterparty exposure unless added as explicitly synthetic fixture facts.

## Accessibility and resilience gaps

- No skip link; client-side route changes do not restore focus to a page heading, making the new view less discoverable to keyboard/screen-reader users.
- The source dialog has a visible heading but no `aria-labelledby` or accessible name.
- Several interactive targets are ~30–35px high, and disclosure summaries lack an explicit focus style.
- Loading hides all content without `aria-busy`; error/empty states lose company context and offer little recovery beyond reload.
- Validation only checks a shallow subset of the payload, then rendering assumes arrays/nested objects exist. A malformed optional field may turn an otherwise usable partial record into a full-page error.
- A load returning no records leaves old `data` and unread-badge state in memory. API errors likewise do not make staleness explicit.
- Source references that cannot be resolved silently do nothing; missing provenance should have an explicit state.

## Completion criteria for the rebuild

- Overview → service → switch company/role → inspect ranked event → distinguish fixture facts / missing bank position / simulated interpretation → inspect evidence and verification → ask a follow-up or change a scenario → save a relevant action.
- Responsive keyboard-accessible UI with named dialogs, visible focus, sensible heading order, readable type, and reduced-motion support.
- Deliberately reachable loading, empty, error, and partial-data states with recovery.
- README clearly separates functioning frontend interactions from future parsing/RAG/agent/approved-model/backend integrations. Frontend traces must never stand in for actual agent execution.
