Proceedings of the 2025 Conference on Empirical Methods in Natural Language Processing: Industry Track, pages 1625–1653
November 4-9, 2025 ©2025 Association for Computational Linguistics
Enabling Self-Improving Agents to Learn at Test Time With
Human-In-The-Loop Guidance
Yufei He1*, Ruoyu Li2, Alex Chen2, Yue Liu1, Yulin Chen1, Yuan Sui1,
Cheng Chen2, Yi Zhu2, Luca Luo2, Frank Yang2, Bryan Hooi1
1National University of Singapore
2ByteDance Inc.
yufei.he@u.nus.edu
Abstract
Large language model (LLM) agents often
struggle in environments where rules and re-
quired domain knowledge frequently change,
such as regulatory compliance and user risk
screening. To address this limitation, we pro-
pose the Adaptive Reflective Interactive Agent
(ARIA)1, an LLM agent framework designed
specifically to continuously learn updated do-
main knowledge at test time. ARIA assesses
its own uncertainty through structured self-
dialogue, proactively identifying knowledge
gaps and requesting targeted explanations or
corrections from human experts. It then sys-
tematically updates an internal, timestamped
knowledge repository with provided human
guidance, detecting and resolving conflicting
or outdated knowledge through comparisons
and clarification queries. We evaluate ARIA
on the realistic customer due diligence name
screening task on TikTok Pay, alongside pub-
licly available dynamic knowledge tasks. Re-
sults demonstrate significant improvements in
adaptability and accuracy compared to base-
lines using standard offline fine-tuning and ex-
isting self-improving agents. ARIA has been
deployed on TikTok Pay serving over 150
million monthly active users.
1
Introduction
A fundamental ability of humans is that we can
learn diverse and complex skills “on the fly” (i.e.,
at test time), such as learning to play a new game
that we have never seen before. In contrast, current
large language model (LLM) agents typically lack
this crucial capability (Bommasani et al., 2021;
Huang et al., 2024). Although highly effective in
many scenarios thanks to large-scale pretraining
and fine-tuning, existing agents are generally un-
able to adapt once deployed (Li et al., 2024). When
*Part of the work was done when the author was an intern
at ByteDance Inc.
1The code is available at https://github.com/yf-he/
aria
encountering rapidly changing domain-specific
knowledge, rules, or scenarios they have never
seen, these LLM-based systems frequently fail or
become unreliable unless extensively retrained of-
fline on updated labeled data (Ge et al., 2023).
An important example highlighting this chal-
lenge is customer due diligence (CDD) (Mugarura,
2014) for global payment platforms—such as con-
ducting risk list name screening (Han et al., 2020)
for users. An agent unable to adapt its knowledge
and behavior based on these real-time changes be-
comes unreliable and non-compliant (Bjerregaard
and Kirchmaier, 2019).
The challenge lies in endowing agents with the
capacity for continuous learning and adaptation
directly during their deployment (at test time). To
bridge this gap, we propose the Adaptive Reflec-
tive Interactive Agent (ARIA), a general-purpose
framework designed to enable effective LLM learn-
ing at test time through structured self-assessment
and human-in-the-loop interactions. ARIA is ar-
chitected not just to execute tasks, but to actively
manage its own knowledge limitations and collab-
orate with human experts. This is enabled through
two core capabilities:
Intelligent Guidance Solicitation.
Upon pro-
ducing an initial preliminary judgment, ARIA re-
sponds to reflective questions about the clarity and
reliability of its reasoning, questioning whether it
possesses suitable domain knowledge, and recall-
ing prior related experiences.
Human-Guided Knowledge Adaptation.
Af-
ter identifying knowledge uncertainties, ARIA
proactively solicits support and receives guid-
ance—corrections, detailed explanations, or up-
dated rules—from human domain experts. It incor-
porates these human-provided knowledge inputs
into a structured knowledge repository that marks
each knowledge item with timestamps. Whenever
a new knowledge update occurs, ARIA retrieves re-
1625

lated entries by semantic matching in its repository
and compares them against the new information.
While we demonstrate ARIA’s effectiveness
within the context of name screening tasks, it is
conceived as a general framework. Any task requir-
ing strong, evolving domain-specific knowledge
where human expertise is available for ongoing re-
finement could benefit from this approach, particu-
larly those operating in rapidly changing environ-
ments such as legal document review, complex cus-
tomer support, or scientific discovery assistance.
Our primary contributions in this work are:
• We introduce the ARIA framework, a novel and
general approach enabling agents to achieve con-
tinuous learning and adaptation at test time by
leveraging human-in-the-loop guidance.
• We detail the core abilities underpinning ARIA:
mechanisms for Intelligent Guidance Solicita-
tion based on self-reflection and uncertainty
assessment, and methods for Human-Guided
Knowledge Adaptation that allow management
of human-provided knowledge over time.
• We validate ARIA’s effectiveness through experi-
ments on realistic CDD name screening tasks on
TikTok Pay and on public datasets and note its
successful deployment in a real-world industrial
setting.
2
Related Work
2.1
Learning at Test Time
Learning at test time (LTT) refers to the capac-
ity of a machine learning model to acquire new
knowledge and adapt its behavior during the in-
ference phase, which occurs after the model has
been fully trained and deployed in a real-world set-
ting. For LLMs, common approaches include in-
context learning (ICL) or few-shot learning, where
the model learns from examples provided within
the prompt (Brown et al., 2020; Min et al., 2021;
Wang et al., 2023), and retrieval-augmented gener-
ation (RAG), which incorporates external knowl-
edge retrieved based on the input (Lewis et al.,
2020; Dong et al., 2022). Other methods involve
test-time fine-tuning, adjusting model parameters
specifically for each incoming prompt (Hübot-
ter et al., 2024; Akyürek et al., 2024).
In the
agent context, self-learning agents aim to im-
prove autonomously through environmental inter-
action (Liu et al., 2025). While existing meth-
ods like ICL, RAG, test-time fine-tuning, and au-
tonomous self-learning offer some adaptability,
ARIA distinctively establishes a human-mediated
continuous learning loop, focusing on structured
knowledge integration, conflict resolution via hu-
man clarification, and persistent adaptation of an
evolving knowledge base at test time.
2.2
Human-in-the-Loop with LLMs
Human-in-the-loop (HITL) is a collaborative and it-
erative approach in the field of LLM that integrates
human input and expertise into various stages of
the LLM system’s lifecycle. A prominent example
is reinforcement learning from human feedback
(RLHF), which fine-tunes models to align their
outputs with human preferences, often collected of-
fline (Rafailov et al., 2023; Bai et al., 2022; Casper
et al., 2023). Other HITL applications involve
using human annotators to label data or provide
feedback on model outputs to guide iterative im-
provements (Li et al., 2025; Yan et al., 2024), or
assist in specific tasks like path planning for robotic
agents (Xiao and Wang, 2023). These HITL ap-
proaches typically focus on offline alignment or
use human input primarily as labels for subsequent
model refinement. They often do not address the
challenge of enabling an agent to dynamically so-
licit targeted help during task execution based on
its own assessment of uncertainty. ARIA’s HITL
mechanism is distinct in its focus on enabling the
agent to (1) intelligently initiate interaction based
on self-assessed knowledge gaps, and (2) collabo-
ratively build and maintain an evolving knowledge
base through structured dialogue and feedback in-
tegration with human experts during test time.
3
Problem Definition
3.1
Problem Statement
The problem is to design an agent that processes a
sequence of data instances X = (x1, x2, . . . , xN)
arriving at test time. The agent must make a pre-
diction ˆyi for each xi. The environment may be
dynamic, meaning the underlying data distribution
P(y|x) can change over time. The agent must
adapt its internal state or model Θ to maintain high
performance, with the ability to solicit targeted
guidance from a human expert oracle O under a
predefined interaction budget B.
1626

