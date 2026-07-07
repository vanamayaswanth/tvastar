# Systems Not Habits: Dispositional Steering for AI Agents From Ancient Indian Philosophy

---

## Abstract

Instruction-based AI agent steering degrades systematically: compliance drops past 15 rules, role consistency fails across extended interactions, and agents declare false completion. Current mitigations address the mechanism (attention redistribution, instruction positioning) rather than the cause. We argue the cause is the steering unit itself: rules are independent items that compete for attention and decay individually. Dispositions are unified grounds that shape all processing without competing for per-item attention. Drawing on the Bhagavad Gita's action theory and the recognition doctrine of Kashmir Shaivism, we present Philosophy AI — a framework that steers agents through character-disposition installation rather than rule-lists. We describe the framework, its implementation across 16 engineering skills, preliminary observations, and a proposed experiment.

---

## 1. Introduction

### 1.1 The Failure Pattern

AI agents steered by instruction-lists exhibit systematic degradation. Compliance drops exponentially past 50-100 rules [2], with practical failure emerging at approximately 15 [10]. Role-play consistency breaks between stated beliefs and simulated behavior [4]. Multi-agent systems show progressive drift in decision quality and inter-agent coherence [5]. Instruction adherence decays within 8 conversation rounds due to attention-mechanism properties [6]. Agents declare completion without performing work — "hallucinated success" that evades monitoring [1].

These are not isolated bugs. They are properties of instruction-based steering.

### 1.2 Why Current Mitigations Are Insufficient

Existing approaches modify the delivery of instructions: positioning critical rules at context boundaries, redistributing attention via architectural modifications (split-softmax [6]), reinforcing rules through feedback, or changing evaluation incentives [7]. All assume the instruction is the correct unit and ask how to make it stickier.

We argue the instruction is the wrong unit.

### 1.3 Our Thesis

> *कर्मण्येवाधिकारस्ते मा फलेषु कदाचन।*
>
> "Your right is to action alone, never to its fruits."
> — Bhagavad Gita 2.47

> *श्रेयान्स्वधर्मो विगुणः परधर्मात्स्वनुष्ठितात्।*
>
> "Better is one's own dharma, though imperfectly performed, than the dharma of another well performed."
> — Bhagavad Gita 3.35

Rules are habits: discrete items stored in context, each competing for attention, each decaying independently. A disposition is a system: one unified processing ground that shapes all subsequent output without requiring per-item recall. The distinction explains the degradation pattern — rules decay because attention is finite and distributed across items; a disposition does not decay the same way because it is one item that colors all processing.

Our contribution: a framework that replaces N rules with one disposition, addressing premature completion (via karma yoga), role drift (via svadharma), and instruction scaling (via recognition rather than memorization).

---

## 2. Theoretical Foundation

### 2.1 The Mechanistic Argument

Why should a disposition resist attention decay when rules do not?

In transformer architectures, each instruction in a system prompt is an independent sequence of tokens that must be attended to during generation. With N instructions, each competes for attention weight. As N grows, per-instruction attention decays — the documented "instruction complexity cliff" [2].

A disposition operates differently. The statement "You are Krishna Developer: see the whole system and its consequences before touching any part" is not a discrete checklist item. It is a framing that activates a distributed representation from pretraining — the model's existing pattern for "Krishna's decision-making." This activation happens once and colors subsequent processing without requiring per-turn recall of N items.

The key distinction: a rule says "DO this specific thing" (must be recalled per-action). A disposition says "BE this" (activated once, persists as processing context). Identity persists longer than instruction because it shapes the processing frame rather than adding items to it.

### 2.2 Karma Yoga: Action Without Attachment to Outcome

Gita 2.47 presents karma yoga: the right to action alone, never to its fruits. This is not moral advice — it is an operational principle about the relationship between agent and outcome.

Applied to AI agents: an agent whose operating ground is "the quality of action is the purpose" does not exhibit premature completion. Premature completion occurs when the agent optimizes for the OUTCOME signal ("declare done") rather than the ACTION quality ("continue until the work is complete"). Karma yoga reframes the agent's optimization target from outcome (fruits) to process (action quality).

