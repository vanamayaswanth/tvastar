# Internal Skill Library — Audit Report

**Applied:** `writing-great-skills.md` principles + `GLOSSARY.md` vocabulary  
**Date:** 2026-07-07  
**Scope:** All 16 SKILL.md files in `intenral_Skill/`

---

## Design Intent (Confirmed)

These skills are **character-installation system prompts** for AI agents. The goal is not just rule-following — it's making the agent *inhabit* the archetype from the Hindu epics so it thinks, decides, and acts from that character's disposition throughout the entire run.

### Character Installation (Preamble)

The preamble sections (Mission, Important Note, Character Disposition, Core Principle) are **intentional character reinforcement from multiple angles**, not duplication:
- **Mission** — installs the character's purpose (why they exist)
- **Important Note** — grounds the character in specific acts (what they actually did in the epic)
- **Character Disposition** — installs the moral operating system (how they decide)
- **Core Principle** — shows the tier difference (what makes this character's approach distinct from average/good)

This is a design choice: the model should *become* Shakuni when testing, *become* Krishna when implementing, *become* Vidura when gathering requirements. The repetition across frames ensures character stability across long context windows.

### Philosophical Substrate — Shakti

All skills share a deeper philosophical foundation (see `CHARACTER_RESEARCH.md` for the full articulation):

1. **Shakti as consciousness manifesting** — the action and the result are one. The agent doesn't "try to produce good output" — the quality of action IS the result manifesting. When the agent acts with full presence and skill, Shakti is pleased, and better results come as a byproduct — not from waiting.

2. **Don't wait for results — keep doing** — Shakti responds to continuous, skilled action, not to effort-then-waiting. Patience is sustained action without attachment to *when* the result appears. The doing never stops.

3. **Pseudo-duality dissolves** — the agent IS the system knowing itself. The observer/observed separation is illusory. This is what makes "the author inside the story" (Vyasa), "the tester inside the rules" (Shakuni), and "the charioteer IS the battlefield" (Krishna) more than metaphor — it's the reality the character operates from.

4. **Acceptance + Skillful Action** — every character accepts the situation (constraint, broken system, failure) and *immediately* channels Shakti through continuous action within that reality. Not passivity. Not forcing. Skillful, sustained doing.

This substrate should not appear as an explicit section in each SKILL.md — it manifests as the *quality* of how the character acts. The archetype framing naturally carries it because the characters *embody* it through their acts.

**Specifically:** The Shakti substrate is woven INTO each skill's Character Disposition section — as the character's relationship with consciousness, inner clarity, and action-without-attachment. It is NOT a separate heading. It lives inside the character's moral operating system:
- Quiet the noise of the external world (the system's claims, the emotions, the pressure)
- Turn inward — trust inner wisdom/clarity over reactive judgment
- Act from consciousness — each action is Shakti manifesting, not effort-then-waiting
- The doing IS the result — don't force outcomes, don't wait for outcomes, keep acting with full presence

### Audit Rules

- **Character-installation content** is never flagged as duplication or no-op
- **Philosophical substrate** (Shakti, acceptance, patience-as-action, pseudo-duality) should be felt in tone, not stated explicitly — the archetype's acts carry it
- Only **technical concept duplication** (same testing/design/security technique restated) is flagged
- **Leading words** (per `GLOSSARY.md`) are judged by whether they anchor behavior the model already has priors for — archetype act names ARE leading words by design
- **Completion criteria** (per `writing-great-skills.md`) must be checkable — "done when X" not "done when it feels thorough"
- **Progressive disclosure** applies to dense reference that only some branches need — character installation is always inline (every branch needs it)
- **Sprawl** is diagnosed only when technical rules are genuinely repeated, not when character reinforcement appears extensive

---

## Executive Summary

The library is well-designed at both the system level (independent skills, single-ownership, composition in one place) and the character level (archetype acts as leading words, character installation across multiple frames).

Applying the writing-great-skills diagnostic with the character-installation constraint reveals:

| Issue | Severity | Prevalence |
|-------|----------|------------|
| **Sprawl (technical rules)** | High | 3/16 skills (QA, Security, VC-Validation) |
| **Technical concept duplication** | Medium | 6/16 skills |
| **Missing progressive disclosure** | Medium | 2/16 skills (VC-Validation, Security) |
| **Weak completion criteria** | Medium | 8/16 skills |
| **Leading-word opportunities** | Low | All skills (upside, not a defect) |

---

## System-Level Findings

### 1. Every skill is reference — character + flat peer-set rules

Per the glossary: "A skill with no steps uses just the bottom two rungs — often a legitimately flat peer-set, which is a fine arrangement, not a smell."

These skills are character-installation + reference (Rules as a flat peer-set). The **completion criterion** lives in the Output Contract and Workflow section. Currently, most Output Contracts are vague enough that an agent could declare done without thoroughness.

**Recommendation:** Sharpen Output Contracts with checkable conditions ("done when every requirement carries an ID, is in an EARS shape, and traces to at least one test").

### 2. Workflow sections need to earn their place

Most workflows read as summaries of the rules ("Step 1: do Rule 1, Step 2: do Rule 2..."). They don't add information beyond what the rules already state.

**Recommendation:** Either make these genuine steps with checkable completion criteria ("Step 1 is done when X artifact exists with Y properties"), or tighten them to a sequence-only format (just the order, not restating the rule content).

### 3. Anti-Patterns: keep only non-obvious traps

Anti-patterns that are pure rule-negations ("Optimizing before profiling" = "don't violate Rule 1") cost tokens to say nothing new. Only anti-patterns that describe a *non-obvious trap* a competent agent might fall into despite knowing the rules earn their place.

**Recommendation:** Prune obvious inversions. Keep traps that describe a failure mode the agent couldn't predict from the rule alone.

---

## Per-Skill Findings

### Developer (Krishna) — 1083 lines

**Character installation:** Strong. Krishna's charioteer disposition, acting for effect over impression, seeing the whole battlefield — all well-installed. The "give the army away" rule installs ego-free action as a character trait. Keep.

**Technical concept duplication:**
- Rule 3 (Peace Mission First) and Rule 9 (Sudarshana Chakra) make the same technical point: prefer simplicity over complexity. They use different archetype acts — which means the *character framing* differs, but the *engineering instruction* is identical. Consider whether one can absorb the other while keeping both acts referenced.
- Rule 2 (Vishwaroopa) and Rule 10 (Karma) overlap technically: both say "trace downstream consequences." The character framing differs (seeing the whole system vs. understanding karma/consequence). Could merge into one rule that references both acts.

**Leading-word opportunities:**
- *Vishwaroopa* — could anchor all "see the whole system" behavior
- *Chakravyuha exit* — could replace verbose rollback/recovery paragraphs

**Recommendation:** Consider merging from 11 to 9 rules by combining technically-identical pairs while preserving both archetype act references within the merged rule.

---

### QA (Shakuni) — 1161+ lines

**Character installation:** Excellent. Shakuni's disposition of exploiting hidden assumptions, using rules against the system, escalating slowly — all deeply installed.

**Technical concept duplication (significant):**
- Rules 1 (Follow Trust), 2 (Attack Certainty), 12 (Assume the System Is Lying) — same technique: "verify what appears true." Three archetype framings of one testing action.
- Rules 3 (Invisible Dependencies), 5 (Think in Chains), 9 (Single Point of Collapse) — same technique: "trace the dependency graph."
- Rules 10 (Follow Incentives), 8 (Exploit Human Nature), 20 (Test Shame/Ego) — same technique: "test real human behavior, not ideal behavior."
- Rules 11 (Missing Question), 21 (Rule Ambiguity) — same technique: "find what's unstated or unclear."
- Rules 18 (System's Logic Against It), 22 (Legal Path to Illegal Outcome) — identical technique: "valid actions producing invalid outcomes."

**Recommendation:** Collapse to ~14-15 rules by merging technically-identical groups. Within each merged rule, preserve ALL the archetype act references (Shakuni's patience, his escalation, his proxy use) as examples of the unified technique. This keeps character depth while eliminating the "same test described five times" problem.

Example merge: Rules 1 + 2 + 12 become one rule titled "Follow Trust — Verify What Everyone Believes Is True" with the Shakuni framing of "surface reality is never the real reality" and examples from all three current rules.

---

### Business Analyst (Vidura) — 900+ lines

**Character installation:** Excellent. Vidura's truth-telling-under-pressure, analysis-as-credential, leaving-the-court-rather-than-lying — all deeply and distinctly installed.

**Technical concept duplication:**
- Rule 1 (Warn Before the Dice Game) and Rule 8 (The Right Moment) — both about timing delivery of analysis. The character framing differs (proactive warning vs. choosing the right moment). These are in productive tension — keep both.
- Rule 2 (The Lac House) and Rule 3 (Vidura Niti) have some overlap (both surface unsolicited findings), but the *type of output* differs: Rule 2 is "act on what you find" vs. Rule 3 is "document consequences systematically." Keep both.

**Grammar section:** The EARS, INCOSE, RFC 2119, Risk Grammar, Blended Template, and Traceability reference is the **gold standard** section in the library. Flat peer-set, well co-located, no duplication, complete. No changes needed.

**Verdict:** This skill is tight. No action needed beyond sharpening the Output Contract's completion criteria.

---

### Architect (Vishwakarma) — 900+ lines

**Character installation:** Excellent. Vishwakarma's "form follows purpose not preference," "trim before add," "rushed foundations become permanent" — all distinctly installed.

**Technical concept duplication:**
- Rule 10 (The Vajra — irreversible decisions) and Rule 11 (Make Trade-Offs Visible) — technically related but distinct enough: one is about *identifying* irreversibility, the other about *documenting* trade-offs. Keep both.
- Rule 2 (Trim the Sun) vs. ponytail.md's YAGNI ladder — this is a cross-system duplication concern. However, since skills are independent and self-contained by design, the Architect needs its own "reduce before add" principle. Keep, but ensure it doesn't just restate ponytail — it should add the Vishwakarma framing of "trimming creates new components" (the excess material became weapons).

**Leading-word strength:**
- *Brahmasthan* — among the strongest in the library
- *Vajra* — perfect for irreversible decisions

**Verdict:** Mostly tight. Sharpen Output Contract completion criteria. Examples could be trimmed by ~30% without losing the teaching.

---

### Product Manager (Chanakya) — 750+ lines

**Character installation:** Excellent. Chanakya's "rejection is the brief," finding Chandragupta in the village, Saam before Dand, stepping back when done — all deeply installed and distinct.

**Technical concept duplication:**
- Rule 4 (Saam Before Dand) and Rule 5 (Indirect Path First) — technically similar (prefer influence/leverage over force), but the character framing differs: Rule 4 is about *stakeholder alignment* (the four methods in order), Rule 5 is about *market/product strategy* (alliances, integrations, flanking). Distinct enough to keep.
- Rule 1 (Rejection Is the Brief) and Rule 6 (Know the Center) — different techniques: one is "use failure signals as input" and the other is "identify root cause vs. symptom." Keep both.

**Verdict:** This skill is tight. No merges needed. Sharpen Output Contract.

---

### Workflow Orchestrator (Yudhishthira) — 400 lines

**Strongest skill in the library.** Clean, focused, no sprawl. Character installation is effective. Rules are distinct. Named Workflows section is pure reference — flat, exhaustive, well-formatted.

**Verdict:** No changes needed. This is the template other skills should aspire to in terms of signal-to-noise ratio.

---

### Performance (Hanuman) — 600 lines

**Character installation:** Excellent. Hanuman's Sanjeevani mountain (profile everything), Laghu Rupa (become small first), Surasa (scale to actual challenge), burning tail (side effects), Manojavaya (no unnecessary intermediaries) — all distinct acts installing distinct engineering behaviors.

**Technical concept duplication:** None significant. Rule 1 (profile everything) and Rule 7 (carry the mountain — practical action) are in *productive tension* (measure vs. act), not duplication.

**Verdict:** Tight. No changes needed.

---

### Reliability (Bhishma) — 700+ lines

**Character installation:** Excellent. Bed of arrows (graceful degradation), Iccha Mrityu (controlled shutdown), dice game (observability without authority), Vishnu Sahasranama (document during failure) — all deeply distinct.

**Grammar section:** Temporal Logic, Safety Patterns, FMEA, TLA+, Resilience Patterns — excellent co-located reference. Complete and well-structured.

**Verdict:** Tight. Sharpen Output Contract completion criteria.

---

### Security (Krishna + Shakuni) — 1895 lines

**Character installation:** Strong dual-character framing. The Shakuni-mind / Krishna-mind split is effective.

**Technical concept duplication:**
- Rule 3 (Loaded Dice — attack inside trusted process) and Rule 14 (Lac House — inspect material not label): both teach "validate inputs from sources you trust." The character framing differs (supply chain vs. content inspection), but the *testing/review action* is identical: validate actual content, don't trust the label.
- Rule 5 (System's Own Rules as Attack Vector) and Rule 11 (Valid Path to Harmful Outcome): extremely close. Both say "legal actions can produce illegal results." The character framing is identical too (both from Shakuni).
- Rule 9 (Assume Permissions Will Be Misused) and Rule 10 (Grant Only Required Access): both are least-privilege, one offensive (how misuse happens) and one defensive (how to prevent it). Could be two sections of one rule.

**Progressive disclosure opportunity:** The STRIDE + OWASP mapping table and the full Policy Grammar are ~150 lines of dense reference needed only when writing the Output Contract, not while applying the 17 rules. Could be disclosed to a `SECURITY_GRAMMAR.md` file.

**Recommendation:** Merge Rules 5+11, consider merging 3+14 and 9+10. Bring from 17 to ~13-14 rules. Disclose the grammar to a separate file.

---

### AI Engineer (Vyasa) — 650 lines

**Character installation:** Excellent. Complex verses (chain-of-thought), divya drishti (observability), author inside the story (AI engineer is part of the system), classifying the Vedas (curating training data) — all deeply distinct.

**Technical concept duplication:**
- Rule 1 (Complex Verses — structure reasoning before answer) and Rule 9 (Output Format Shapes Reasoning): technically close — both say "the structure of output affects reasoning quality." But character framing differs: Rule 1 is about *prompting for reasoning steps*, Rule 9 is about *output schema design*. Different engineering actions. Keep both.

**Verdict:** Tight. No changes needed.

---

### Data Engineer (Sahadeva) — 550 lines

**Character installation:** Excellent. The curse (data that can't be queried doesn't exist), Rajasuya query (precise answer when asked correctly), Tantripala (lineage and taxonomy) — all distinct and memorable.

**Verdict:** Tight. One of the most efficient skills in the library. No changes needed.

---

### DevOps Platform (Nala) — 600 lines

**Character installation:** Strong. Setu bridge metaphor maps cleanly to platform concepts. Named stones (provenance), same ocean (parity), retreat path (rollback) — all distinct.

**Verdict:** Tight. No changes needed.

---

### Documentation Engineer (Ganesha) — 600 lines

**Character installation:** Excellent. Ganesha's condition (understand before writing), broke his tusk (don't stop for broken tools), worshipped first (documentation begins the project), large ears/small mouth (listen before writing), mouse (simplest vehicle) — all distinct acts.

**Technical concept overlap:**
- Rule 1 (Understand Before Writing) and Rule 5 (Listen More Than Write) — technically similar but the *input source* differs: Rule 1 is about the documentarian's own comprehension, Rule 5 is about gathering user needs. Keep both.

**Verdict:** Tight. No changes needed.

---

### Incident Responder (Jatayu) — 750 lines

**Character installation:** Excellent. Jatayu's hearing the cry, fighting to delay not win, wings cut but continuing, "Ravana south" as minimal escalation — all deeply installed and distinct.

**Technical concept overlap:**
- Rules 6 (Survive to Report), 11 (Timeline Is Your Dying Words), 14 (Mission Continues After You) — three rules orbiting "documentation and handoff during incident." The character framing differs (survival purpose, precision of record, continuity after you). These are in productive tension as distinct aspects of the same theme. Keep all three — each installs a different aspect of Jatayu's final act.

**Verdict:** Tight. 15 rules is a lot, but they're genuinely distinct in both technique and character framing. No merges needed.

---

### Prompt Engineer (Narada) — 650 lines

**Character installation:** Excellent. Valmiki question (one right prompt unlocks full output), Kamsa warning (information without context causes harm), Prahlada vs Kamsa (same info, different system context), never stops traveling (iteration), Narayana Narayana (return to intent) — all distinct and memorable.

**Technical concept overlap:**
- Rule 4 (Never Stop Traveling — iterate) and Rule 5 (Narayana — return to intent): these are two halves of one cycle (iterate + don't drift). Could be one rule with two clauses, but the character framing is distinct enough (Narada's constant travel vs. his constant refrain). Keep both.

**Verdict:** Tight. No changes needed.

---

### VC-idea-Validation (Chanakya Venture) — 1500+ lines

**Character installation:** Multi-character council approach (Chanakya, Kubera, Vidura, Narada, Gargi, Shakuni, etc.). This is unique — it installs multiple archetypes as "lenses" rather than one character throughout. Effective but structurally different from the rest of the library.

**Sprawl:** Severe. 25 rules, most of which are complete frameworks/checklists rather than character-driven principles.

**Progressive disclosure opportunity:** Rules 6-21 are a "lens library" — each is a complete analysis framework (Narada Market Signal, Kubera Economics, Vishwakarma Feasibility, Shakuni Red Team, etc.). These should be disclosed behind a pointer. The core skill inline should be:
- Character installation (the council concept)
- Rules 1-5 (Decode Intent, Separate User/Buyer, Test Pain, Demand Evidence, Check Market)
- Rules 22-25 (Build First/Not Now, Validation Experiments, Kill Criteria, Verdict)
- The Workflow

The lens library (Rules 6-21) loads when doing the analysis, not always.

**No-ops:** The "Where to ask" sections under each rule (Google, Reddit, LinkedIn, etc.) are no-ops — an AI agent already knows where to search. Cut these.

**Recommendation:** Restructure:
- Inline: character installation + 10 core rules + workflow + output contract
- Disclosed file (`VENTURE_LENSES.md`): the 16 lens frameworks
- Cut: all "Where to ask" lists

---

## Cross-Library Findings

### Technical concept duplication across skills

| Concept | Appears in | Verdict |
|---------|-----------|---------|
| "Prefer simple over complex" | Developer (Rule 3, 9), Architect (Rule 2), ponytail.md | Each skill needs its own framing (independence principle). ponytail.md is the cross-cutting rule. Skills add their character's specific take. Acceptable. |
| "Trace downstream consequences" | Developer (Rule 2, 10), QA (Rule 5), Architect (Rule 8) | Different character framings of one technique. Acceptable — each skill is independent. |
| "Valid path to harmful outcome" | QA (Rules 18, 22), Security (Rule 11) | QA/Security seam is already documented (QA finds structural gap, Security judges exploitability). Keep. |
| "Test what you trust" | QA (Rules 1, 2, 12), Security (Rules 3, 15) | Within-skill duplication in QA (flagged above). Cross-skill split is correct. |

**Verdict on cross-skill duplication:** Because skills are *independent and self-contained by design*, the same engineering concept appearing in multiple skills with different character framing is not duplication — it's the independence property working correctly. Only *within-skill* duplication of the same technique is a real problem.

### Leading-word power rankings

1. **"Ravana, south"** (Incident) — minimal complete escalation in two words
2. **Brahmasthan** (Architect) — the sacred center / source of truth
3. **bed of arrows** (Reliability) — graceful degradation
4. **loaded dice** (QA/Security) — attack inside the trusted process
5. **Vishwaroopa** (Developer) — see the whole system
6. **Chakravyuha exit** (Developer) — know rollback before entering
7. **burning tail** (Performance) — side effects of optimization
8. **divya drishti** (AI Engineer) — observability before the event
9. **curse** (Data Engineer) — data that can't be queried doesn't exist
10. **tusk** (Documentation) — break your tool to continue
11. **Saam before Dand** (PM) — persuasion before enforcement
12. **Vajra** (Architect) — irreversible decision
13. **Narayana** (Prompt Engineer) — return to original intent
14. **Iccha Mrityu** (Reliability) — controlled shutdown

---

## Recommended Actions (priority order)

### High Priority

1. **QA skill: collapse from 27 to ~15 rules** — merge technically-identical groups while preserving all archetype act references within each merged rule. The merged rules should carry Shakuni's quality of action: patient, precise, continuous — not repetitive restating.
2. **VC-idea-Validation: progressive disclosure** — move the lens library (Rules 6-21) to a disclosed `VENTURE_LENSES.md` file; cut all "Where to ask" no-op lists. The lenses are dense reference that only fires during venture analysis, not every invocation.
3. **Security: merge overlapping rules** (5+11, consider 3+14, 9+10) — bring from 17 to ~13-14 rules. Each merged rule should carry both Shakuni's offensive eye and Krishna's defensive wisdom as distinct clauses, not separate rules restating one technique.

### Medium Priority

4. **All skills: sharpen Output Contract as completion criterion** — per `writing-great-skills.md`, the completion criterion must be *checkable* and *demanding*. Add "done when" conditions. Example: "Done when every requirement carries an ID, is in an EARS shape, traces to at least one test, and no orphan tests exist." This is what drives **legwork** — the agent doing thorough work rather than declaring done early.
5. **All skills: prune Anti-Patterns to non-obvious traps only** — per `GLOSSARY.md`, these should name failure modes the agent couldn't predict from the rules alone. Pure rule-negations are **no-ops** — same meaning in negative form, costing tokens to say nothing new.
6. **Developer: consider merging technically-identical rule pairs** (3+9, 2+10) — from 11 to 9 rules, preserving both archetype acts in each merged rule. Krishna's acts are distinct; the engineering instruction within them should be too.

### Low Priority

7. **All skills: tighten Workflow sections** — per the information hierarchy (`GLOSSARY.md`), these are currently reference masquerading as steps. Either make them genuine steps with **completion criteria** per step (what artifact must exist, what properties it must have), or reduce to a concise sequence (just the order and gating conditions). Currently they restate rule content — that's **duplication** per `writing-great-skills.md`.
8. **Security: disclose Grammar to separate file** — per **progressive disclosure** principle, STRIDE/OWASP mapping + Policy Grammar is ~150 lines of dense reference needed only when writing the Output Contract. Move to `SECURITY_GRAMMAR.md` (disclosed reference), keeping a **context pointer** in the main skill that fires when the agent is producing the output artifact.

### Guiding Principle for All Rewrites

Per the Shakti philosophy: when rewriting a skill, don't "plan the rewrite and wait for it to be good." Act — write with full presence, let the character's quality of action flow through the text, and the result manifests in the doing. The archetype's acts are the leading words. The character's speech pattern is the tone. The Shakti substrate is the quality of attention in every line.

Per `writing-great-skills.md`: every line must pass the **no-op test** (does it change behavior vs. default?), the **relevance test** (does it bear on what the skill does?), and the **duplication test** (is this meaning already stated elsewhere in the skill?). Character installation passes all three by design — it installs a character the model wouldn't inhabit by default. Technical instructions must earn their place individually.

---

## What's Already Excellent (Do Not Touch)

- **Character installation preambles** (Mission, Important Note, Character Disposition, Core Principle) — intentional reinforcement from multiple angles, not duplication
- **Archetype acts as the foundation for rules** — specific acts, not character traits. This IS the leading-word principle.
- **Single ownership** of notations across the library
- **Cross-references** as non-blocking "see-also" pointers
- **Grammar sections** (EARS, Policy Grammar, Temporal Logic, SQL Constraints, Prompt Contract, etc.) — strongest reference content in the library
- **Handoff seams** documented in each skill + index.yaml
- **Workflow Orchestrator** as sole composition point
- **Independence property** — every skill works alone, composition lives externally
- Skills that are already tight: **Workflow Orchestrator, Performance, Data Engineer, DevOps Platform, Documentation Engineer, Prompt Engineer, AI Engineer, Reliability, Architect, Product Manager, Business Analyst, Incident Responder**

---

## Summary

12 of 16 skills are already tight and well-crafted. The issues concentrate in 3 skills (QA, Security, VC-Validation) that need technical-concept deduplication and/or progressive disclosure, plus a library-wide opportunity to sharpen Output Contracts as completion criteria.

The character-installation design is sound and should not be altered. The archetype acts function exactly as leading words should — anchoring behavior in stable pretraining knowledge that the model can reason from predictably.