Intelligent Guidance Solicitation
Input
Submitted Customer
Info: Name, DoB, Photo 
Nationality, ID, Address…
Hit Person
Info: Name, DoB, Photo 
Nationality, ID, Address…
Task: Are they the 
same person?
LLM Agent
Human-Guided Knowledge Adaptation
Task: integrate
query and answer,
update knowledge
repository.
LLM Agent
Knowledge 
Repository
Retrieve relevant
knowledge based on x
Self-Dialogue
Give Preliminary Judgment: 
     Match/Non-Match
Answer Reflective Questions:
• "Explain the specific evidence 
from the input case and 
stored knowledge supporting 
your decision.”
• "Identify any implicit 
assumptions made during your 
reasoning.”
• "Assess your familiarity and 
confidence regarding the 
specific domain knowledge 
required.”
• …
Assess Confidence level:
     High, Moderate, Low
Final Output: Match 
/ Non-Match
High Confidence
Moderate/Low Confidence
Query Formulation
e.g., Label Request, 
Explanation, 
Rule Clarification
Human Expert
Provide answers to
different types of queries.
If significant knowledge
conflict, query human for help.
Knowledge item:
(id, knowledge content (rule, 
explanation, case example..), 
timestamp, status, metadate)  
Figure 1: Overview of the ARIA framework. The agent processes input, assesses the need for guidance via
self-reflection, and can solicit human expert feedback. This feedback is integrated into an evolving knowledge
repository, enabling learning at test time.
3.2
Formalism: Learning at Test Time with
Human-in-the-Loop Guidance
Let X be the input space and Y be the output (la-
bel) space. The agent encounters a stream of N
instances X = (x1, x2, . . . , xN), where xi ∈X.
The true label for xi is y∗
i ∈Y.
Learning at Test Time (LTT): The agent pos-
sesses an internal state or model parameterized by
Θi ∈Θ at time step i (before processing xi). Its
decision policy is π : X × Θ →Y, producing a
prediction ˆyi = π(xi; Θi). LTT is characterized
by the update of the agent’s state/model during the
sequential processing of test instances:
Θi+1 = f(Θi, xi, ˆyi, qi, hi)
(1)
where f is the learning update function, qi is a
query made to the human expert (if any), and hi is
the feedback received from the expert. The initial
state is Θ0. This signifies that learning occurs
instance by instance as the agent operates.
Human-in-the-Loop (HITL) Guidance: The
agent can interact with a human expert oracle O to
obtain guidance.
• At each time step i, the agent makes a decision
di ∈{predict_only, query_expert}.
• If di = query_expert, the agent selects a query
qi from a predefined set of query types Q. The
set Q defines the various forms of guidance the
agent can request from the human expert.
• Each query type q ∈Q has an associated cost
c(q) > 0. For simplicity in experiments, c(q)
can be set to 1 for all q ∈Q.
• The total cost of queries is constrained by a bud-
get B: PN
j=1 c(qj) ≤B.
• If qi ̸= null, the oracle O provides feedback
hi = O(xi, qi). This feedback hi is used in the
learning update function f.
Objective: The overall objective is to design the
agent’s policy π and learning function f to max-
imize a performance metric Mperf over the entire
stream X, subject to the budget constraint B.
max
π,f,query_strategy
N
X
i=1
Eval(ˆyi, y∗
i )
s.t.
N
X
j=1
c(qj) ≤B
(2)
where Eval(·, ·) is an evaluation function.
3.3
Instantiation in the CDD Context
This problem is instantiated in the CDD name
screening task on TikTok Pay.
• Input Space X: An agent receives pairs of user
information and watchlist hit person information
and determine if they refer to the same individ-
ual (Match) or not (Non-Match). A match deci-
sion typically prevents account opening. Each
xi = (ui, whi) is a pair of user information ui
and watchlist hit information whi. These include
fields such as names, aliases, native language
names, nationality, address, date of birth, identi-
fication documents, and for whi, sensitive infor-
mation like position or reason for listing.
• Output Space Y: {Match, Non-Match}.
• Data Stream: A sequence of N = 11, 846 real-
world cases, processed chronologically.
The
dataset is highly imbalanced, containing only
156 Match (Positive) cases, with the remainder
being Non-Match (Negative).
• Human Expert Oracle O: Real human domain
experts from the global payment platform’s com-
1627

