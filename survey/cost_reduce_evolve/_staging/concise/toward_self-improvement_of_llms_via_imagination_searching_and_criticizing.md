Toward Self-Improvement of LLMs via Imagination,
Searching, and Criticizing
Ye Tian1,2∗, Baolin Peng1∗, Linfeng Song1∗, Lifeng Jin1, Dian Yu1, Lei Han2
Haitao Mi1†, Dong Yu1
1Tencent AI Lab, Bellevue, WA
2Tencent Robotics X
{baolinpeng,lfsong,lifengjin,yudian,haitaomi,dyu}@global.tencent.com
{yaptian,lxhan}@tencent.com
Abstract
Despite the impressive capabilities of Large Language Models (LLMs) on vari-
ous tasks, they still struggle with scenarios that involves complex reasoning and
planning. Self-correction and self-learning emerge as viable solutions, employing
strategies that allow LLMs to refine their outputs and learn from self-assessed
rewards. Yet, the efficacy of LLMs in self-refining its response, particularly in
complex reasoning and planning task, remains dubious. In this paper, we introduce
ALPHALLM for the self-improvements of LLMs, which integrates Monte Carlo
Tree Search (MCTS) with LLMs to establish a self-improving loop, thereby enhanc-
ing the capabilities of LLMs without additional annotations. Drawing inspiration
from the success of AlphaGo, ALPHALLM addresses the unique challenges of
combining MCTS with LLM for self-improvement, including data scarcity, the
vastness search spaces of language tasks, and the subjective nature of feedback
in language tasks. ALPHALLM is comprised of prompt synthesis component, an
efficient MCTS approach tailored for language tasks, and a trio of critic models for
precise feedback. Our experimental results in mathematical reasoning tasks demon-
strate that ALPHALLM significantly enhances the performance of LLMs without
additional annotations, showing the potential for self-improvement in LLMs. The
code is available at https://github.com/YeTianJHU/AlphaLLM.
1
Introduction
LLMs, trained on trillions of tokens with billions of parameters have shown unparalleled capabilities
in a wide range of natural language processing tasks (Touvron et al., 2023b; Team et al., 2023;
OpenAI, 2023). Nevertheless, they continue to face challenges in scenarios requiring complex
reasoning and strategic planning (Valmeekam et al., 2022; Stechly et al., 2024). While advanced
prompting approaches such as Chain, Tree, Graph-of-Thought (Wei et al., 2022; Yao et al., 2024;
Besta et al., 2024; Ding et al., 2023), it remains essential to fine-tune LLMs using a substantial
volume of high-quality, supervised data to fundamentally improve the model performance (Nye et al.,
2021; Lewkowycz et al., 2022; Chung et al., 2022). This methodology is inherently limited by the
scope and quality of data that humans can provide.
Considering these challenges, the concept of self-correction and self-learning have been proposed
as promising solutions (Madaan et al., 2024; Saunders et al., 2022; Chen et al., 2024). Within these
framework, LLMs typically operate by employing two main strategies: 1) they continuously refine
∗Equal Contribution; †Corresponding Author
38th Conference on Neural Information Processing Systems (NeurIPS 2024).

+1 Value Function
+1 Step Reward
Outcome Reward +1
Imagination
Searching
Criticizing
Improving
（𝑥!, 𝑦!）
Real Data
LLM
$𝑥!
$𝑥"
...
Synthesized 
Prompts 
+
Figure 1: Imagination-Searching-Criticizing self-improvement loop: Imagination component synthe-
sizes prompts as new learning examples, with MCTS searching better trajectories guided by signals
from critics for policy improving.
their responses based on the feedback of their past responses, and 2) they extensively sample responses
then learn from preferences judged by itself as reward models with PPO or DPO (Yuan et al., 2024a,b;
Chen et al., 2024). However, it remains a matter of ongoing research whether LLMs can effectively
critique their own outputs to either enhance response quality or apply a scalar reward to indicate the
quality of responses, especially in contexts demanding intricate planning and reasoning (Valmeekam
et al., 2022; Stechly et al., 2024; Huang et al., 2023; Hong et al., 2023). On the other hand, advanced
search algorithms such as MCTS, combined with reinforcement learning, have enabled models to
learn from self-play and achieve human parity or even surpass human performance in complex tasks
such as the game of Go (Silver et al., 2016, 2017). This naturally raises a question: is it viable to
leverage the strengths of MCTS alongside LLMs to inaugurate a novel paradigm of self-improving?
More precisely, could the assimilation of MCTS empower LLMs to more effectively explore better
responses, guided by strategic 