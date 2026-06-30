Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers), pages 27890–27913
July 27 - August 1, 2025 ©2025 Association for Computational Linguistics
G¨odel Agent: A Self-Referential Agent Framework for Recursively
Self-Improvement
Xunjian Yin♠, Xinyi Wang♣, Liangming Pan♢, Li Lin♠
Xiaojun Wan♠, William Yang Wang♣
♠Peking University
♣University of California, Santa Barbara
♢University of Arizona
{xjyin,wanxiaojun}@pku.edu.cn
william@cs.ucsb.edu
Abstract
The rapid advancement of large language mod-
els (LLMs) has significantly enhanced the ca-
pabilities of agents across various tasks. How-
ever, existing agentic systems, whether based
on fixed pipeline algorithms or pre-defined
meta-learning frameworks, cannot search the
whole agent design space due to the restric-
tion of human-designed components, and thus
might miss the more optimal agent design. In
this paper, we introduce G¨odel Agent, a self-
evolving framework inspired by the G¨odel ma-
chine, enabling agents to recursively improve
themselves without relying on predefined rou-
tines or fixed optimization algorithms. G¨odel
Agent leverages LLMs to dynamically modify
its own logic and behavior, guided solely by
high-level objectives through prompting. Ex-
perimental results on multiple domains demon-
strate that implementation of G¨odel Agent can
achieve continuous self-improvement, surpass-
ing manually crafted agents in performance,
efficiency, and generalizability.
1
Introduction
As large language models (LLMs) (OpenAI et al.,
2024; Dubey et al., 2024) demonstrate increasingly
strong reasoning and planning capabilities, LLM-
driven agentic systems have achieved remarkable
performance in a wide range of tasks (Wang et al.,
2024a). Substantial effort has been invested in
manually designing sophisticated agentic systems
using human priors in different application areas.
Recently, there has been a significant interest in
creating self-evolving agents, that not only greatly
reduce human labor but also produce better solu-
tions. Given that human effort can only cover a
small search space of agent design, it is reason-
able to expect that a self-evolving agent with the
freedom to explore the full design space has the
potential to produce a more optimal solution.
There is a large body of work proposing agents
capable of self-refinement. Some agents are de-
LLM
Decision-making Module
Sensor
Logic
Executor
Environment
Feedback
Action
Interaction
Agent
Self
Aware
Self
Modify
Figure 1: Modular demonstration of G¨odel Agent. Com-
pared with traditional agents, its sensor and executor
can read and write all of its own code.
signed to iterate over a fixed routine consisting of
a list of fixed modules, while some of the modules
are capable of taking self- or environment feedback
to refine their actions (Chen et al., 2023b; Qu et al.,
2024a; Tang et al., 2025). This type of agent, re-
ferred to as Hand-Designed Agent, is depicted as
having the lowest degree of freedom in Figure 2.
More automated agents have been designed to be
able to update their routines or modules in some
pre-defined meta-learning routine, for example, nat-
ural language gradients (Zhou et al., 2024), meta
agent (Hu et al., 2024), or creating and collecting
demonstrations (Khattab et al., 2023). This type
of agent, known as Meta-Learning Optimized
Agents, is depicted as having the middle degree of
freedom in Figure 2. However, there are inevitably
some human priors involved in these agent designs
that cannot be improved during the inference time.
In this paper, we propose G¨odel Agent to elimi-
nate the human design prior, which is an automated
LLM agent that can freely decide its own routine,
modules, and even the way to update them. It
is inspired by the self-referential G¨odel machine
(Schmidhuber, 2003), which was proven to be able
to find the global optimal solutions. Self-reference
means the property of a system that can analyze and
27890

Rebuttal
Design
Increasing degrees of freedom; Decreasing manual design; Fewer constraints and bottlenecks
Learnable
Fixed
Expert
Agent
Meta Agent
Feedback
Implementation
Design
Design
Draft
Review
…
Draft
Review
Draft
Review
Prompt:
Improve it
Prompt:
Check and Improve it
Verify
...
Hand-designed Agent
Meta-Learning Optimized Agent
Self-Referential Agent
Draft
Review
...
Improve
Recursively
Prompt:
Improve it
Figure 2: Comparison of three agent paradigms. Hand-designed agents rely on human expertise which are 