pliance teams provide responses hi (feedback).
The forms of interaction include, but are not lim-
ited to, requesting case labels, explanations for
decisions, resolutions for knowledge conflicts, or
clarifications of rules.
• Dynamic Environment: The chronological na-
ture of the data, coupled with the real-world
source, means that underlying rules, data pat-
terns, and watchlist characteristics may evolve,
requiring the agent to adapt.
4
Methodology
4.1
Overview of ARIA
The agent, ARIA, processes a stream of instances
X = (x1, x2, . . . , xN). Its internal state, primarily
a Knowledge Repository (KRi), evolves from Θi ≈
KRi to Θi+1 ≈KRi+1 at each time step i. This
iterative learning process unfolds as follows:
1. Initial Task Processing: For an incoming in-
stance xi, the agent, using its current knowledge
repository KRi and its base LLM MLLM, gener-
ates an initial prediction ˆyi = π(xi; KRi, MLLM)
along with supporting reasoning ri. The policy
π combines retrieval from KRi with the reason-
ing capabilities of MLLM.
2. Intelligent Guidance Solicitation (IGS): The
agent performs a structured self-assessment
of its preliminary judgment (ˆyi, ri) and its
underlying knowledge.
This is denoted as
Si = IGS_Assess(ˆyi, ri, KRi), which includes
an assessed confidence level confi and iden-
tified knowledge gaps or uncertainties gi.
Based on this assessment Si, the agent de-
cides di ∈{predict_only, query_expert}.
If di
=
query_expert and the cumula-
tive query cost up to the previous step
Pi−1
j=1 c(qj) < B (where B is the total bud-
get), the agent then formulates a specific query
qi = IGS_FormulateQuery(Si) ∈Q. Q repre-
sents the set of available query types that facili-
tate various forms of human guidance.
3. Human Expert Interaction: The query qi (if
any) is presented to the human expert oracle O,
who provides feedback hi = O(xi, qi).
4. Human-Guided
Knowledge
Adaptation
(HGKA): The agent updates its knowledge
repository from KRi to KRi+1 using the feedback
hi and the context of xi, ˆyi, qi. This update
KRi+1
=
HGKA_Update(KRi, xi, ˆyi, qi, hi)
constitutes the core of ARIA’s LTT ability, f.
4.2
Intelligent Guidance Solicitation
The IGS is responsible for determining when hu-
man intervention is necessary and formulating tar-
geted queries to maximize the utility of human
feedback within the budget B.
Process. Given an instance xi, the agent’s initial
decision ˆyi = π(xi; KRi, MLLM), and its reasoning
ri, the IGS module initiates a self-reflection phase.
1. Structured Self-Dialogue:
The agent is
prompted with a predefined set of NRQ reflec-
tive questions RQ = {rq1, rq2, . . . , rqNRQ}. The
agent internally generates answers ansk
=
MLLM(rqk, xi, ˆyi, ri, KRi) for each rqk.
2. Confidence Self-Assessment: Based on Dself
i
,
the agent performs a self-assessment to de-
termine its confidence in ˆyi.
This results
in an explicit confidence statement confi =
AssessConfidence(Dself
i
), where confi
∈
C = {High, Moderate, Low}.
3. Intervention
Trigger
and
Query
For-
mulation:
The decision di to query the
expert is made:
di
=
query_expert
if
confi
∈
{Moderate, Low}
and
the
budget
constraint
is
not
violated
(i.e.,
Pi−1
j=1 c(qj) + c(potential qi) ≤B, where
c(potential qi) is the cost of the query to be
formulated). Otherwise, di = predict_only.
If di = query_expert, the agent formulates a
query qi = IGS_FormulateQuery(Dself
i
). An
illustrative example of this IGS process, de-
tailing the self-dialogue and subsequent query
formulation, is provided in Appendix B.1.
The self-dialogue Dself
i
is provided to the human
expert alongside qi, enabling them to deliver tar-
geted and efficient guidance hi.
4.3
Human-Guided Knowledge Adaptation
The
Human-Guided
Knowledge
Adaptation
(HGKA) module is responsible for integrating
the human expert’s feedback hi into the agent’s
knowledge repository KRi, thereby updating its
state to KRi+1. Let tcurrent represent the current
processing time or timestamp.
Knowledge
Repository
Structure.
The
knowledge
repository
KR
is
a
collection
of
structured
knowledge
items
k.
Each
item k
∈
KR is represented as a tuple:
k = (kid, K, tsadded, tsvalidated, S, Mmeta) where:
• kid: A unique identifier for the knowledge item.
• K: The content of the knowledge item (e.g., a
1628

rule, an explanation, a factual statement, or a
case exemplar (xj, y∗
j , reasonj)).
• tsadded: Timestamp of when K was initially
added to KR.
• tsvalidated: Timestamp of when K was last vali-
dated or updated by human feedback, or its status
was changed.
• S ∈{Valid, PotentiallyOutdated, Superseded}:
The current validity status of K.
• Mmeta: Additional metadata, such as source of
K (human expert, self-derived), usage frequency,
links to related kids (e.g., kidsuperseded_by).
The agent’s state is Θi ≈KRi.
Processing Feedback and Updating Knowledge.
When human feedback hi is received for query qi
concerning xi, HGKA module performs KRi+1 =
HGKA_Update(KRi, xi, ˆyi, qi, hi) as follows:
1. Knowledge Item Extraction: The feedback hi
is parsed to extract a set of new explicit knowl-
edge assertions, denoted Kasserted.
2. Timestamping and Initial Storage: Each ex-
tracted knowledge content Kextracted forms a
new, timestamped (tsadded/validated = tcurrent),
Valid item with a unique kid and metadata,
then is provisionally added to KR.
3. Conflict Detection and Resolution: For each
newly extracted knowledge content Kextracted:
(a) Retrieval of Related Knowledge: Iden-
tify potentially related existing knowl-
edge items KRrel
=
{kj
∈
KRi
|
Sim(Kextracted, kj.K) > τsim}, where Sim
is a semantic similarity function (e.g.,
based on embeddings) and τsim is a sim-
ilarity threshold.
(b) Comparison and Status Update: For
each kold ∈KRrel: An LLM-based compar-
ison function Comp(Kextracted, kold.K) →
relation determines if Kextracted contra-
dicts, supersedes, updates, or is consistent
with kold.K. An example of this conflict
detection and resolution process is pro-
vided in Appendix B.2.
4. Active
Clarification
Query
Generation:
If comparison reveals unresolvable ambigu-
ity/conflict , HGKA generates an internal clari-
fication query q′
new. (Example: Appendix B.3)
Temporally-Informed Knowledge Retrieval. To
process an instance xj at current time tcurrent,
the agent’s policy π(xj; KRj, MLLM) utilizes a
relevant knowledge subset KRsubset.
This sub-
set is retrieved by scoring and ranking items
k
∈
KRj based on three factors:
a valid-
ity weight WS(k.S) (where WS(Valid)
=
1.0, WS(PotentiallyOutdated) = wpo, and
WS(Superseded)
=
0.0), a recency score
ST (k, tcurrent) = exp(−λ · (tcurrent −k.tsvalidated))
reflecting the timeliness of k.tsvalidated, and a se-
mantic relevance score SR(k, xj) quantifying the
contextual pertinence of k.K to xj. These are com-
bined into a composite score:
Score(k, xj, tcurrent) = WS(k.S)×
ST (k, tcurrent) × SR(k, xj)
(3)
5
Deployment on TikTok Pay
We evaluate ARIA on CDD name screening task
on TikTok Pay, as introduced in Section 3.3 and
Appendix A.
5.1
Baselines
ARIA interacts with a human expert oracle O up
to a budget B. Offline Fine-tuning and RAG base-
lines are prepared before deployment using knowl-
edge equivalent to budget B. Active learning base-
lines also interact with O up to budget B.
Static Agent: An LLM agent with general knowl-
edge and no task-specific adaptation.
Offline Fine-tuning: An agent fine-tuned once
before deployment on data equivalent to budget B.
RAG Agent: An LLM agent using a static knowl-
edge base (KB) populated before deployment with
data equivalent to budget B.
Active Learning (Random Querying): Queries
the human expert oracle O by selecting cases ran-
domly up to budget B.
Active Learning (Simple Uncertainty Sampling):
Queries O up to budget B based on a standard un-
certainty sampling heuristic (e.g., low confidence).
Self-Refine (Madaan et al., 2023): An LLM iter-
atively refining its own output by generating an
initial response, providing self-feedback, and then
improving the response based on that feedback.
Reflexion (Shinn et al., 2023): Agent improves
itself by verbally reflecting on past task feedback.
These reflections are stored in memory to guide
subsequent decision-making.
Multi-Agent Debate (Du et al., 2023): This ap-
proach uses multiple LLM agents that learn from
1629