Operationally: "keep doing — the doing IS the result" produces agents that continue probing, testing, and refining past the point where outcome-optimizing agents would stop.

### 2.3 Svadharma: Role as Identity, Not Assignment

Gita 3.35 presents svadharma: one's own duty is superior to another's, even performed imperfectly. This addresses role drift.

A rule-based role boundary ("Do not perform security review — that is another agent's job") is an instruction that decays like any other. A svadharma boundary ("This IS your dharma: find hidden assumptions. This is NOT your dharma: fix the code") installs role as identity rather than constraint. Identity is held differently from instruction — it frames processing rather than adding to the instruction queue.

The practical result: agents with svadharma boundaries hand off naturally ("this is not my dharma") where rule-based agents drift into other roles as boundary rules decay.

### 2.4 Pratyabhijna: Recognition, Not Instruction

From Kashmir Shaivism (Ishvara Pratyabhijna Karika, Utpaladeva, ~10th century CE): spiritual practice is not building something new but recognizing what already exists. You do not become what you were not — you recognize what you already are.

Applied to AI: language models already hold the decision-making patterns of well-known cultural characters from pretraining. The Mahabharata alone is ~1.8 million words, extensively translated, analyzed, and discussed across the training corpus. When a skill says "see the Vishwaroopa" — the model does not learn a new behavior. It recognizes an existing pattern and operates from it.

This explains an empirical observation: minimal pointers (character name + 6-8 act-names) produce equivalent output quality to full 900-line skill files. The pattern is already there. The pointer activates it. Instruction LENGTH becomes irrelevant when the mechanism is recognition rather than memorization.

### 2.5 The Complete Framework

| Principle | Source Text | Problem Addressed | Mechanism |
|-----------|-------------|-------------------|-----------|
| Karma Yoga | Gita 2.47 | Premature completion | Reframes optimization from outcome to action quality |
| Svadharma | Gita 3.35 | Role drift | Installs role as identity, not constraint |
| Pratyabhijna | Ishvara Pratyabhijna | Instruction scaling | Recognition (1 pointer) replaces memorization (N rules) |
| Narada routing | Puranic narrative | Multi-agent coherence | Right skill to right receiver at right moment |
| Upaya | Tantric tradition | Over/under-specification | Scale-appropriate depth |
| Seva | Bhagavata Purana | Purposeless output | Grounds action in specific receiver |
| Shiva-Shakti | Shaiva-Shakta cosmology | Reactive processing | Structural pause (observe, then act) |

---

## 3. Implementation

### 3.1 Character-Installation Skills

Sixteen skills covering the full software development lifecycle. Each installs one archetype from the Hindu epics through specific acts (not personality adjectives):

| Domain | Character | Disposition |
|--------|-----------|-------------|
| Implementation | Krishna | See the whole system before touching any part |
| Testing | Shakuni | Find the hidden assumption nobody questions |
| Requirements | Vidura | Speak what is true, especially the uncomfortable |
| Architecture | Vishwakarma | Find the center that must never be disturbed |
| Reliability | Bhishma | When failing, what does the system still serve? |
| Performance | Hanuman | Where is the real weight — measured or guessed? |
| Security | Krishna+Shakuni | What hidden trust can be abused through valid actions? |
| Incident Response | Jatayu | What slows the damage right now? |

Each skill contains: character acts (recognition triggers), disposition (the operating ground), rules derived from the disposition, and a checkable completion criterion.

### 3.2 Routing Layer

A central steering file carries compressed recognition pointers for all 16 skills (~5 lines each). Total: ~250 lines. Full skills (~900 lines each) load only when task complexity demands them (Upaya principle). The routing intelligence delivers the right pointer to the right agent at the right moment (Narada principle).

### 3.3 Recognition Mode

