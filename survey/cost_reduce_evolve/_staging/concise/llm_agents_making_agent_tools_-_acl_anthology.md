Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers), pages 26092–26130
July 27 - August 1, 2025 ©2025 Association for Computational Linguistics
LLM Agents Making Agent Tools
Georg Wölflein1,2,3,†
Dyke Ferber3,4,‡
Daniel Truhn5
Ognjen Arandjelovi´c2
Jakob N. Kather1,4,6
1EKFZ for Digital Health, TU Dresden
2University of St Andrews
3Synagen AI
4NCT, Heidelberg University Hospital
5University Hospital Aachen
6University Hospital Dresden
Correspondence: georg@woelflein.de
Abstract
Tool use has turned large language models
(LLMs) into powerful agents that can perform
complex multi-step tasks by dynamically util-
ising external software components. However,
these tools must be implemented in advance
by human developers, hindering the applic-
ability of LLM agents in domains demand-
ing large numbers of highly specialised tools,
like in life sciences and medicine. Motivated
by the growing trend of scientific studies ac-
companied by public code repositories, we
propose TOOLMAKER, an agentic framework
that autonomously transforms papers with code
into LLM-compatible tools. Given a GitHub
URL and short task description, TOOLMAKER
autonomously installs dependencies and gener-
ates code to perform the task, using a closed-
loop self-correction mechanism for debugging.
To evaluate our approach, we introduce a bench-
mark comprising 15 complex computational
tasks spanning various domains with over 100
unit tests to assess correctness and robust-
ness. Our method correctly implements 80% of
the tasks, substantially outperforming current
state-of-the-art software engineering agents.
TOOLMAKER therefore is a step towards fully
autonomous agent-based scientific workflows1.
1
Introduction
Scientific discovery is the foundation for innova-
tion and progress. Traditionally, the underlying
research processes that guarantee progress have
been entirely reliant on human expertise, involving
the formulation of ideas and hypotheses, the collec-
tion of information and analysis of data, the plan-
ning and execution of experiments, and iterative
refinement to arrive at a solution. With the recent
development of autonomous agents that employ
†Work done while at EKFZ for Digital Health, TU Dresden
and University of St Andrews. ‡Work done while at EKFZ for
Digital Health, TU Dresden and NCT Heidelberg.
1Our code and benchmark are publicly available at
https://github.com/KatherLab/ToolMaker.
Figure 1: We envision a future where agents posess
dynamic toolsets that can be expanded at runtime. Tool
creation, studied here, is a crucial step towards this goal.
LLMs to perform tasks through multi-step reason-
ing and planning, and by utilising tools (external
pieces of software that the model can execute), we
are at the cusp of a paradigm shift where artificial
intelligence (AI) can assist throughout entire re-
search projects as a virtual scientist (Fig. 1), rather
than being limited to addressing narrowly and a
priori defined problems.
Although LLM agents have shown success for
specific tasks in domains such as software engineer-
ing (Wang et al., 2024; Yang et al., 2024), health-
care (Ferber et al., 2024; Kim et al., 2024), law (Li
et al., 2024), and scientific research (Swanson et al.,
2024; Gao et al., 2024; Schmidgall et al., 2025),
they struggle to generalise to broader classes of
tasks. This limitation arises from their reliance on
tools that must be explicitly designed, implemen-
ted, and integrated by human developers – often
requiring extensive technical expertise – before de-
ployment (Ferber et al., 2024; Jimenez et al., 2024).
While AI assistants can support this process, cur-
rent systems still depend heavily on manual inter-
vention to ensure compatibility and functionality.
To address this, some agentic frameworks have
been designed that autonomously craft their own
tools (Cai et al., 2024; Yuan et al., 2024; Qian et al.,
26092

2023).
However, because these methods build
each tool from scratch, they inevitably produce
simple, narrowly scoped tools tailored to single-
dimensional problems – an approach ill-suited to
the complexity of real-world research problems.
In fact, in critical fields such as healthcare, data
necessary to build tools from scratch is often in-
accessible due to privacy restrictions, preventing
agents from using it to build their own solutions.
Moreover, the complexity of modern scientific
tools has increased substantially in terms of compu-
tational requi