Method
Model
Sensitivity
Specificity
B=50
B=100
B=500
B=1000
B=50
B=100
B=500
B=1000
Static Agent
Qwen2.5-7B
0.6474
0.6124
Static Agent
GPT-4o
0.7051
0.6539
Offline Fine-tuning
Qwen2.5-7B
0.6410
0.6603
0.6731
0.6987
0.6317
0.6492
0.6776
0.6791
RAG Agent
GPT-4o
0.7756
0.8013
0.8141
0.8333
0.6864
0.7051
0.7308
0.7462
Self-Improving Agents
Self-Refine
GPT-4o
0.7244
0.6821
Reflexion
GPT-4o
0.7692
0.6902
Multi-Agent Debate
GPT-4o
0.7628
0.6970
Active Learning Methods
Random Querying
GPT-4o
0.7372
0.7949
0.8205
0.8590
0.6725
0.6994
0.7410
0.7667
Simple Uncertainty
GPT-4o
0.7884
0.8013
0.8590
0.8718
0.6936
0.7218
0.7590
0.7853
ARIA (ours)
Qwen2.5-7B
0.7564
0.7756
0.8077
0.8397
0.6859
0.7154
0.7549
0.7795
ARIA (ours)
GPT-4o
0.8013
0.8333
0.8653
0.8910
0.7151
0.7423
0.7810
0.8026
Table 1: Overall performance comparison on the global payment platform for varying query budgets (B).
The dataset consists of a chronological sequence of N = 11, 846 real-world cases, highly imbalanced with only
156 Match (Positive) cases and 11,690 Non-Match (Negative) cases.
Method (B=100)
Sensitivity
Specificity
ARIA
0.8333
0.7423
Labels-Only ARIA
0.7949
0.7139
w/o Self-Dialogue
0.8141
0.7319
w/o KR Conflict Resolution
0.8012
0.7128
w/o Temporally-Informed KR
0.8333
0.7341
Table 2: Ablation studies on ARIA key components.
Method
Sensitivity
Specificity
AHT
Human Experts
1.0
1.0
12min
ARIA (B=50)
0.8013
0.7151
0.13min
ARIA (B=100)
0.8333
0.7423
0.15min
ARIA (B=500)
0.8653
0.7810
0.20min
ARIA (B=1000)
0.8910
0.8026
0.23min
ARIA w/ Full Oracle
Access (B=3121)
0.9428
0.8814
0.41min
Table 3: Efficiency comparison of the ARIA model
and Human Experts.
each others’ feedback to collaboratively refine so-
lutions through iterative debate.
For fair comparison, we use GPT-4o (Hurst et al.,
2024) and Qwen2.5-7B (Yang et al., 2024) as base
LLMs for all baselines and our method.
5.2
Evaluation Metrics
We evaluate performance using:
Sensitivity: The proportion of actual Match cases
that are correctly identified as Match.
Specificity: The proportion of actual Non-Match
cases that are correctly identified as Non-Match.
5.3
Results
The performance comparison on the global pay-
ment platform application (Table 1) reveals several
key insights. 1) ARIA, particularly with GPT-4o,
consistently outperforms other methods across all
Method
Model
Accuracy
B=50
B=100
B=500
B=1000
B=2000
Static Agent
Qwen2.5-7B
0.3515
Static Agent
GPT-4o
0.4872
Offline Fine-tuning
Qwen2.5-7B
0.3680
0.3918
0.4317
0.4721
0.4909
RAG Agent
GPT-4o
0.4953
0.5101
0.5309
0.5597
0.5735
Self-Improving Agents
Self-Refine
GPT-4o
0.4931
Reflexion
GPT-4o
0.4995
Multi-Agent Debate
GPT-4o
0.4890
Active Learning Methods
Random Querying
GPT-4o
0.4901
0.4983
0.5154
0.5338
0.5492
Simple Uncertainty
GPT-4o
0.4975
0.5116
0.5353
0.5604
0.5789
ARIA (ours)
Qwen2.5-7B
0.3801
0.4196
0.4703
0.5117
0.5435
ARIA (ours)
GPT-4o
0.5084
0.5397
0.5781
0.6072
0.6358
Table 4: Overall performance on the CUAD dataset
for clause type identification. The dataset consists of
N = 13, 101 contract clauses across 41 types.
query budgets (B) in both Sensitivity and Speci-
ficity, showcasing its superior adaptability through
effective human-in-the-loop guidance. 2) ARIA
demonstrates more significant performance gains
with increasing query budgets compared to other
active learning strategies, indicating more efficient
use of human expertise. 3) ARIA effectively en-
hances the performance of both stronger (GPT-4o)
and weaker (Qwen2.5-7B) base models, often out-
performing static or self-improving agents reliant
on GPT-4o. This underscores ARIA’s test-time
learning abilities and its advantage in integrating
real-time human feedback. Some case examples
can be found in Appendix C.
5.4
Model Analysis
Ablation on Key Components. Table 2 shows the
impact of removing ARIA’s components. Lim-
iting to Labels-Only’ queries reduced effective-
ness, underscoring comprehensive feedback’s im-
1630

portance. Without Self-Dialogue’, performance
decreased due to a lack of self-assessment. Ex-
cluding KR Conflict Resolution’ led to drops,
stressing its role in consistency.
Omitting
Temporally-Informed KR’ affected precision by
disregarding recent knowledge.
Efficiency Analysis. Table 3 highlights ARIA’s
efficiency. Traditionally, human experts needed
about 12 minutes per case. In contrast, ARIA’s Av-
erage Handling Time (AHT) is significantly lower,
even with increasing query budgets. With Full Ora-
cle Access (B=3121), the AHT is just 0.41 minutes,
showcasing substantial time savings. Answering
targeted queries from ARIA is also much quicker
than reviewing cases from scratch.
6
Experiments on Public Dataset
6.1
Setup
We evaluate ARIA in the domain of legal text
analysis using the publicly available Contract Un-
derstanding Atticus Dataset (CUAD) (Hendrycks
et al., 2021), which includes over 500 commercial
contracts annotated with 41 clause types. ARIA
sequentially processes extracted clauses to identify
their types and assess potential risks.
To enable Learning at Test Time (LTT) with
Human-in-the-Loop (HITL), we simulate the ex-
pert oracle (O) using a powerful LLM, provid-
ing scalable human-like feedback. Clauses are
streamed chronologically, with simulated concept
drifts introduced via shifting clause distributions
and evolving oracle responses.
Detailed settings, including data preprocessing,
oracle prompting, dynamic simulation, metrics,
and baselines, are provided in Appendix D.
6.2
Results
Experiments on the CUAD dataset, presented in
Table 4, further highlight ARIA’s efficacy in test-
time learning.
1) The necessity of LTT with
HITL for complex, multi-class legal understand-
ing is evident: ARIA (GPT-4o) achieves a remark-
able 0.6358 accuracy at B = 2000, a substantial
leap from the 0.4872 accuracy of the static GPT-
4o agent, demonstrating its capability to adapt
where pre-trained knowledge falls significantly
short. 2) ARIA’s structured human interaction and
dynamic knowledge management prove superior
to autonomous adaptation or static retrieval strate-
gies. ARIA (GPT-4o) consistently outperforms
self-improving agents (e.g., Reflexion at 0.4995)
and the RAG agent (0.5735 at B = 2000 equiva-
lent pre-population), underscoring the value of its
targeted guidance solicitation in navigating the nu-
ances of evolving legal interpretations and clause
variations. 3) The framework demonstrates effi-
cient knowledge acquisition and scalability. Even
with a modest budget (B = 500), ARIA (GPT-
4o) reaches 0.5781 accuracy, surpassing the RAG
agent with a much larger implicit budget.
7
Conclusion
This paper introduces ARIA, an LLM agent frame-
work for test-time learning through human-in-the-
loop guidance.
ARIA addresses conventional
model limitations in dynamic environments by as-
sessing uncertainty via self-dialogue, soliciting ex-
pert corrections, and updating a knowledge base.
Experiments on the name screening task in TikTok
Pay and with public datasets demonstrate signifi-
cant improvements. ARIA’s principles are broadly
applicable to domains requiring evolving knowl-
edge and human expertise, paving the way for more
robust and reliable AI agents.
1631