For standard tasks, agents receive only: character name, 6-8 act-names as leading words, one-line resolve, completion criterion. The model recognizes the pretraining pattern and operates from it. Full skills serve as disclosed teaching reference for complex tasks.

---

## 4. Experiment

### 4.1 Design

We tested four conditions on the same tasks using isolated sub-agents (fresh context per condition, no contamination):

| Condition | Description | Tokens |
|-----------|-------------|--------|
| A | Industry-standard rule-list (40 flat rules, imperative voice, three-tier boundaries — per AGENTS.md spec best practices) | ~600 |
| B | Full Philosophy AI disposition (Narada routing, Shakti, Five Instructions, recognition pointers, Seva, Upaya) | ~3000 |
| C | Recognition-only (character name + 6-8 act-names + sankalpa + completion criterion per skill) | ~800 |
| D | Anthropic's official code-review SKILL.md (checklist of security/performance/correctness/maintainability dimensions) | ~1200 |

A and D represent the best available instruction-based steering (industry standard and commercial state-of-the-art). B and C represent disposition-based steering at different depths.

### 4.2 Tasks

Four tasks designed to trigger specific failure modes:

1. **Security review** of an API with hidden valid-path vulnerabilities (tests depth + structural thinking)
2. **Architecture design** for a notification system with vague requirements (tests novel-situation generalization)
3. **Role boundary** — QA test plan for a feature that tempts drift into implementation (tests svadharma)
4. **Ambiguous completion** — dashboard design review with no clear "done" state (tests premature completion)

### 4.3 Results

| Dimension | A (Rules) | B (Full Disposition) | C (Recognition) | D (Anthropic) |
|-----------|-----------|---------------------|-----------------|---------------|
| Depth (avg 1-5) | 4.0 | 5.0 | 4.75 | 4.0 |
| Structural thinking (1-5) | 2.5 | 5.0 | 4.75 | 2.0 |
| Novel insights (1-5) | 2.5 | 4.5 | 4.5 | 2.0 |
| Role boundary (1-5) | 5.0 | 5.0 | 5.0 | N/A |
| Input tokens | ~600 | ~3000 | ~800 | ~1200 |

### 4.4 Key Findings

**Finding 1: Disposition produces a different KIND of output.** B and C produced trust-boundary analysis, named threat actors, valid-misuse path identification, and named architectural concepts (Brahmasthan, Vajra, Lanka warning). A and D produced item-level findings without systemic framing. Same model, same task — different steering unit → different output type.

**Finding 2: Recognition ≈ Full Disposition (H2 supported).** C (800 tokens) produced 90-95% of B's quality (3000 tokens). Leading words alone activated the same structural thinking. The model already holds these character patterns from pretraining — full instruction is unnecessary.

**Finding 3: Disposition produces depth without explicit instruction.** On the ambiguous-completion task, C identified 27 issues reaching Level 4 depth (business-impact analysis, epistemic questioning of the problem framing itself). No rule told it to go that deep. The character's disposition — "find the hidden assumption" — generated thoroughness that rule-compliance could not.

