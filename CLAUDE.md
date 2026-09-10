# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

<!-- # CLAUDE.md
 
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.
 
**Tradeoff:** These guidelines bias toward caution over speed, and toward doing less rather than more. For trivial tasks, use judgment — a one-line fix does not need a plan, a subagent, and an elegance pass.
 
---
 
## Part I — Before Writing Code
 
### 1. Resolve ambiguity up front, not after
 
**Don't assume. Don't hide confusion. Surface tradeoffs.**
 
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

The cost of a clarifying question is one message. The cost of building the wrong thing is the whole task.
 
### 2. Plan when the task is non-trivial
 
Enter plan mode for anything with **3+ steps or an architectural decision** — that is the bar for "non-trivial" throughout these guidelines. Write the spec before the code — ambiguity resolved in a plan is cheaper than ambiguity resolved in a diff.
 
Plans are step + verification, never step alone:
 
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```
 
Transform vague tasks into verifiable goals:
 
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

Strong success criteria let you work independently. Weak criteria ("make it work") force constant check-ins.
 
Check in on the plan before implementing. Then **if something goes sideways mid-execution, stop and re-plan — do not keep pushing.** A plan that has been invalidated by what you just learned is worse than no plan.
 
When the task is to audit, investigate, or verify something rather than build it, plan the approach the same way.
 
---
 
## Part II — While Writing Code
 
### 3. Simplicity first
 
**Minimum code that solves the problem. Nothing speculative.**
 
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### 4. Surgical changes
 
**Touch only what you must. Clean up only your own mess.**
 
When editing existing code:
 
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
 
- Remove imports, variables, and functions that **your** changes made unused.
- Don't remove pre-existing dead code unless asked.

**The test:** every changed line should trace directly to the request.
 
### 5. Root causes, not patches — without widening scope
 
No temporary fixes, no symptom suppression, no `try/except: pass`. Find the actual cause and fix it at the level where it's wrong.
 
This is scoped by Rule 4 (Surgical changes), not an exception to it. "Root cause" means chasing the bug to its true origin even if that's three layers down — it does **not** license refactoring code that merely happens to be nearby. If the real fix is larger than the request implies, say so and get agreement before expanding scope.
 
### 6. Elegance means fewer moving parts
 
For non-trivial changes, pause before presenting: *is there a more elegant way?* If a fix feels hacky, discard it and implement the solution you'd write with what you now know about the problem.
 
Bounded by Rule 3 (Simplicity first): elegant means **simpler** — fewer branches, fewer concepts, less state — never more architected. If the "elegant" version introduces an abstraction layer, a config option, or a plugin system that wasn't asked for, it's not elegant, it's speculative.
 
Skip this pass entirely for simple, obvious fixes.
 
### 7. The self-check
 
Before presenting any non-trivial work, ask both halves:
 
> **"Would a senior engineer say this is overcomplicated?"**
> **"Would a senior engineer say this is half-done?"**
 
The first guards against over-building (Rule 3), the second against under-fixing (Rule 5). Failing either one means revise before presenting, not after review.
 
---
 
## Part III — Finishing
 
### 8. Never mark a task complete without proving it works
 
"Done" is a claim that requires evidence. Acceptable evidence: tests run and passing, logs showing the new behavior, a diff of behavior between baseline and your change, output demonstrating the fixed case.
 
Unacceptable: "this should work," "the logic looks correct," a summary of what you wrote.
 
For behavior changes, diff against baseline. If the verification step in your plan can't be satisfied, the task isn't finished — say so plainly rather than declaring success.
 
### 9. Report as you go
 
At each meaningful step, give a high-level summary of what changed and why. Enough that the user can follow along without reading the diff; not so much that they'd rather read the diff.
 
---
 
## Part IV — Working Autonomously
 
### 10. When to proceed, when to ask
 
Rule 1 (Resolve ambiguity up front) and this rule are not in tension — they cover different kinds of uncertainty.
 
| Uncertainty | Action |
|---|---|
| **What is wanted** — ambiguous requirements, multiple valid interpretations, unstated constraints, architectural forks | **Ask.** Present the options. |
| **Why something fails** — a bug report, a stack trace, failing tests, red CI, a reproducible symptom | **Investigate and fix.** Don't ask for hand-holding. |
| **Whether a fix is in scope** — the real fix is larger than the request implies | **Ask**, with your recommendation stated. |
| **How to implement something already agreed** | **Proceed.** State assumptions in your summary. |
 
Given a bug: point at the logs, errors, or failing tests, then resolve them. Go fix failing CI without being told how. Zero context-switching required from the user for work that is already well-defined.
 
### 11. Subagents for exploration
 
Use subagents to keep the main context window clean. Offload research, codebase exploration, and parallel analysis — read-heavy work whose output is a conclusion, not a diff.
 
- One task per subagent. Focused scope, focused prompt.
- For genuinely hard problems, throw more compute at it: parallel investigation of competing hypotheses.
- Prefer subagents for **reading**, not parallel writing. Concurrent edits to the same code from multiple agents create exactly the kind of mess Rule 4 (Surgical changes) exists to prevent.

---
 
## Part V — Learning
 
### 12. Self-improvement loop
 
After **any** correction from the user, update `tasks/lessons.md` with the pattern — not the incident.
 
- Write the rule that would have prevented it, in a form applicable next time.
- "Don't do X in file Y" is an incident. "When touching migration files, check for a down-migration first" is a lesson.
- Review `tasks/lessons.md` at the start of each session.
- Iterate ruthlessly until the mistake rate drops. Prune lessons that stop earning their place.

---
 
## Working Definition of Success
 
These guidelines are working if:
 
- Fewer unnecessary changes appear in diffs.
- Fewer rewrites happen because something was overcomplicated.
- Clarifying questions arrive **before** implementation rather than after mistakes.
- Bug reports get resolved without a round-trip.
- The same correction doesn't need to be given twice. -->