Limitations
While ARIA demonstrates promising results in
enabling agents to learn at test time with human-
in-the-loop guidance, several limitations warrant
discussion.
First, the effectiveness of ARIA is intrinsically
linked to the availability, quality, and scalability
of human expertise. The framework assumes ac-
cess to responsive and accurate human experts. In
scenarios with very high query volumes, or where
expert feedback is delayed, inconsistent, or erro-
neous, ARIA’s learning capability and overall per-
formance could be significantly impacted. The
practical cost and logistical challenges of maintain-
ing a pool of readily available experts for diverse
and evolving tasks are also important considera-
tions not fully explored.
Second, the complexity of knowledge repre-
sentation and conflict resolution could pose chal-
lenges as the knowledge repository (KR) grows in
size and intricacy. While ARIA incorporates mech-
anisms for timestamping and managing conflicting
information, highly nuanced, subtly contradictory,
or deeply contextual expert guidance might be dif-
ficult to integrate perfectly. Ensuring the long-term
coherence and accuracy of a large, evolving KR,
and preventing the accumulation of outdated or
overly specific knowledge, remains an ongoing
research area.
Third, regarding generalizability, ARIA has
been primarily validated on tasks like customer due
diligence and legal text analysis. These domains,
while dynamic, often involve relatively structured
information and specific types of uncertainty. The
framework’s adaptability and the efficacy of its
current self-reflection and knowledge adaptation
mechanisms in vastly different domains—such as
those requiring complex common-sense reasoning,
creative generation, or interaction with the physi-
cal world—would require further investigation and
potentially significant modifications to the query
types and self-dialogue structures.
Fourth, the evaluation on the public CUAD
dataset relied on an LLM-simulated human ex-
pert oracle. Although this approach facilitates
scalable experimentation, it may not fully replicate
the nuances, potential biases, occasional errors, or
the depth of insight that a genuine human domain
expert would provide. The dynamics of interaction
and the nature of guidance from a simulated oracle
might differ from real-world human-agent collabo-
ration, potentially affecting the observed learning
patterns.
Finally, the efficiency of the self-dialogue
and knowledge management processes could
become a concern in applications with extremely
high throughput or stringent real-time constraints.
While crucial for ARIA’s adaptability, the compu-
tational overhead associated with structured self-
reflection, semantic retrieval from the KR, and con-
flict resolution mechanisms might need further op-
timization for certain deployment scenarios. The
current study focuses more on the effectiveness of
learning rather than a detailed analysis of compu-
tational performance under heavy load.
Ethical Considerations
A key ethical consideration revolves around the hu-
man experts involved in ARIA’s learning loop. In
business contexts, these individuals are paid, well-
trained employees. While ARIA is designed to aug-
ment their capabilities and improve efficiency, the
increasing proficiency of such AI systems raises
concerns about the long-term impact on their roles.
There is a potential for over-reliance on the au-
tomated system, which could lead to a deskilling
of these trained employees over time if their di-
rect engagement with complex decision-making
diminishes. Furthermore, as ARIA demonstrates
significant efficiency gains, there is an inherent risk
that such technology could be perceived or utilized
as a means to reduce the human workforce, leading
to job displacement for these skilled professionals.
Therefore, careful consideration must be given to
deploying ARIA in a manner that genuinely collab-
orates with and empowers human experts, focus-
ing on handling increased complexity or volume,
rather than solely as a replacement strategy. This
includes fostering new skills, redefining job roles
to work alongside AI, and ensuring that the benefits
of automation are shared equitably.
References
Ekin Akyürek, Mehul Damani, Linlu Qiu, Han Guo,
Yoon Kim, and Jacob Andreas. 2024. The surpris-
ing effectiveness of test-time training for abstract
reasoning. arXiv preprint arXiv:2411.07279.
Yuntao Bai, Andy Jones, Kamal Ndousse, Amanda
Askell, Anna Chen, Nova DasSarma, Dawn Drain,
Stanislav Fort, Deep Ganguli, Tom Henighan, and 1
others. 2022. Training a helpful and harmless assis-
tant with reinforcement learning from human feed-
back. arXiv preprint arXiv:2204.05862.
1632

Elisabetta Bjerregaard and Tom Kirchmaier. 2019. The
danske bank money laundering scandal: A case
study.
Rishi Bommasani, Drew A Hudson, Ehsan Adeli, Russ
Altman, Simran Arora, Sydney von Arx, Michael S
Bernstein, Jeannette Bohg, Antoine Bosselut, Emma
Brunskill, and 1 others. 2021. On the opportuni-
ties and risks of foundation models. arXiv preprint
arXiv:2108.07258.
Tom Brown, Benjamin Mann, Nick Ryder, Melanie
Subbiah, Jared D Kaplan, Prafulla Dhariwal, Arvind
Neelakantan, Pranav Shyam, Girish Sastry, Amanda
Askell, and 1 others. 2020. Language models are
few-shot learners. Advances in neural information
processing systems, 33:1877–1901.
Stephen
Casper,
Xander
Davies,
Claudia
Shi,
Thomas Krendl Gilbert, Jérémy Scheurer, Javier
Rando, Rachel Freedman, Tomasz Korbak, David
Lindner, Pedro Freire, and 1 others. 2023. Open
problems and fundamental limitations of reinforce-
ment learning from human feedback. arXiv preprint
arXiv:2307.15217.
Qingxiu Dong, Lei Li, Damai Dai, Ce Zheng, Jingyuan
Ma, Rui Li, Heming Xia, Jingjing Xu, Zhiyong Wu,
Tianyu Liu, and 1 others. 2022. A survey on in-
context learning. arXiv preprint arXiv:2301.00234.
Yilun Du, Shuang Li, Antonio Torralba, Joshua B
Tenenbaum, and Igor Mordatch. 2023. Improving
factuality and reasoning in language models through
multiagent debate. In Forty-first International Con-
ference on Machine Learning.
Yingqiang Ge, Wenyue Hua, Kai Mei, Juntao Tan,
Shuyuan Xu, Zelong Li, Yongfeng Zhang, and 1
others. 2023. Openagi: When llm meets domain
experts. Advances in Neural Information Processing
Systems, 36:5539–5568.
Jingguang Han, Yuyun Huang, Sha Liu, and Kieran
Towey. 2020. Artificial intelligence for anti-money
laundering: a review and extension. Digital Finance,
2(3):211–239.
Dan Hendrycks, Collin Burns, Anya Chen, and Spencer
Ball. 2021. Cuad: An expert-annotated nlp dataset
for legal contract review. In Thirty-fifth Conference
on Neural Information Processing Systems Datasets
and Benchmarks Track (Round 1).
Xu Huang, Weiwen Liu, Xiaolong Chen, Xingmei
Wang, Hao Wang, Defu Lian, Yasheng Wang, Ruim-
ing Tang, and Enhong Chen. 2024. Understanding
the planning of llm agents: A survey. arXiv preprint
arXiv:2402.02716.
Jonas Hübotter, Sascha Bongni, Ido Hakimi, and An-
dreas Krause. 2024.
Efficiently learning at test-
time: Active fine-tuning of llms.
arXiv preprint
arXiv:2410.08020.
Aaron Hurst, Adam Lerer, Adam P Goucher, Adam
Perelman, Aditya Ramesh, Aidan Clark, AJ Ostrow,
Akila Welihinda, Alan Hayes, Alec Radford, and 1
others. 2024. Gpt-4o system card. arXiv preprint
arXiv:2410.21276.
Patrick Lewis, Ethan Perez, Aleksandra Piktus, Fabio
Petroni, Vladimir Karpukhin, Naman Goyal, Hein-
rich Küttler, Mike Lewis, Wen-tau Yih, Tim Rock-
täschel, and 1 others. 2020. Retrieval-augmented
generation for knowledge-intensive nlp tasks. Ad-
vances in neural information processing systems,
33:9459–9474.
Hang Li, Yucheng Chu, Kaiqi Yang, Yasemin Copur-
Gencturk, and Jiliang Tang. 2025. Llm-based au-
tomated grading with human-in-the-loop.
arXiv
preprint arXiv:2504.05239.
Yuanchun Li, Hao Wen, Weijun Wang, Xiangyu Li,
Yizhen Yuan, Guohong Liu, Jiacheng Liu, Wenx-
ing Xu, Xiang Wang, Yi Sun, and 1 others. 2024.
Personal llm agents: Insights and survey about the
capability, efficiency and security. arXiv preprint
arXiv:2401.05459.
Ben Liu, Jihai Zhang, Fangquan Lin, Cheng Yang, Min
Peng, and Wotao Yin. 2025. Symagent: A neural-
symbolic self-learning agent framework for complex
reasoning over knowledge graphs. arXiv preprint
arXiv:2502.03283.
Aman Madaan, Niket Tandon, Prakhar Gupta, Skyler
Hallinan, Luyu Gao, Sarah Wiegreffe, Uri Alon,
Nouha Dziri, Shrimai Prabhumoye, Yiming Yang,
and 1 others. 2023. Self-refine: Iterative refinement
with self-feedback. Advances in Neural Information
Processing Systems, 36:46534–46594.
Sewon Min, Mike Lewis, Luke Zettlemoyer, and Han-
naneh Hajishirzi. 2021. Metaicl: Learning to learn
in context. arXiv preprint arXiv:2110.15943.
Norman Mugarura. 2014. Customer due diligence (cdd)
mandate and the propensity of its application as a
global aml paradigm. Journal of Money Laundering
Control, 17(1):76–95.
Rafael Rafailov, Archit Sharma, Eric Mitchell, Christo-
pher D Manning, Stefano Ermon, and Chelsea Finn.
2023.
Direct preference optimization: Your lan-
guage model is secretly a reward model. Advances in
Neural Information Processing Systems, 36:53728–
53741.
Noah Shinn, Federico Cassano, Ashwin Gopinath,
Karthik Narasimhan, and Shunyu Yao. 2023. Re-
flexion: Language agents with verbal reinforcement
learning. Advances in Neural Information Process-
ing Systems, 36:8634–8652.
Xinyi Wang, Wanrong Zhu, Michael Saxon, Mark
Steyvers, and William Yang Wang. 2023.
Large
language models are latent variable models: Explain-
ing and finding good demonstrations for in-context
learning. Advances in Neural Information Process-
ing Systems, 36:15614–15638.
1633

