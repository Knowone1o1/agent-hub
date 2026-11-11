# Communication methods in the Agent Lab

The Lab compares a default (D0) against **eight methods from recent research**: four from AI research on multi-agent
LLM systems and four from management/organizational research on human teams. Every method is training-free (prompting,
message formats and router rules only, because the agents are Claude models used through Claude Code) and, where possible,
is built on D0 so that it changes **one mechanism**.

| Key | Method | Family | Source |
|---|---|---|---|
| D0 | Coordinator-led (default) | baseline | Pilot proposal |
| A1 | Inhibitory evidence routing | AI research | Siboni, Springer & Rostami (2026), arXiv:2610.24718 |
| A2 | PACT action-state messages | AI research | Huang, Wu & Zhang (2026), arXiv:2606.05304 |
| A3 | DarkForest controlled disclosure | AI research | Li, Wei, Jiang, Guo, Li & Li (2026), arXiv:2605.25188 |
| A4 | Blackboard with volunteers | AI research | Salemi et al. (2025/26), arXiv:2510.01285 |
| B1 | Adaptive hierarchy | business research | Abi-Esber, Greer & De Hoogh (2026), *Academy of Management Journal* 69(3) |
| B2 | Stand-up check-ins | business research | Ghosh & Wu (2023), *Organization Science* 34(6) |
| B3 | Quiet time with interaction windows | business research | Perlow (1999), *Administrative Science Quarterly* 44(1); Bernstein, Shore & Lazer (2018), *PNAS* 115(35) |
| B4 | Burst threads with a novelty filter | business research | Riedl & Woolley (2017), *Academy of Management Discoveries* 3(4) |

V1–V3 (relay bypass, pull information, early escalation) from the pilot proposal remain available.
*Changed 11 Nov 2026:* the earlier B2 (LLM-facilitated sharing, Alsobay et al., CSCW 2026) was replaced by stand-up check-ins;
the earlier B3 (structured advice pairs, Sandvik et al. 2020) and B4 (independent estimates then discuss, Minson et al. 2018)
were replaced because they aim to bring out each person's best contribution, not to make team communication more
efficient. The business methods now come from research on communication *efficiency* (less interruption, less repetition,
better timing).

---

## D0 · Coordinator-led (default)
Specialists report to the Coordinator; a message one specialist addresses to another is delivered to the Coordinator, who
decides whether to pass it on. The Coordinator submits the final answer.

## A1 · Inhibitory evidence routing
**Source.** Siboni, Springer & Rostami, *Neuro-Inspired Inhibitory Message Passing for Accurate and Token-Efficient
Multi-Agent Reasoning*, arXiv:2610.24718 (Oct 2026). A training-free router forwards the **original evidence passage** agents
restate instead of their paraphrase, once per receiver per round, and cancels copies receivers already hold. Reported +39 to
+77 points accuracy with 10–46% fewer tokens; putting senders' reasoning back roughly halved accuracy.
**In the Lab.** D0 topology. When a specialist cites evidence IDs, the router delivers the original evidence text (once per
receiver per round; cancelled if already held). The Coordinator also gets a one-line claim; specialists get no wording.
**Metrics.** Evidence forwarded vs cancelled, wording not forwarded, unknown IDs cited.
**Simplifications.** Declared evidence IDs replace lexical matching (the paper suggests source tags can); the one-line claim
to the coordinator is ours; five agents is below the scale of the paper's largest gains.

## A2 · PACT action-state messages
**Source.** Huang, Wu & Zhang, *What Should Agents Say? Action-state Communication for Efficient Multi-Agent Systems*,
arXiv:2606.05304 (Jun 2026). Outputs are projected to {Action, State, Result} records before entering shared history;
reasoning and restatements are dropped. Reported similar quality at about half the tokens.
**In the Lab.** D0 topology. Every message is `action` / `state` (with evidence IDs) / `result`, each one short sentence
(~25 words); only the record is delivered; agents' private notes are kept out of their history.
**Metrics.** Records sent and complete, share grounded in evidence IDs, average message length.
**Simplifications.** Projection by prompting plus a router-side strip; transfer from Qwen3 on QA/coding to Claude on
supply-chain diagnosis is untested. A first test without the length limit made A2's messages the longest of all.

