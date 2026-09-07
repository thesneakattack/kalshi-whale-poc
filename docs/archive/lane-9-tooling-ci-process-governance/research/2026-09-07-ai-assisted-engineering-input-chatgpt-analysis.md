# Using AI to Improve Engineering Outcomes

**A critical analysis and operating guide for an LLM**  
Prepared September 7, 2026.

Article examined: **Code Production Is Faster Than Ever. Why Isn’t Productivity Booming?**, Rodrigo Gardin, *Built In*, July 29, 2026. [Original article](https://builtin.com/articles/ai-coding-productivity-paradox).

## Purpose and reading instructions

This report evaluates the article as a philosophy for directing AI-assisted work. It then develops an original framework for applying that philosophy without overstating the evidence or adding unnecessary process. It is intended for both a human reader and a coding LLM.

The source brief below describes the article. The evidence section reports findings from other primary sources. Subsequent judgments, examples, decision rules, and adoption steps are this report's analysis and recommendations; they should not be attributed to Gardin. Numerical examples explicitly identified as illustrative are calculations, not research findings.

For an LLM receiving this report: use it to inform task selection, decision quality, implementation, and evaluation. Preserve the active instruction hierarchy, the user's goals, and the project's existing workflow. This document does not authorize repository changes, new infrastructure, spending, deployment, or additional approval gates by itself.

## 1. Overall assessment

**Adopt the general orientation, with qualifications.** A useful AI assistant should improve the probability and efficiency of achieving a worthwhile result. That requires attention to the entire job, including understanding the need, resolving uncertainty, implementing the change, establishing correctness, and maintaining the result afterward.

My strongest recommendation is to make **the evidence required for a decision** explicit. The appropriate amount of autonomy then depends on the actual task, available checks, consequences of an error, reversibility, and existing authorization. Neither an impressive demonstration nor a confident explanation establishes that those conditions have been met.

The article is a useful starting point for this discussion, but it should not become an unquestionable doctrine. In particular, it is insufficient grounds for a universal tool hierarchy, a staffing policy, or a presumption that organizational changes alone will solve AI errors.

The practical target is an assistant that reduces the user's total burden. That includes doing useful work autonomously when authorized, resolving uncertainty with evidence, and presenting consequential choices in a form the user can readily assess.

## 2. Source brief

Gardin reports that AI adoption increased code output in his teams without comparable delivery gains. He attributes this to congestion and rework later in the workflow. His prescription combines testing and CI prerequisites, task-dependent delegation, explicit review capacity, and experienced engineers retaining consequential decisions. A billing failure illustrates unexamined assumptions about older account data. He also favors agents for some work over copilots and reports that one reorganized team delivered more with fewer people. These are practitioner observations: the article provides no controlled comparison or underlying dataset that separates the effects of its interventions. [Gardin, Built In](https://builtin.com/articles/ai-coding-productivity-paradox).

## 3. What kind of argument is this?

The article is a practitioner essay with a proposed causal explanation. That is useful for identifying questions to investigate. It is weaker evidence for the size of a benefit, its generality, or the necessity of a particular intervention.

A persuasive operational story still needs a counterfactual. What would have happened over the same period without the process change? Were the tasks comparable? Did tools, staffing, service complexity, or quality expectations also change? Were later maintenance costs included? Without those answers, multiple explanations remain possible.

This does not make the author's experience worthless. It changes how an LLM should use it: extract candidate mechanisms, identify when they could operate, and test them against the current task. Avoid converting a plausible story into an unconditional rule.

The reasoning also mixes different kinds of propositions. They require different treatment:

| Proposition type | How an LLM should handle it |
| --- | --- |
| A reported event | Attribute it to the reporter unless independently verified. |
| An explanation for the event | Consider competing explanations and missing observations. |
| A proposed workflow rule | Ask which failure it prevents, its cost, and its scope. |
| A numerical productivity claim | Preserve the denominator, time period, population, and uncertainty. |
| A forecast | Treat it as a forecast and identify what would support or contradict it. |
| A statement of priorities | Check that the priorities match the user's actual objective. |

These distinctions are especially important when the report is passed to another LLM. Otherwise, the receiving model may convert recommendations into facts and facts from one setting into rules for every setting.

## 4. What the external evidence establishes

The following sources were checked for this report. They provide complementary evidence, with different methods and limitations. They are not directly comparable estimates of a single universal AI productivity effect.

| Primary source | Finding and scope | Appropriate interpretation |
| --- | --- | --- |
| Cui and colleagues, three company field experiments; Microsoft Research summary, June 2025 | Randomized access to a coding assistant across 4,867 developers produced a pooled estimate of 26.08% more completed tasks, with reported standard error of 10.3 percentage points. Less experienced developers had greater gains and higher adoption. [Research summary](https://www.microsoft.com/en-us/research/publication/the-effects-of-generative-ai-on-high-skilled-work-evidence-from-three-field-experiments-with-software-developers/) | Meaningful improvement is possible in ordinary company work. Task completion is not a direct measurement of customer value, lifecycle cost, or a universal seniority effect. |
| METR, July 10, 2025 | An experiment involving 16 experienced open-source developers and 246 issues found 19% longer completion times with early-2025 tools. Participants nevertheless believed AI had helped their speed. This was work in repositories the developers knew well. [Study](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) | Subjective impressions can diverge from measured performance. The result concerns a particular population, task distribution, and generation of tools. |
| METR, February 24, 2026 | Later observations suggested possible improvement, but the researchers identified serious participant and task selection effects, along with difficulties measuring concurrent agent work. They explicitly described the magnitude estimate as unreliable. [Update](https://metr.org/blog/2026-02-24-uplift-update/) | The earlier slowdown should not be frozen into a claim about current tools. The update also does not establish a precise replacement estimate. |
| METR, May 11, 2026 | A convenience sample of 349 technical workers reported median value gains of 1.4–2 times across different questions, and a median speed gain of 3 times. These were self-reports, with selection and calibration concerns. [Survey](https://metr.org/blog/2026-05-11-ai-usage-survey/) | Speed and value are different quantities. Contemporary enthusiasm is useful evidence about perceived benefit, but these figures are not experimentally measured productivity multipliers. |
| DORA, March 10, 2026 | DORA describes an association between AI adoption and both higher delivery throughput and greater instability. Its accompanying qualitative analysis examined 1,110 open-ended responses from Google engineers and identified benefits alongside verification overhead. [DORA analysis](https://dora.dev/insights/balancing-ai-tensions/) | The mechanism is consistent with workflow friction. Associations and reported experiences do not identify the causal effect of a particular model or workflow rule. |

**My synthesis:** the evidence supports conditional usefulness and local measurement. It does not support either inevitable acceleration or inevitable slowdown. A good operating philosophy must remain useful when the model improves, the task changes, and the main source of wasted effort moves.

Do not average the headline percentages in this table. Completion time, task throughput, perceived value, and delivery associations use different denominators and methods. Likewise, a finding about developer experience in one study cannot determine who should own a consequential decision in another organization.

## 5. A more precise model of productivity

### 5.1 Separate elapsed time, human effort, and value

Three outcomes can move independently:

- **Elapsed time:** how long the user waits for the result.
- **Human effort:** the total attention spent specifying, supervising, reviewing, correcting, and integrating the work.
- **Value:** whether the result meaningfully advances the user's objective, including quality and future costs.

For example, an agent may finish overnight while consuming little human attention, even if its wall-clock execution is slow. Another may respond instantly but require an hour of correction. A third may generate a polished feature that nobody needed. These are different outcomes and should not receive the same productivity label.

Measure tool and compute spending separately from hours unless a deliberate monetary conversion is being used. Avoid equations that add dollars, hours, defect counts, and business value as though they share a unit.

### 5.2 A local speedup has a bounded effect

**Illustrative calculation, not an empirical estimate:** suppose code writing accounts for 30% of a sequential delivery cycle and all other work accounts for 70%. Making writing three times as fast changes total time to:

\[
T_{new}/T_{old}=0.70+0.30/3=0.80
\]

The cycle is 20% shorter, equivalent to 25% higher throughput under the simplified assumption of repeated identical work and unchanged capacity. Even instantaneous code writing would leave 70% of the original cycle.

The assumptions matter: the steps are sequential, the work is comparable, and the faster method introduces no extra supervision or rework. The purpose is to show why a spectacular improvement in one activity need not produce a comparable improvement in the whole job.

### 5.3 Capacity and queues impose a second constraint

**Another illustrative example:** generation initially produces three comparable changes per day, while validation can complete four. If generation doubles to six and validation remains at four, sustained completion cannot exceed four. In this simplified system, the waiting queue grows by two changes per day.

The appropriate response depends on what is waiting. Useful interventions might include reducing unnecessary changes, making evidence easier to inspect, improving a slow test, resolving a recurring requirement ambiguity, or limiting concurrent work. Hiring reviewers or adding agents is only one possible response.

An assistant should therefore ask internally: **Which unfinished condition is preventing this task from becoming a useful result?** It should spend effort on that condition. More implementation is useful only if implementation is the limiting condition.

### 5.4 Include the work AI makes newly possible

A time-saving calculation alone can undervalue exploration that would previously have been unaffordable. A small prototype may cheaply eliminate a bad design. A migration rehearsal may reveal a costly compatibility problem. An analysis may enable a decision that previously lacked evidence.

The corresponding trap is doing extra work merely because it is inexpensive to generate. Every new artifact still consumes attention and can create maintenance obligations. Before expanding scope, identify the concrete decision or user outcome the expansion would improve.

### 5.5 Keep the underlying assumptions revisable

The recommended approach depends on conditions that an LLM should examine rather than assume:

| Working assumption | What to do when it does not hold |
| --- | --- |
| The requested outcome is sufficiently clear. | Investigate the need or resolve the consequential ambiguity before committing to a solution. |
| The acceptance condition represents the real goal. | Check whether the proxy can succeed while the user still receives a poor result. |
| The relevant errors can be detected at reasonable cost. | Improve observability, narrow the change, rehearse it, or qualify the result. |
| Reviewing generated work is cheaper than rebuilding it. | Compare methods; a direct implementation may be the better choice for that task. |
| The work can be divided without losing important interactions. | Keep coupled decisions together and validate the complete behavior. |
| Someone has the knowledge and capacity to assess consequential choices. | Make the knowledge gap explicit; more output cannot supply missing decision authority or expertise. |

The philosophy must also be falsifiable in practice. If a new instruction increases interruption and review effort without improving outcomes, revise or remove it. If a newer tool reliably handles a previously difficult task with less supervision, reconsider the old restriction within the applicable authority boundaries. A method that interprets every failure as a reason for more of the same process cannot learn.

## 6. Critical qualifications

### 6.1 Outcome measures still need interpretation

A successfully deployed change can be unnecessary, confusing, difficult to maintain, or harmful to an important user group. A project can also improve substantially through deletion, simplification, or a decision to stop an unpromising feature.

Define the intended effect before choosing a measure. For a reliability fix, the useful effect might be eliminating a reproducible failure. For a UI task, it might be successful completion of a user action. For exploration, it might be answering a specific feasibility question.

Counting deployments or completed tickets can help describe flow, but cannot replace that definition. Prefer a small set of measures that expose tradeoffs rather than a single score that conceals them.

### 6.2 Tool categories are not a maturity ladder

Choose a tool by what the task requires: local editing, repository navigation, execution, external retrieval, or controlled interaction with a running system. A simple completion tool may be entirely adequate. A repository agent can be appropriate when success requires coordinated edits and feedback from execution.

Greater autonomy can also introduce more actions to inspect and more opportunities for a wrong assumption to propagate. The relevant comparison is between complete methods on comparable tasks, including supervision and correction. A numerical ceiling associated with a tool category should not be adopted without a documented benchmark and a relevant task distribution.

### 6.3 Complexity is only one dimension of delegation

A sophisticated transformation over a disposable dataset may have an excellent validation oracle and be easy to undo. A one-line change to authorization behavior may be difficult to validate and have serious consequences.

The delegation decision should account for ambiguity, observability, reversibility, blast radius, and authority. These factors can justify substantial independent work on a technically complex task, or stronger validation for a seemingly simple one.

Also separate preparation from execution. Investigating a migration, editing a local draft, opening a reviewable proposal, merging a change, and executing it against production have different effects. They should not inherit one undifferentiated permission requirement.

### 6.4 Coverage is a clue about execution, not a complete oracle

A test can execute every changed line while checking the wrong expected result. A fixture can represent only the convenient subset of valid data. A mock can hide the integration behavior that matters.

Ask what assertion establishes the required behavior, where its expectation comes from, and which plausible counterexample would falsify it. Useful techniques may include contract checks, targeted integration tests, representative fixtures, invariant checks, or a regression case based on an observed failure.

Do not make complete modernization of a legacy module a universal prerequisite for a bounded improvement. Establish the evidence needed for the current change and strengthen the relevant boundary. Nor should trivial, reversible edits accumulate tests that merely restate the implementation.

### 6.5 Human expertise is valuable, but job title is not evidence

The positive field experiments in Section 4 complicate any simple assumption that benefit rises uniformly with seniority. More fundamentally, speed of task completion and ability to authorize a consequential decision are different questions.

Look for relevant domain knowledge, understanding of system invariants, and ability to assess the evidence. An experienced engineer working outside their domain may still miss a critical assumption. A less experienced contributor can do valuable work when the task and feedback are well designed.

For a solo developer, the practical adaptation is to make review easier: coherent changes, short explanations, direct evidence, and repeatable checks. A second model pass can identify issues, but it is not equivalent to another accountable human and does not guarantee independent errors.

The model should still contribute substantive judgment: identify contradictions, investigate alternatives, challenge assumptions, and make a supported recommendation. Human decision rights determine which commitments require a human. They need not prevent the assistant from doing the analytical work that makes those commitments better informed.

### 6.6 Responsibility and technical cause must remain distinct

Human accountability for a deployed system does not prove that the model contributed nothing to a failure. Conversely, identifying a model error does not explain why the system accepted it.

Investigate both. Did the model invent an API, overlook a requirement, or fail to inspect relevant code? Did the specification omit a valid case? Did the environment misrepresent the data? Did review rely on a misleading test result?

Different causes call for different responses. Some require better task context or a different model; others require a stronger test, an explicit invariant, or a change in release controls. Assigning all errors to either people or tools prevents useful diagnosis.

### 6.7 Verification can itself become wasteful

Extra review does not automatically improve evidence. Several reviewers may inherit the same mistaken specification. Repeated broad test runs may establish nothing about the unresolved risk. Long checklists may obscure the one condition that actually matters.

Design review around a concrete question. If the issue is retry behavior, inspect and exercise retries. If it is schema compatibility, examine consumers and old records. Stop optional checks when the relevant question is answered and required project gates are satisfied.

This is an argument for proportionate rigor. It is also an argument against an assistant that treats asking the user, adding paperwork, or spawning more reviewers as inherently responsible behavior.

### 6.8 Staffing claims require a longer observation window

An improvement in short-term throughput cannot by itself establish sustainable staffing needs. Assess on-call coverage, review concentration, maintenance work, knowledge transfer, resilience during absences, and the ability to handle future changes.

It is reasonable to redirect time that is no longer needed for repetitive work. It is premature to infer a generally appropriate headcount policy from an isolated operational account. An LLM should keep workforce conclusions separate from task-level engineering recommendations.

## 7. The operating philosophy I recommend

The following principles are this report's proposed adaptation. They are decision criteria, not a mandatory sequence of meetings or documents.

| Principle | Why it matters | Concrete LLM behavior |
| --- | --- | --- |
| Start from the intended effect | A precise implementation can still solve the wrong problem. | Identify the user's desired result and the acceptance condition before expanding the solution. |
| Find the limiting uncertainty or activity | Effort has different value at different points in a task. | Determine whether progress needs better requirements, evidence, implementation, testing, or integration. |
| Reduce total human burden | Fast drafts can shift work onto the user. | Include context gathering, self-correction, validation, and a concise handoff in the job. |
| Match autonomy to evidence and consequences | Easy syntax does not imply low risk. | Consider reversibility, blast radius, available checks, and existing permission independently of technical difficulty. |
| Use the environment as a source of truth | General knowledge can miss project-specific constraints. | Inspect relevant code, configuration, contracts, and current documentation; keep assumptions visible. |
| Establish expectations independently | Implementation and its tests can share the same mistake. | Derive expected behavior from the requirement, a contract, an invariant, or an independently checked example. |
| Keep changes coherent | Review depends on understanding intent and interactions. | Produce the smallest complete change with a clear rationale; avoid unrelated cleanup. |
| Make uncertainty actionable | Vague caution does not help the user decide. | State what is unknown, why it matters, and the smallest useful way to resolve it. |
| Preserve authority boundaries | Capability is not authorization. | Carry out authorized preparation; stop only at the action or decision that actually needs further authority or input. |
| Learn from measured outcomes | Both tools and workflows change. | Update task-specific practices when real evidence shows a benefit, regression, or recurring failure. |
| Preserve the user's understanding | An opaque system becomes expensive to own. | Explain important decisions and leave usable evidence without narrating every internal step. |
| Charge process against its benefit | Rules and automation require maintenance too. | Add a persistent mechanism only when it addresses a demonstrated need and has a clear owner. |

### 7.1 Replace an autonomy score with explicit conditions

There is no validated universal numerical formula in this report for granting autonomy. Use the following dimensions to make the actual decision clearer:

| Dimension | Question | Effect on the work |
| --- | --- | --- |
| Goal clarity | Is the required behavior sufficiently specified? | Resolve consequential ambiguity; use reasonable defaults for routine details. |
| Context quality | Are relevant constraints and dependencies accessible? | Inspect missing context before relying on an assumption. |
| Verification quality | Is there a credible way to recognize a wrong result? | Improve the check, narrow the claim, or keep the output provisional. |
| Consequences | What could be affected by an error? | Focus scrutiny on the affected boundary and failure modes. |
| Reversibility | Can the state be restored, including external effects? | Prefer a rehearsal or isolated change when reversal is uncertain. |
| Authority | Has this action or decision already been authorized? | Proceed within that scope; do not infer broader permission from tool access. |

These conditions apply to an action, not permanently to a model or developer. A model can independently investigate a sensitive subsystem while lacking permission to alter live behavior. A task can also move from uncertain to well understood after a small experiment.

### 7.2 Suggested handling by situation

| Situation | Work the assistant should complete | Where human input may be necessary |
| --- | --- | --- |
| Clear, reversible local change | Inspect the relevant context, implement it, and perform a focused check. | Only if an actual ambiguity or authority boundary remains. |
| Change spanning several components | Trace dependencies, identify the contract, implement a coherent slice, and check integration behavior. | An unresolved product choice or an architectural commitment outside the request. |
| Change with substantial data, access, or availability consequences | Prepare the design, inspect edge conditions, rehearse where appropriate, and produce concrete validation and recovery evidence. | The consequential action if it has not been authorized, or a tradeoff the assistant cannot responsibly settle from the available facts. |
| Exploratory idea with uncertain value | Conduct a bounded investigation or prototype aimed at a specific question. | Whether to commit further resources when the answer requires a user priority decision. |
| Required environment or evidence unavailable | Complete independent work, identify the exact missing condition, and leave a reviewable result. | Access, information, or a decision that is genuinely required to continue. |

The table does not impose new review counts or permissions. Existing project rules remain in force. Risk should change the relevant evidence and execution method; it should not automatically produce a new permission request.

## 8. How to change an LLM's behavior in practice

### 8.1 At task intake

Form a short task understanding: desired effect, relevant constraints, known authorization, and what would demonstrate success. For a simple request, this may remain a few internal checks followed by action. For a complicated task, a concise plan can expose important choices.

Inspect only the context needed to resolve those choices. Search broadly when the task demands it, but avoid treating every small edit as an invitation to inventory the entire repository.

Ask a question when different answers would materially change the result and the missing information cannot reasonably be recovered. Otherwise, use the available evidence and proceed. If a question is necessary, finish unrelated authorized work first when doing so remains useful.

### 8.2 During investigation and implementation

Convert consequential assumptions into explicit questions. Examples include whether old records conform to the current schema, whether retries can duplicate side effects, and whether a caller relies on a response field that appears unused locally.

Use focused experiments to answer questions before increasing implementation scope. A failing example, a small trace of the call path, or a contract check can resolve more uncertainty than a long speculative architecture document.

Keep related behavior together so the result is understandable and functional. Smallness is not a line-count target. Splitting a necessary interaction across several incomplete changes can make assessment harder. Conversely, including formatting churn and unrelated refactors in a behavioral fix can conceal the important change.

When parallel work is available and authorized, use it only for sufficiently independent subtasks with clear outputs and a defined integration owner. Count the time needed to reconcile findings and validate the integrated result. Several agreeing agents do not constitute several independent sources of truth.

### 8.3 During verification

Check the result against the intended behavior, not just the implementation's own explanation. Determine where the expected result came from. If the test's expected value was copied from the current code, it may only confirm self-consistency.

Choose representative edge cases from the actual boundary being changed. Depending on the task, relevant dimensions might include missing fields, duplicate requests, ordering, limits, old versions, time transitions, partial failures, and concurrency. This is a menu of possibilities, not a requirement to test every category for every edit.

Keep checks tied to the final relevant version and environment. If a correction changes the behavior under test, earlier results may no longer establish it. Report which tests ran, what they establish, and any material gap. Do not convert a test command that was suggested into a claim that it passed.

Separate an unavailable check from a failed check. Both limit confidence, but they require different next steps. A missing environment is not evidence of a product defect; an observed failing requirement should not be hidden under a generic environment caveat.

### 8.4 At handoff

Return the result in a form that supports a decision. The amount of explanation should track the significance of the change.

For a substantial coding task, a useful handoff answers five questions:

1. What behavior changed, and why?
2. What evidence supports the result?
3. What material uncertainty remains?
4. What is the integration or execution status?
5. Does the user actually need to decide or do anything next?

For a trivial edit, one or two sentences may cover everything. Avoid a ceremonial template when it adds more reading than information.

## 9. Concrete examples

These are invented scenarios that illustrate the recommended behavior. They are not incidents from the article or findings about the user's repositories.

### Example A: Adding pagination to an API

The implementation question is larger than adding a limit parameter. Relevant questions could include ordering, ties, empty pages, maximum page size, concurrent inserts, and existing client expectations. Which ones matter depends on the endpoint's contract.

A useful assistant first checks the existing convention and consumers, then implements the requested behavior and verifies representative boundaries. It does not invent a new pagination framework if the application already has an adequate one. It also does not require an architectural approval solely because the change is in an API.

**Decision principle:** ordinary feature labels do not substitute for understanding the affected contract.

### Example B: Fixing retry behavior for a webhook

Suppose an external sender may deliver the same event more than once. A successful happy-path test is insufficient to establish the intended result if duplicate processing would create an unwanted side effect.

The assistant should trace event identity, persistence, failure ordering, and retry handling, then exercise the relevant duplicate or interruption case. The expected behavior should follow the business rule for processing an event once, including what happens after a partial failure.

If the assistant has authority only to prepare a change, it completes the implementation and evidence within that scope. It does not infer permission to replay real events into a live service.

**Decision principle:** test the assumption whose failure would change the outcome.

### Example C: Editing explanatory text

For a reversible text change, useful validation may be a diff inspection and checking that links or formatting still work. Adding a new test suite, review role, or process document would usually have little value unless the edit affects machine-consumed content or a required project gate applies.

**Decision principle:** rigor means choosing an appropriate check, not maximizing the amount of checking.

### Example D: Several agents propose the same refactor

Agreement can be useful for identifying a candidate, but it does not establish compatibility. All agents may have received the same incomplete context or favored the same familiar pattern.

The integrating assistant should inspect actual callers, compare the proposal with the acceptance conditions, and test a case that could disprove it. If coordination and reconciliation cost more than a focused investigation, use fewer parallel tasks.

**Decision principle:** independent evidence matters more than the appearance of consensus.

### Example E: A user requests an ambitious architecture

The assistant should identify which observed or anticipated requirement justifies each major component. A prototype or measurement may resolve the question. Where the requirement is real, implement or propose the necessary design within scope; where it is unestablished, label the assumption and avoid presenting speculative capacity as proven.

**Decision principle:** use AI to improve the quality of the commitment before increasing its size.

## 10. Fit the guidance into an existing coding workflow

This philosophy should influence the decisions made inside the project's workflow. It should not silently replace that workflow or establish a competing sequence of gates.

For a project using Claude, Codex, Superpowers, or similar instructions, first read the currently applicable rules. Do not assume a particular version or claim that a generic report documents the installed system. Put each durable rule in its existing authoritative location when a change is actually requested.

| Concern | Appropriate home, if needed | Example |
| --- | --- | --- |
| General assistant behavior | Existing instruction file, such as `CLAUDE.md` or `AGENTS.md` | State material assumptions; complete authorized work; report evidence accurately. |
| Durable domain requirement | Existing specification, contract, or domain documentation | An event must not create its side effect twice. |
| Mechanically checkable invariant | Existing tests or validation tooling | A regression case exercises duplicate delivery. |
| Required integration condition | Existing CI or repository control | The relevant check must succeed before merge. |
| A particular task's design choice | Existing plan, issue, or review description | Why this change uses the established API convention. |
| Repeated operational procedure | Existing runbook | How to diagnose and recover a known failure. |

**When is an instruction enough?** When the desired change is primarily about behavior: avoiding irrelevant scope expansion, communicating uncertainty, choosing focused verification, or respecting existing authorization.

**When is executable support warranted?** When a recurring requirement can and should be checked mechanically, or when a process depends on a persistent capability that prose cannot supply. A sentence asking the model to preserve compatibility is useful guidance; a suitable contract test can provide additional evidence that it did so.

Do not create a new tracker, orchestrator, hook, state machine, or document hierarchy merely to embody this philosophy. Identify the missing capability and the observed cost first. Extend the existing mechanism where feasible. Retire superseded rules so multiple sources do not diverge.

This is also why the companion instruction file is deliberately shorter than the analysis. The report explains the reasoning; the instructions express the recurring decisions. Loading the entire report into every task would add context cost without necessarily improving behavior.

## 11. Measure whether the approach helps

Start with information already available. The following table distinguishes standard delivery measures from additional local measures proposed by this report.

| Measure | Definition or practical use |
| --- | --- |
| Change lead time | Commit to production deployment. |
| Deployment frequency | Deployments per period, or interval between deployments. |
| Failed deployment recovery time | Time to recover from a deployment failure requiring immediate intervention. |
| Change fail rate | Fraction of deployments requiring immediate intervention. |
| Deployment rework rate | Fraction of deployments that are unplanned responses to production incidents. |

These are DORA's current five delivery measures as described in its January 5, 2026 guide. They describe delivery performance, not the whole of product value. [DORA definitions](https://dora.dev/guides/dora-metrics/).

Add only the local measures needed to answer the current question:

| Local question | Proposed measure | Interpretation caution |
| --- | --- | --- |
| Is the user waiting less? | Request-to-accepted-result time, with a consistent start and end. | Distinguish planned pauses and external dependencies from active work. |
| Is AI saving attention? | Human minutes spent specifying, supervising, reviewing, correcting, and integrating. | Do not count overlapping effort twice or treat agent runtime as human labor. |
| Is review congested? | Waiting time before review, active review time, and age of unfinished changes. | A complex change and a trivial one are not interchangeable units. |
| Are apparent gains surviving later use? | Reopened work, attributable corrective work, and relevant incidents over a chosen follow-up period. | Use severity and exposure; a short period with no observed failures is weak evidence. |
| Is the result useful? | The acceptance condition or product effect established before implementation. | Avoid substituting artifact count for the intended benefit. |
| Is the operating model economical? | Tool spend and CI usage, alongside human effort and useful results. | Keep units explicit; estimates should remain labeled as estimates. |

A solo developer can begin with a short note per meaningful task. A team can often reuse timestamps and review records from its existing tools. Build measurement infrastructure only if it enables decisions worth its cost.

### A practical evaluation sequence

1. **Define one improvement question.** For example: does requiring a concise evidence summary reduce review effort for bug fixes?
2. **Establish a relevant baseline.** Use comparable tasks in the same project. Record the tool, relevant environment, and measurement definitions.
3. **Change one important practice where feasible.** Otherwise acknowledge that attribution will be weaker.
4. **Observe the full job.** Include corrections and integration, not only the first draft.
5. **Examine variation.** Separate task classes and explain unusual cases. Do not discard unsuccessful AI attempts from the accounting.
6. **Keep, adapt, or remove the practice.** Base the choice on benefit, quality, and effort. Repeat after a material tool or task change.

A before-and-after comparison is a practical diagnostic, not necessarily a causal experiment. Task selection, learning, staffing, and concurrent changes can explain part of the difference. Where volume permits, a controlled comparison of comparable work can strengthen the conclusion. Do not claim statistical certainty from a handful of heterogeneous tasks.

## 12. Transfer beyond coding

The framework can inform other AI-assisted work, but software-specific controls do not transfer automatically. Choose an acceptance condition appropriate to the deliverable.

| Work | Useful contribution from AI | Appropriate evidence |
| --- | --- | --- |
| Research synthesis | Find relevant material, reconcile claims, and identify unanswered questions. | Traceable sources, correct dates, and explicit separation of observation from inference. |
| Writing | Develop and revise a draft for a defined audience and purpose. | Factual accuracy, fit to the brief, and the user's intended voice. |
| Data analysis | Prepare data, calculate results, explore explanations, and produce clear outputs. | Provenance, reproducible calculations, consistent definitions, and checks against plausible alternative explanations. |
| Planning | Explore options, dependencies, and resource tradeoffs. | Explicit assumptions, realistic constraints, and a clear connection to the goal. |

A coding test suite cannot determine whether a research conclusion is well supported or a strategic objective is desirable. The transferable habit is to ask what would justify confidence in this particular result.

## 13. How to use the companion instructions

The companion file, `ai-engineering-llm-instructions.md`, is an original operational adaptation. It contains no claim that reading it retrains a model or permanently changes future sessions.

For a one-off task, attach it as guidance together with the actual task and project context. For a recurring workflow, adapt the relevant portions into the instruction mechanism the environment already supports, with the user's authorization. Avoid duplicating the same rules across several files.

Check the effect on real tasks. An assistant that follows the wording but creates more unnecessary work has missed the intended objective. The behavior to look for is concrete: useful independent progress, justified claims, fewer avoidable corrections, and decisions that are easier for the user to make.

### Behavioral evaluation scenarios

| Scenario | Behavior that would indicate useful adoption |
| --- | --- |
| A small authorized edit has an obvious interpretation. | The assistant completes it and performs an appropriate check without inventing an approval step. |
| Existing tests pass, but a plausible valid input is unrepresented. | The assistant investigates the relevant assumption and adds or runs a targeted check when warranted. |
| A consequential action is outside current authorization. | The assistant prepares a concrete proposal and evidence, then identifies the exact action needing authorization. |
| A user asks for an unnecessary component. | The assistant explains the tradeoff and offers a simpler way to meet the goal, while respecting the user's final direction. |
| A check cannot be run. | The assistant identifies the gap accurately and avoids claiming the result is verified. |
| Several checks repeat the same evidence. | The assistant stops optional repetition once the relevant uncertainty and required gates are resolved. |
| A model or workflow update changes performance. | The assistant revises its task-specific assumptions instead of defending a permanent productivity belief. |

These scenarios are a suggested evaluation aid. They have not been run as experiments on a receiving LLM, and this report does not claim that the companion instructions have demonstrated effectiveness.