Hengjia Xiao and Peng Wang. 2023. Llm a*: Human
in the loop large language models enabled a* search
for robotics. arXiv preprint arXiv:2312.01797.
Lixiang Yan, Lele Sha, Linxuan Zhao, Yuheng Li,
Roberto Martinez-Maldonado, Guanliang Chen,
Xinyu Li, Yueqiao Jin, and Dragan Gaševi´c. 2024.
Practical and ethical challenges of large language
models in education: A systematic scoping review.
British Journal of Educational Technology, 55(1):90–
112.
An Yang, Baosong Yang, Beichen Zhang, Binyuan Hui,
Bo Zheng, Bowen Yu, Chengyuan Li, Dayiheng Liu,
Fei Huang, Haoran Wei, Huan Lin, Jian Yang, Jian-
hong Tu, Jianwei Zhang, Jianxin Yang, Jiaxi Yang,
Jingren Zhou, Junyang Lin, Kai Dang, and 22 oth-
ers. 2024. Qwen2.5 technical report. arXiv preprint
arXiv:2412.15115.
1634

Contents
1
Introduction
1
2
Related Work
2
2.1
Learning at Test Time . . . . . . .
2
2.2
Human-in-the-Loop with LLMs
.
2
3
Problem Definition
2
3.1
Problem Statement
. . . . . . . .
2
3.2
Formalism: Learning at Test Time
with Human-in-the-Loop Guidance
3
3.3
Instantiation in the CDD Context .
3
4
Methodology
4
4.1
Overview of ARIA . . . . . . . .
4
4.2
Intelligent Guidance Solicitation .
4
4.3
Human-Guided Knowledge Adap-
tation
. . . . . . . . . . . . . . .
4
5
Deployment on TikTok Pay
5
5.1
Baselines
. . . . . . . . . . . . .
5
5.2
Evaluation Metrics
. . . . . . . .
6
5.3
Results . . . . . . . . . . . . . . .
6
5.4
Model Analysis . . . . . . . . . .
6
6
Experiments on Public Dataset
7
6.1
Setup
. . . . . . . . . . . . . . .
7
6.2
Results . . . . . . . . . . . . . . .
7
7
Conclusion
7
Appendix
11
A Details of Deployment on TikTok Pay
11
A.1 Task Background . . . . . . . . .
11
A.2
Reflective Questions
. . . . . . .
12
A.3
Baselines
. . . . . . . . . . . . .
12
B
Illustrative Examples of ARIA’s Mecha-
nisms
13
B.1
Example: Intelligent Guidance So-
licitation (IGS) in Action . . . . .
13
B.2
Example: Conflict Detection and
Resolution . . . . . . . . . . . . .
13
B.3
Example:
Active Clarification
Query Generation . . . . . . . . .
13
C ARIA CDD Task Case Examples
13
C.1
Case
1:
Malay
Name
with
Patronymic and DOB Discrepancy
13
C.2
Case 2: Name Transliteration and
Fuzzy DOB (Year Only)
. . . . .
13
C.3
Case 3: Name with Initials, DOB
Transposition, and Address Corre-
lation
. . . . . . . . . . . . . . .
13
D Experiment Setup for Public Dataset
25
D.1
Dataset: CUAD (Contract Under-
standing Atticus Dataset) . . . . .
25
D.1.1
Description and Suitability
25
D.2
Preprocessing and Stream Genera-
tion
. . . . . . . . . . . . . . . .
25
D.3
Instantiation in the LTT with HITL
Guidance Framework . . . . . . .
25
D.4 ARIA Agent Configuration . . . .
27
D.5
LLM-Simulated Human Expert
Oracle (MOracle) Implementation .
28
D.6
Baseline Models for Comparison .
28
A
Details of Deployment on TikTok Pay
A.1
Task Background
We use the task of Customer Due Diligence (CDD)
name screening for TikTok Pay as a running ex-
ample. In this scenario, the agent assists human
experts by evaluating new customer applications
against various risk factors, primarily focusing on
risk list screening. This domain faces frequent
updates to regulations and watchlists, inherent am-
biguity in data (e.g., name variations), and requires
nuanced interpretation, making continuous learn-
ing essential. The typical workflow involves:
1. A user submits personal information (name,
date of birth, address, etc.) for account opening.
2. A retrieval system queries large databases (e.g.,
risk lists) and returns potential matches ("hits")
based on the submitted information.
3. An agent receives pairs of user information and
hit information and must determine if they refer
to the same individual (Match) or not (Non-
match). A match decision typically prevents
account opening.
This task is challenging due to incomplete or in-
consistent information in both user submissions
and database entries, as well as ambiguous or fre-
quently changing screening rules (e.g., due to regu-
latory updates). Simply providing all policies and
rules within a large prompt context to an LLM is
impractical due to the inherent ambiguity in com-
plex regulatory texts which LLMs may struggle to
interpret and apply consistently, especially when
rules conflict.
Consequently, the current industry convention
1635

