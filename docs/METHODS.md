# Communication methods in the Agent Lab

The Lab compares a default against four methods taken from recent research. **Every method is built on D0** (the
coordinator-led default) and changes **one mechanism**, so differences in a study can be traced to that change.
All four are training-free: they work through prompting, message formats and router rules, because the agents are Claude
models used through Claude Code (no fine-tuning or access to model internals).

| Key | Method | Family | Source |
|---|---|---|---|
| D0 | Coordinator-led (default) | baseline | Pilot proposal |
| A1 | Inhibitory evidence routing | AI research | Siboni, Springer & Rostami (2026), arXiv:2610.24718 |
| A2 | PACT action-state messages | AI research | Huang, Wu & Zhang (2026), arXiv:2606.05304 |
| B1 | Adaptive hierarchy | business research | Abi-Esber, Greer & De Hoogh (2026), *Academy of Management Journal* 69(3) |
| B2 | Facilitated information sharing | business research | Alsobay, Rothschild, Hofman & Goldstein (2026), ACM CSCW 2026 |

V1–V3 (relay bypass, pull information, early escalation) from the pilot proposal remain available.

---

## D0 · Coordinator-led (default)
Specialists report to the Coordinator; a message one specialist addresses to another is delivered to the Coordinator,
who decides whether to pass it on. The Coordinator submits the final answer.

## A1 · Inhibitory evidence routing (AI research)
**Paper.** Siboni, Springer & Rostami, *Neuro-Inspired Inhibitory Message Passing for Accurate and Token-Efficient
Multi-Agent Reasoning*, arXiv:2610.24718 (Oct 2026). https://arxiv.org/abs/2610.24718
A training-free router finds the original evidence agents restate, forwards the **original passage** instead of the
sender's paraphrase, at most **once per receiver per round**, and **cancels copies** to receivers that already hold it
(the "inhibition"); senders' reasoning is dropped. Reported: +39 to +77 points accuracy over standard message passing with
10–46% fewer tokens, across 4–64 agents and several topologies; adding senders' reasoning back roughly halved accuracy.

**In the Lab.** D0 topology. When a specialist sends a message citing evidence IDs, the router delivers the original
evidence text for each cited ID (once per receiver per round; cancelled if the receiver already holds it). The Coordinator
also receives a one-line claim (first ~30 words) so it can synthesise; messages between specialists carry no wording.
Coordinator instructions are delivered normally.
**Metrics:** evidence forwarded vs cancelled (cancellation ratio), wording not forwarded, unknown evidence IDs cited.
**Simplifications:** declared evidence IDs replace the paper's lexical matching (the paper itself suggests source tags
can do this); the one-line claim to the coordinator is our addition (the paper's lead role is not fully specified for
synthesis tasks); a forwarded item can be re-sent in a later round, as in the paper; corrections can't retract an item
already delivered (a limitation the paper also lists). Five agents is below the scale where the paper's gains were largest.

## A2 · PACT action-state messages (AI research)
**Paper.** Huang, Wu & Zhang, *What Should Agents Say? Action-state Communication for Efficient Multi-Agent Systems*,
arXiv:2606.05304 (Jun 2026). https://arxiv.org/abs/2606.05304
Each agent output is projected into an {Action, State, Result} record before it enters shared history; reasoning traces and
restatements are dropped. Reported: comparable quality at about half the tokens (e.g. HotpotQA F1 69.9 at 6.7k tokens vs
debate 69.6 at 13.5k; SWE-agent input tokens −50% with resolve rate 25.6% → 24.2%); dropping Action or State cost ~5 F1.

**In the Lab.** D0 topology. Agents must write each message as `action` (what I did / what you should do), `state`
(observed facts with evidence IDs) and `result` (finding). The router delivers only `ACTION | STATE | RESULT`; agents'
private notes are kept out of their own history.
Each field is limited to one short sentence (~25 words): in a first test without the limit, agents kept the structure but
wrote long fields, making A2's messages the longest of all protocols, which defeats the method's purpose.
**Metrics:** records sent and complete (all 3 fields), share grounded in evidence IDs, average message length.
**Simplifications:** the projection is done by prompting the sender plus a router-side strip (the paper's exact projection
mechanism differs by setting); results came from Qwen3 models on QA/coding, so transfer to Claude and supply-chain diagnosis
is untested. Expect token savings rather than accuracy gains.