## A3 · DarkForest controlled disclosure
**Source.** Li, Wei, Jiang, Guo, Li & Li, *DarkForest: Less Talk, Higher Accuracy for Multi-Agent LLMs*, arXiv:2605.25188
(May 2026). Agents answer independently with no exchange; answers are parsed into candidates, clustered, and scored into a
reliability-weighted belief state; the coordinator sees **only that summary** (never raw traces), and a deterministic
guardrail overrides it when a strong, clear majority disagrees. Reported 4.5–30.7% relative gains over the next-best method
with about 3–5× fewer tokens; withholding raw traces beat showing them.
**In the Lab.** Its own round flow:
1. *Evidence rounds:* no messages are delivered; a specialist is called only when it receives evidence and writes notes.
2. *Blind answers:* each specialist gives conclusion, plan, evidence IDs and confidence 0–1, from its own evidence only.
3. *Belief table:* answers are clustered (word overlap ≥ 0.35 = same answer) and scored s = Σ α·ρ·(0.5 + confidence) with
   α = 0.5 and ρ = 0.5 for malformed answers (no confidence, or citing evidence the agent never received); normalised to P,
   with margin = P(top) − P(second), flagged uncertain if P < 0.5 or margin < 0.2.
4. *Decision:* the Coordinator gets one call with only the belief table and must decide.
5. *Guardrail:* if ≥ 2 specialists back the top cluster, P ≥ 0.66 and margin ≥ 0.20, and the Coordinator's conclusion
   differs, the router replaces the conclusion with the top cluster's (keeping the Coordinator's plan).
**Metrics.** Blind answers, clusters, top P, margin, uncertainty flag, override, messages discarded, characters disclosed.
**Simplifications.** The paper's offline calibration (per-agent accuracies from labelled examples) is replaced by uniform
priors, which makes the score confidence-weighted voting; exact-match clustering is replaced by word overlap; 4+1 agents
instead of 3+1. Important difference: the paper's agents can each answer the whole task, while here each specialist holds
only part of the evidence, so blind answers are partial by design. That is exactly what the experiment tests.

## A4 · Blackboard with volunteers
**Source.** Salemi, Parmar, Goyal, Song, Yoon, Zamani, Pfister & Palangi, *LLM-Based Multi-Agent Blackboard System for
Information Discovery in Data Science*, arXiv:2510.01285 (2025; v2 2026). The main agent posts requests to a board every
helper sees, **without naming a recipient**; helpers decide themselves whether they can contribute and write to a response
board only the main agent reads; unanswered requests are reformulated. Reported end-to-end 31.4% vs 27.6% (master–slave)
vs 23.0% (RAG) with Claude Opus, at about 1.8× the master–slave cost.
**In the Lab.** Every Coordinator message becomes an anonymous board request (R1, R2…), shown to all specialists.
Specialists are called only when the board has something new for them and may reply only to the response board (to the
Coordinator); they can't see each other's replies or talk to each other. A request with no reply after two rounds is flagged
to the Coordinator for reformulation. The Coordinator is called every round, with at most 10 actions.
**Metrics.** Requests, replies, volunteers per request, unanswered requests.
**Simplifications.** No code/search helpers; prompts rewritten (the paper's prompt figures weren't available); synchronous rounds.

## B1 · Adaptive hierarchy
**Source.** Abi-Esber, Greer & De Hoogh, *Team Hierarchical Adaptability: Benefits for Team Coordination and Performance*,
*Academy of Management Journal* 69(3):563-592 (2026), doi:10.5465/amj.2023.1308. Teams that shift between centralized and
flat influence patterns (with the same formal hierarchy) outperform always-hierarchical or always-flat teams.
**In the Lab.** Flat rounds (rounds 1–2 and whenever evidence arrives; max two in a row): anyone may message anyone, no
decisions. Centralize rounds: positions to the Coordinator, who synthesises and decides.
**Metrics.** Flat/centralize rounds, switches, direct flat messages, hub messages, final answers held.
**Simplifications.** Switching is imposed by a rule (the paper measures it as an emergent team capability); no status dynamics.

## B2 · Stand-up check-ins
**Source.** Ghosh & Wu, *Iterative Coordination and Innovation: Prioritizing Value over Novelty*, *Organization Science*
34(6):2182-2206 (2023), doi:10.1287/orsc.2021.1499. Randomized field experiment at a Google hackathon (38 teams): after a
pre-treatment period, a mentor ran a short stand-up every 2 hours with three questions (accomplished since last check-in;
goals until the next; end-of-day goals and whether they changed), with no value judgments. Treated teams' products were rated
more valuable (+0.61 on a 1–5 scale) and less novel, driven by more integration; a pre-registered lab study replicated it.
**In the Lab.** D0, plus stand-ups in rounds 3, 5 and 7 (three in total, after two "pre-treatment" rounds). In a stand-up
everyone, Coordinator included, broadcasts one message to all: accomplished | next | end goal (changed? yes/no, why), with
evidence IDs. No requests, decisions or comments in a stand-up; a final answer waits for the next normal round.
**Metrics.** Stand-up messages, reported end-goal changes, plus cross-domain integration (all protocols).
**Simplifications.** A broadcast message replaces spoken talk; layered on a hub (the hackathon teams had none).