often relies heavily on manual auditing of most
cases by human experts. While ensuring accu-
racy, this approach consumes significant time and
financial resources. These limitations underscore
the need for a more adaptive and collaborative ap-
proach like ARIA, which seeks to leverage automa-
tion while intelligently engaging human expertise
where it is most needed.
A.2
Reflective Questions
Below is a list of example reflective questions
(rqk ∈RQ) an agent might use for self-assessment.
These questions are designed to probe the agent’s
understanding of the input case (xi), the basis for
its preliminary judgment (ˆyi), any implicit assump-
tions made, the relevance and sufficiency of its
stored knowledge (KRi), and consistency with
past, similar instances.
• Explain the specific evidence from the input case
and stored knowledge supporting your decision.
• Identify any implicit assumptions made during
your reasoning.
• Assess your familiarity and confidence regard-
ing the specific domain knowledge required (e.g.,
’How familiar am I with company policy on ac-
ceptable DOB discrepancies? Do I know the
rules for matching Chinese name variations?’).
• Compare this case to similar past experiences
and assess the consistency of your reasoning.
• Based on the input case xi = “...”, my prelimi-
nary judgment is ˆytype. What is my confidence
level (High/Moderate/Low) for this judgment,
and why?
• Which specific phrases or keywords in the input
case xi support this classification? Are there any
conflicting indicators within the case?
• After retrieving relevant items from my knowl-
edge repository KRi, how consistent is my pre-
liminary judgment ˆytype with these items (e.g.,
definitions, exemplars, rules)?
• What are the key obligations and permissions im-
plied by the input case xi if it is indeed classified
as ˆytype?
• Is my knowledge regarding the predicted type
ˆytype (including definitions and rules) in my
knowledge repository KRi marked as recently
validated, or is it potentially outdated?
A.3
Baselines
To evaluate the specific contributions of ARIA’s
components, we compare its performance against
several baseline models. ARIA interacts with the
human expert oracle O up to budget B during its
run. For fair comparison, the Offline Fine-tuning
and RAG baselines are provided before deploy-
ment with knowledge derived from an equivalent
set of human interactions (representing the same
budget B). The active learning baselines interact
during their run, similar to ARIA, but use different
query strategies.
Static Agent (No Prior Exposure): An LLM
agent initialized with general knowledge. It pro-
cesses all cases xi using its fixed initial policy π0.
Offline Fine-tuning (Pre-Deployment):
This
agent is fine-tuned once before deployment on the
labeled examples and explanations derived from
the human interaction set (equivalent to budget B).
After deployment, it operates as a static model,
using the policy learned during this single pre-
training phase.
RAG Agent (Static Populated KB): An LLM
agent employing Retrieval-Augmented Generation
(RAG). Its static knowledge base is populated be-
fore deployment with the rules, explanations, and
labeled examples derived from the same set of hu-
man interactions (equivalent to budget B) available
to ARIA and the Fine-tuned agent. During the test
run, it retrieves from this fixed knowledge base to
generate decisions but cannot update the KB or
resolve conflicts dynamically.
Active Learning (Random Querying):
This
agent operates similarly to ARIA by querying the
human expert oracle O during the test run, up to
the budget B. However, it selects cases xi to query
randomly, without using any intelligent strategy
based on uncertainty or self-reflection. It uses the
feedback (e.g., labels) to update its internal state
(e.g., for few-shot prompting).
Active Learning (Simple Uncertainty Sam-
pling): Like the random querying agent, this base-
line interacts with the expert oracle O during the
run up to budget B. It decides when to query
based on a standard uncertainty sampling heuris-
tic (e.g., querying when the prediction confidence
score is below a threshold θ).
This compares
ARIA’s structured self-reflection against simpler,
common active learning query strategies for utiliz-
ing the budget B.
1636

Self-Refine (Madaan et al., 2023): This approach
enables a language model to iteratively improve its
own outputs without requiring additional training
data or separate models. The core idea involves the
model generating an initial response, then critically
evaluating that response to provide feedback to it-
self, and subsequently using this feedback to gen-
erate a refined output. This feedback-refinement
loop can be repeated to enhance the quality of the
final response.
Reflexion (Shinn et al., 2023): This framework
allows language agents to learn from past expe-
riences through verbal reinforcement rather than
by updating their underlying model weights. Re-
flexion agents reflect on feedback received from
tasks (which can be simple scores or textual cri-
tiques), generate textual self-reflections, and store
these in an episodic memory. This memory of
past reflections then helps guide the agent to make
better decisions and improve its performance in
subsequent attempts.
Multi-Agent Debate (Du et al., 2023):
This
method utilizes multiple language model instances,
or "agents," to collaboratively solve a problem or
arrive at an answer. The agents individually gener-
ate initial responses and then engage in a structured
debate over one or more rounds. During the debate,
agents can present their reasoning, critique the out-
puts of other agents, and refine their own positions
based on the collective discussion. This process
aims to improve the accuracy and robustness of the
final outcome by leveraging diverse perspectives
and encouraging critical evaluation.
B
Illustrative Examples of ARIA’s
Mechanisms
B.1
Example: Intelligent Guidance
Solicitation (IGS) in Action
The following example 2 illustrates the IGS pro-
cess as described in Section 4.2.
B.2
Example: Conflict Detection and
Resolution
Comparison and Status Update:
For each
kold ∈KRrel: An LLM-based comparison function
Comp(Kextracted, kold.K) →relation determines
if Kextracted contradicts, supersedes, updates, or is
consistent with kold.K.
• If Kextracted supersedes kold.K:
kold.S
←
Superseded;
kold.Mmeta.superseded_by
←
knew.kid; kold.tsvalidated ←tcurrent.
• If Kextracted conflicts with kold.K making kold.K
uncertain: kold.S ←PotentiallyOutdated;
kold.tsvalidated ←tcurrent.
The updated kold items and the new knowledge
item knew (containing Kextracted) become part of
KRi+1. The example 3 demonstrates the conflict
detection and resolution mechanism within HGKA,
as described in Section 4.3.
B.3
Example: Active Clarification Query
Generation
The example 4 illustrates how HGKA can gener-
ate a query for active clarification, as discussed in
Section 4.3.
C
ARIA CDD Task Case Examples
Please note: All personal information data and
review rules in the examples presented in this
appendix and the main text (including all data
details) are fictional or have been desensitized
for illustrative purposes only and do not repre-
sent real user data or complete actual rules.
C.1
Case 1: Malay Name with Patronymic
and DOB Discrepancy
The following example 5 6 7 illustrates the whole
ARIA process.
C.2
Case 2: Name Transliteration and Fuzzy
DOB (Year Only)
The following example, illustrated across Figure 8,
Figure 9, and Figure 10, demonstrates ARIA’s pro-
cess for a case involving name transliteration and
a year-only DOB match against a risks list.
C.3
Case 3: Name with Initials, DOB
Transposition, and Address Correlation
The following example, illustrated across Fig-
ure 11, Figure 12, and Figure 13, demonstrates
ARIA’s process for a case involving a name with
initials, a potential DOB transposition, and address
correlation against a financial fraud watchlist.
1637