## B1 · Adaptive hierarchy (business research)
**Paper.** Abi-Esber, Greer & De Hoogh, *Team Hierarchical Adaptability: Benefits for Team Coordination and Performance*,
*Academy of Management Journal* 69(3):563-592 (2026). doi:10.5465/amj.2023.1308
Five studies (field data from intact teams and a lab experiment): teams that repeatedly shift between centralized and flat
influence patterns, while the formal hierarchy stays the same, coordinate and perform better than always-hierarchical or
always-flat teams.

**In the Lab.** The Coordinator stays the formal lead. Each round has a mode:
- **Flat** (rounds 1–2 and any round in which new evidence arrives): anyone may message anyone directly; `decision`
  messages are blocked and a final answer is held until the next centralize round.
- **Centralize** (otherwise, and after at most two flat rounds in a row): specialists send the Coordinator one position
  (hypothesis, evidence IDs, confidence, open questions); messages between specialists go via the Coordinator; the
  Coordinator synthesises, decides and may submit the final answer. Every agent acts in the first round after a switch.
**Metrics:** flat/centralize rounds, mode switches, direct messages in flat rounds, hub messages, final answers held.
**Simplifications:** the paper measures an emergent capability of human teams; here the switching is imposed by an
event-driven rule, so this tests *scheduled* switching. LLM agents have no status or deference, which hierarchy shapes in people.

## B2 · Facilitated information sharing (business research)
**Paper.** Alsobay, Rothschild, Hofman & Goldstein, *Bringing Everyone to the Table: An Experimental Study of
LLM-Facilitated Group Decision Making*, ACM CSCW 2026 (arXiv:2508.08242). https://arxiv.org/abs/2508.08242
Preregistered RCT, 1,475 people in 281 groups, hidden-profile task: an LLM facilitator increased information sharing,
mainly by raising the least-engaged members' participation; neither human nor AI facilitation significantly changed the
final decision.

**In the Lab.** D0 topology plus a rule-based Facilitator:
- **Each round:** it nudges the specialist that has shared the smallest share of its private evidence (max two nudges
  per agent), naming the unshared evidence IDs.
- **Before the decision (once):** if any evidence was never shared, or was shared but not cited in the final answer, the
  answer is held; the Coordinator must address each item and resubmit, and holders of unshared evidence are asked to share it.
**Metrics:** nudges, decisions held, private evidence shared (mean and lowest agent). The share of evidence shared is
logged for every protocol, so B2 can be compared on it.
**Simplifications:** the paper's facilitator was an LLM working from partial knowledge; ours is a deterministic rule with
perfect knowledge of who holds what (no extra model cost). LLM agents may share by default, leaving less room for gains;
the paper's null effect on decisions may repeat, which is why information *use* is also checked.

---

## How to compare them
Lab → Studies → **+ Method comparison (D0 · A1 · A2 · B1 · B2)** creates a draft study on the development cases. Run it as a
feasibility study first, check rounds and costs, set limits with headroom, then create a main study on the frozen held-out
cases with the same five conditions. Report cost per joint success with the bootstrap ranges, and each method's mechanism
metrics, next to the simplifications above.

*How these were chosen:* a literature search (Nov 2026) for the two most recent methods in each area that could be
replicated with prompting and routing only. Runner-ups: DarkForest (arXiv:2605.25188), S²-MAD (arXiv:2502.04790),
AgentDropout (ACL 2025; needs training), stand-up meetings (Ghosh & Wu, *Organization Science* 2023), centralized vs flat
networks under shifting evidence (Bernstein, Shore & Jang, *Organization Science* 2023).
