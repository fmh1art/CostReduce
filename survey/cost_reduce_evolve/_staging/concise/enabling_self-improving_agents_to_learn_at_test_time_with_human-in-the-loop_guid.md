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
where human exp