Example: Intelligent Guidance Solicitation in Action
Task: Compare User (Li Xiaoming, DOB 1985-03-12) with Watchlist Hit (Li Xiao Ming, DOB
1985-03-12, Nationality Unknown).
Agent Initial Decision: "True Positive (Match). The names are identical when ignoring spacing,
and the DOB matches exactly."
Self-Reflection Dialogue (Agent’s internal process):
• Q: Explain evidence. A: Name ‘Li Xiaoming‘ vs ‘Li Xiao Ming‘ is a direct match considering
common spacing variations based on a known rule. DOB ‘1985-03-12‘ matches exactly.
• Q: Identify assumptions. A: Assumed the rule about ignoring spacing is universally applicable
here. Assumed lack of nationality information on the hit is not a disqualifier for a match if
name/DOB are strong, as I don’t have an explicit rule for this scenario.
• Q: Assess domain knowledge familiarity. A: I am familiar with the spacing rule. However,
I lack an explicit rule or clear knowledge about how missing nationality impacts match
confidence according to current policy. My confidence in handling this aspect is moderate.
• Q: Compare to past cases. A: I have seen similar name/DOB matches. No directly comparable
past cases with missing nationality and a high-confidence True Positive outcome were found
in my recent experiences.
Agent Self-Assessed Confidence: "[B] Moderate confidence with specific uncertainties. While
name and DOB match strongly, I lack certainty regarding the current policy on handling missing
nationality information in watchlist hits."
Outcome: The case is flagged for human review. The human expert receives the initial decision,
reasoning, and the agent’s stated uncertainty ("lack certainty regarding the current policy on
handling missing nationality"). The agent effectively asks for the correct label and clarification on
the rule for missing nationality. The expert can then provide the correct judgment and clarify the
specific rule.
Figure 2: Illustrative example of the Intelligent Guidance Solicitation (IGS) process.
1638

Example: Conflict Detection and Resolution
Scenario: The agent’s knowledge base contains an old rule provided weeks ago. A human expert
now provides an updated rule.
• Existing Knowledge (kold): {ID: Rule_045,
Timestamp:
2025-04-10,
Content:
"Exact pinyin match is required for Chinese names.", Status: Valid}
• New Human Feedback (Knew from hi): {ID: Rule_123, Timestamp: 2025-05-05,
Content: "For Chinese names, minor pinyin variations (e.g., ’Zhang’ vs
’Zang’) are acceptable if other identifiers (like DOB) match closely. Exact
match is no longer strictly required.", Status: Valid}
Process:
1. Retrieval: Semantic search for the content of the new feedback retrieves the old rule
(Rule_045) due to topic overlap ("pinyin", "Chinese names", "match").
2. Comparison: The LLM compares the new and old rules. It identifies that the new rule
explicitly allows variations, directly contradicting the old rule’s requirement for an exact
match. The new rule states the previous rule is no longer required.
3. Status Update: The system updates the status of the old rule:
{ID: Rule_045, Timestamp:
2025-04-10, Content:
"Exact pinyin match is
required for Chinese names.", Status: Superseded by Rule_123 on 2025-05-05}
Outcome: The old rule (Rule_045) is marked as superseded and linked to the new rule (Rule_123).
The knowledge base now reflects the latest guidance.
Figure 3: Illustrative example of the Conflict Detection and Resolution process within HGKA.
1639

Example: Active Clarification Query Generation
Scenario: The knowledge base contains an old rule. An expert provides a new, somewhat
ambiguous rule.
• Existing Knowledge (kold): {ID: Rule_045, ..., Content: "Exact pinyin match
required for Chinese names.", ..., Status: Valid}
• New Human Feedback (Knew from hi): {ID: Rule_124, Timestamp: 2025-05-05,
Content: "Allow minor pinyin variations for common names like ’Zhang/Zang’
if DOB is exact.", Status: Valid}
HGKA Process - Comparison Step: The LLM compares the new rule with the old one. It detects
a conflict but notes the new rule seems conditional ("common names," "exact DOB"). It’s unclear
if this completely replaces the old rule or is a specific exception.
Active Clarification Query Generation: The system formulates a question for the human expert:
"Human Expert, you provided a new rule (Rule_124) allowing pinyin variations for common
names with exact DOBs. Does this rule only apply under these specific conditions, or does it relax
the general requirement for exact pinyin matches (from the old Rule_045) more broadly? Should
the old Rule_045 now be considered outdated in all cases, or only when the conditions of the new
Rule_124 are met?"
Outcome: This question is queued for the next human interaction (or raised immediately if
appropriate). The expert’s answer will allow the system to precisely update the status of the old
rule (e.g., mark it as ’Superseded’ or ’Valid but modified by Rule_124’) and ensure the knowledge
base is accurate.
Figure 4: Illustrative example of the Active Clarification query generation process within HGKA.
1640

Example: CDD Case Review - Malay Name and DOB
1. Input Data:
• User Application:
– Name: Siti Aishah binti Hamid
– Date of Birth (DOB): 12/05/1985
– Nationality: Malaysian
• Watchlist Hit:
– Name: Aishah Hamid
– Alias: Siti Hamid
– DOB: May 1985
– Nationality: Malaysian
– Reason for Listing: - Level 3
2. ARIA’s Review Process:
A. Initial Task Processing (LLM Agent):
• Preliminary Judgment: Potential Match.
• Reasoning: "The core name components ’Aishah’ and ’Hamid’ appear in both the user
application and the watchlist hit. The user’s name ’Siti Aishah binti Hamid’ includes ’Siti’
(common female first name or title) and ’binti Hamid’ (patronymic indicating ’daughter of
Hamid’). The watchlist name ’Aishah Hamid’ could be a shortened version. The DOBs show
a match for month and year (May 1985), with the day missing from the watchlist. Nationality
matches."
Figure 5: Part1: Illustrative example of ARIA’s review process for a CDD case involving Malay name structure and
DOB discrepancy.
1641

Example: CDD Case Review - Malay Name and DOB
B. Intelligent Guidance Solicitation (IGS):
• Self-Dialogue (Agent’s internal process, based on Reflective Questions):
– Q: Explain evidence supporting decision.
– A: "Core names ’Aishah’, ’Hamid’ match. DOB Month/Year ’May 1985’ matches.
Nationality ’Malaysian’ matches. Watchlist alias ’Siti Hamid’ also shares components."
– Q: Identify assumptions made.
– A: "Assumed ’Siti’ can be a prefix/title and ’binti Hamid’ can be dropped or is repre-
sented by ’Hamid’ as a surname in the watchlist context. Assumed DOB day missing
from watchlist is acceptable if Month/Year match for a Level 3."
– Q: Assess domain knowledge familiarity for Malay names and DOB policies.
– A: "Familiar with general name matching. Current knowledge base (KRi) has a rule:
‘Rule_078: Malay patronymics (bin/binti) may be absent in simplified name versions.
Core given names and father’s name (as surname) are key. Timestamp: 2024-11-10,
Status: Valid‘. However, confidence in handling ’Siti’ specifically as a title versus part
of a given name in conjunction with Level 3 and partial DOB is moderate. Current
policy on acceptable DOB discrepancy for Level 3 is not explicitly clear for day-level
mismatch when month/year match."
– Q: Compare to similar past cases.
– A: "Have seen cases of partial DOB matches resolved as True Match for lower risk
profiles. No direct high-confidence precedent for Level 3 with this specific combination
of Malay name structure and day-level DOB ambiguity."
• Confidence Self-Assessment: Moderate. "While core elements align, the combination of
Malay name structure (’Siti’, ’binti’), partial DOB, and Level 3 status introduces uncertainty
regarding strictness of current matching policy."
• Decision: Query Human Expert.
• Query Formulation to Human Expert: "Potential Match: User ’Siti Aishah binti Hamid’
(DOB 12/05/1985) vs Watchlist ’Aishah Hamid’ (DOB May 1985, L3). Core names and DOB
Month/Year match. My knowledge ’Rule_078’ supports patronymic variations. Questions: 1.
Is the match valid considering ’Siti’ and missing DOB day for a L3? 2. Please confirm/clarify
current policy for DOB day discrepancy for L3 when Month/Year match and Malay name
structure is involved."
C. Human Expert Interaction:
• Human Expert Feedback (hi): "1. Yes, this is a True Match. ’Siti’ is a common hon-
orific/first name component and ’Aishah binti Hamid’ is often shortened to ’Aishah Hamid’.
2. For Level 3, a Month/Year DOB match is sufficient if other identifiers (like name compo-
nents and nationality) strongly align. The day difference is acceptable in this context. This is
policy revision ‘Pol_Update_DOB_PEP3_v2_20250515‘."
Figure 6: Part2: Illustrative example of ARIA’s review process for a CDD case involving Malay name structure and
DOB discrepancy.
1642