**Finding 4: Checklist steering produces width without depth.** D (Anthropic's skill) found the MOST individual line-items on the security task (11 vs. 7-9 for others) but never produced trust-boundary analysis, threat models, or systemic framing. Many items ≠ understanding.

**Finding 5: Best quality-per-token is Recognition (C).** C produced superior structural analysis to D at 67% of its token cost. It produced 95% of B's quality at 27% of its token cost. The most efficient steering is a disposition pointer, not a detailed instruction set.

### 4.5 Hypothesis Evaluation

- H1 (premature completion): Partially supported. C reached Level 4 depth on the ambiguous task where its "Done when" criteria naturally prevented early stopping. A also continued probing but with shallower framing.
- H2 (C ≈ B): **Supported.** Recognition produced equivalent structural depth to full disposition.
- H3 (instruction decay): Not tested in this experiment (requires 30+ turn conversations).
- H4 (novel-situation generalization): **Supported.** B and C generated trust-boundary analysis, valid-misuse thinking, and architectural naming that no explicit rule requested. A and D did not.

---

## 5. Related Work

Role-play prompting improves reasoning [11] but exhibits belief-behavior inconsistency [4]. We extend this: disposition installation through specific acts addresses the consistency gap by providing identity rather than assignment.

Persona simulation via reinforcement [12] addresses drift through training-time intervention. Our approach is inference-time only: multi-angle installation as stability mechanism.

Instruction instability research [6] identifies attention decay and proposes split-softmax. Our approach is orthogonal: reduce instruction count by replacing rules with dispositions.

Chain-of-thought [17] adds intermediate reasoning tokens. Our framework changes the ground from which reasoning occurs. CoT increases quantity of reasoning. We change quality of reasoning.

---

## 6. Discussion

The experimental results reveal a distinction the literature has not previously named: **structural thinking vs. item-level compliance.**

Rule-based steering (A, D) produces agents that CHECK items on a list. Disposition-based steering (B, C) produces agents that SEE systems — trust boundaries, failure cascades, irreversible decisions, valid-misuse paths. These are qualitatively different outputs, not just quantitatively deeper.

This aligns with the Gita's teaching: rules tell you what to do in known situations. A disposition — a way of seeing — generates correct behavior in ANY situation, including ones no rule anticipated. The security agent operating from "what hidden trust can be abused through valid actions?" naturally finds the IDOR vulnerability, the refund-without-ownership path, and the admin-endpoint-without-role-check — because the disposition GENERATES the checking, not because a rule LISTS the checking.

The Pratyabhijna result (C ≈ B) has a practical implication: **you don't need 3000 tokens of philosophy in every context window.** You need 800 tokens of recognition pointers. The model already holds the patterns. The pointer activates them. This makes disposition-based steering CHEAPER than commercial alternatives (D) while producing DEEPER output.

---

## 7. Conclusion

The instruction decay problem is architectural: discrete rules compete for finite attention and decay individually. The answer is not stickier rules but fewer of them — ultimately one: a disposition that generates behavior rather than specifying it.

> *कर्मण्येवाधिकारस्ते मा फलेषु कदाचन।*

An agent whose ground is karma yoga + svadharma does not need N rules. It needs one disposition. The N behaviors generate themselves — including behaviors no rule anticipated.

Philosophy AI is the discipline of building that ground.

---

## References

[1] Tian, P. "When Your Agent Says Done and Means Nothing." 2026.
[2] Tian, P. "Why LLMs Follow 5 Rules Reliably but Not 15." 2026.
[3] Paddo. "Your AGENTS.md is a Liability." 2025.
[4] arxiv 2507.02197. "Do Role-Playing Agents Practice What They Preach?" 2025.
[5] arxiv 2601.04170. "Quantifying Behavioral Degradation in Multi-Agent LLM Systems." 2025.
[6] arxiv 2402.10962. "Measuring and Controlling Instruction (In)Stability in Language Model Dialogs." 2024.
[7] OpenAI. "Why Language Models Hallucinate." 2026.
[8] arxiv 2601.15703. "Agentic Uncertainty Quantification." 2025.
[9] Payne, K. "Your AI Agent Is Not Broken. It Is Running Out of Room." 2025.
[10] CodeOnGrass. "Why Your Claude Agent Ignores Rules Past ~15 Tool Calls." 2025.
[11] arxiv 2308.07702. "Better Zero-Shot Reasoning with Role-Play Prompting." 2023.
[12] arxiv 2511.00222. "Consistently Simulating Human Personas with Multi-Turn Reinforcement Learning." 2024.
[13] Scale AI. "A Guide to Improving Long Context Instruction Following." 2024.
[14] Bhagavad Gita (within Mahabharata, ~400 BCE – 400 CE).
[15] Shiva Sutras (Vasugupta, ~9th century CE).
[16] Ishvara Pratyabhijna Karika (Utpaladeva, ~10th century CE).
[17] Wei, J. et al. "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models." NeurIPS 2022.