## B3 · Quiet time with interaction windows
**Sources.** Perlow, *The Time Famine: Toward a Sociology of Work Time*, *Administrative Science Quarterly* 44(1):57-81 (1999),
doi:10.2307/2667031: 9-month study of 17 software engineers. 95% of interactions were spontaneous although only 10% were urgent
and 86% could have waited; restricting interaction to set windows ("quiet time") raised the share of engineers rating their
productivity above average (59% and 65% in quiet-time phases vs 41% in the restricted-window phase), and the division shipped on
time. Bernstein, Shore & Lazer, *How intermittent breaks in interaction improve collective intelligence*, *PNAS* 115(35):8734-8739
(2018), doi:10.1073/pnas.1802407115: randomized experiment: groups that interacted only every third round matched isolated groups'
best solutions and constant-interaction groups' average, with a third of the interaction.
**In the Lab.** D0 with a fixed rhythm: two QUIET rounds, then one INTERACTION round (rounds 3, 6, 9…). In quiet rounds a specialist
is called only if it has new evidence or a request addressed to it; everything it sends is held. At the interaction round the router
delivers the held messages as one batch, after removing superseded versions (same recipient and topic: latest wins) and capping each
agent at 3 messages per window. Only an urgent correction (kind correction with evidence IDs) gets through during quiet time, once
per agent per cycle. The coordinator is called only in interaction rounds; a final answer waits for one.
**Metrics.** Windows, messages held vs delivered, removed (superseded or over the limit), urgent corrections, rounds from evidence
to coordinator, burstiness.
**Simplifications.** The human benefit (fewer interruptions of deep work) doesn't exist for LLM agents; what carries over is fewer
agent calls, independent work between windows and batching. The cost is latency: evidence reaches the coordinator later.

## B4 · Burst threads with a novelty filter
**Source.** Riedl & Woolley, *Teams vs. Crowds: A Field Test of the Relative Contribution of Incentives, Member Ability, and
Emergent Collaboration to Crowd-Based Problem Solving Performance*, *Academy of Management Discoveries* 3(4):382-403 (2017),
doi:10.5465/amd.2015.0097: 260 developers randomly assigned to 52 teams. Teams whose communication came in concentrated bursts
(quick replies, then silence) scored about 29% higher per standard deviation of burstiness; teams that repeated topics less scored
about 37% higher; the number of messages had no effect. (The process findings are correlational; the Lab manipulates them.)
**In the Lab.** D0, plus: a request opens a thread on its topic; the coordinator and everyone involved are called in the following
rounds until the coordinator closes it with a decision; a thread not closed within two rounds expires and late replies on it are
dropped. Agents with no open thread and no new mail are not called (silence between bursts). Novelty filter: a finding whose
evidence-ID set the recipient (or, for specialist-to-specialist messages, the coordinator) has already received is not delivered;
the sender is told in its own history (without being woken up).
**Metrics.** Threads opened/closed/expired, late replies dropped, repeated findings suppressed, burstiness (Riedl & Woolley's
B = (σ − μ)/(σ + μ) of gaps between messages, logged for every protocol).
**Simplifications.** Burstiness emerged in human teams; here it is engineered, so this is the first causal test of the idea in this
setting. Topic matching uses the agents' topic labels, and the novelty filter matches identical evidence-ID sets, so a genuinely new
finding about the same evidence can be suppressed.
---

## How to compare them
Lab → Studies → the draft **Method comparison** study runs D0 and all eight methods on the four development cases (2
repetitions = 72 runs). Run it as a feasibility study first, check rounds and costs, set limits with headroom, then create a
main study on the frozen held-out cases. Report cost per joint success with bootstrap ranges, each method's mechanism metrics,
and the simplifications above.

*How these were chosen:* literature searches (Nov 2026) for recent methods replicable with prompting and routing only.
Business practices not used: structured advice pairs (Sandvik et al., QJE 2020) and independent estimates then discussion (Minson et al., Management Science 2018) aim at individual contribution rather than efficiency; meeting-free days (Laker et al. 2022, survey), management by exception and Radner-style reporting trees lack experimental evidence. Rejected AI methods: S1-MAS (arXiv:2610.08155; needs a specific pretrained controller), QueenBee, Codebook Agent, MOC and CLSR (need
offline search, embeddings or extra models), AgentPrune and AgentDropout (trained). No recent causal evidence was found for
devil's advocates, red teams, pre-mortems, silent meetings or rotating leadership in human teams.
