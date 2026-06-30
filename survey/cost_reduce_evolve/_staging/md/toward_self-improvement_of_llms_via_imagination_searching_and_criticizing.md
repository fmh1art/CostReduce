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
responses, guided by strategic signals, and subsequently optimize these responses to enhance overall
performance?
To answer this question, we begin with a systematic examination of AlphaGo, identifying three
critical aspects for its success: (i) The large volume of data, including self-play data. (ii) The
use of tree search, which facilitates the exploration of potential moves through statistical sampling
of the large search space. (iii) Accurate and unambiguous environment feedback; the direct and
accurate feedback (win or loss) provided by the game of Go offers a clear and unequivocal learning
signal (Silver et al., 2017). The integration of MCTS with LLMs for self-improvement has several
challenges: (i) Limited Data: High-quality annotated data for LLMs is generally scarce. Furthermore,
how to construct of synthetic data for LLMs training, similar to AlphaGo’s self-play data, remains
unclear. (ii) Search Efficiency: The vast number of potential token combinations in natural language
tasks results in an exponentially large search space, posing a significant challenge to the efficiency of
MCTS (Ramamurthy et al., 2022). (iii) Imperfect Feedback: In contrast to the clear win/loss feedback
in Go, feedback in natural language tasks is often subjective and nuanced, without a straightforward
measure of success.
In this paper, we introduce ALPHALLM, an imagination-searching-criticizing framework designed
for the self-improvement of LLMs . ALPHALLM consists of three key components, as illustrated
in Figure 1. First, an imagination component is designed to synthesize prompts, alleviating the
issues of data scarcity. Second, we propose ηMCTS tailored for efficient searching in language tasks.
Particularly, it has been show that planning at multiple levels of temporal abstraction is critical for RL
problems with a long horizon and large action space (Sutton et al., 1999b; Peng et al., 2017; Luketina
et al., 2019). As such, we propose formulating the text generation process as options over a Markov
Decision Process (MDP) problem, where each option represents the generation of a collection of
tokens for a specific subtask, similar to the concept of chains in chain-of-thought prompting. This
formulation improves search efficiency by substantially reducing the search depth. Additionally, we
propose the use of state merge and adaptive branching factors to further enhance search efficiency by
balancing the trade-off between search width and depth. Lastly, since accurate feedback is crucial
to the success of MCTS, we introduce a trio of critic models to guide ηMCTS, including a value
function for estimating expected rewards, a process reward model for assessing node correctness,
and an outcome reward model for evaluating the overall trajectory. For complex tasks with which
LLMs struggle assessing such as arithmetic computation and code execution, to ensure the accuracy
2

of feedback, we augment the critics with the capacity to make dynamic decisions on which tools to
use, when to use them, and how to use them effectively. After ηMCTS stage, we collect the trajectory
with the largest reward from the critic models as the training examples to improve LLMs.
The experimental results on mathematical reasoning tasks demonstrate that ALPHALLM can effi-
ciently search for better responses and use them to improve LLMs’ performance, forming an effective
self-improving loop. Notably, based on Llama-2-70b and WizardMath-70B-V1.0, ALPHALLM can
improve its performance from 57.8 to 92.0 on GSM8K and from 20.7 to 51.0 on MATH, performing
comparably to GPT-4.
2
Related Work
Search with LLM
Effective search strategy has been shown crucial for tasks that involve complex
reasoning and planning, such as go (Silver et al., 2016) and math reasoning (Cobbe et al., 2021;
Hendrycks et al., 2021). For math reasoning tasks, various search methods have been studied. One
direction of research (Zhu et al., 2024; Xie et al., 2024) designed beam search with dynamic pruning,
where beam items of low quality are pruned. Another line of work (Yao et al., 2024; Long, 2023;
Besta et al., 2024; Hao et al., 2023; Feng et al., 2023) maintains a tree or a graph that represents the
current progress of solving the input question where potential branches are iteratively expanded. Both
our approach and Feng et al. (2023) are based on the MCTS algorithm, while one main difference is
how to define a search step: Feng et al. (2023) fix a search step to be either a token or a sentence,
while our approach is more flexible on deciding steps. We have also carefully designed the MCTS
process, incorporating multiple critique signals to guide the search more effectively and introducing
adaptive search parameters for improved state exploration. As the result, our approach achieves much
better performances.
LLM Self-improving
Being a key to the success of scalable oversight (Bowman et al., 2022),
self-improving for LLM aims to align the LLM to human preference and values mainly using the
supervision from the knowledge inside the LLM (Zelikman et al., 2022, 2024). One crucial part of
self-improving is how to obtain reliable signal of critique to distinguish between good responses
from the LLM and bad ones. Initial work (Bai et al., 2022; Wang et al., 2022) first asks the LLM to
generate input queries of diverse tasks and the corresponding outputs. They then rely on hand-crafted
heuristic rules to filter out redundant or low-quality data pairs (e.g. the query is too long or too
short). Since it is non-trivial to compose effective heuristic rule, later work (Sun et al., 2023; Li et al.,
2023; Guo et al., 2024) proposes a few general principles or judging criteria and ask the LLM itself
to evaluate the quality its responses based on these guidance, hoping that LLMs can automatically
designate these principles into each data point to better guide data filtering. However, this requires
LLMs to have strong abilities to apply these principles for each specific case and make correct
judgements. Different from previous work, we propose to leverage the supervision from MCTS for
LLM self-improvement: taking the outputs of MCTS to continue train the LLM. This is because the
outputs from MCTS are usually in much better quality then standard nucleus sampling, and the large
gap ensure that the LLM can self improve.
3
Preliminaries
3.1
Problem Formulation
In this paper, we consider a LLM characterized by probability pθ and denoted as policy πθ. It takes a
sequence x = [x1, · · · , xn] as input, which is typically referred as prompt, to generate the response
y = [y1, · · · , ym]. In the context of LLMs, each xi and yi represents a token from a pre-defined
vocabulary. The policy πθ operates in an autoregressive manner, where each token is generated
sequentially, relying solely on the context provided by the previously generated tokens. The policy
therefore constitutes a Markov process in which the conditional probability distribution pθ(y|x) can
be decomposed and expressed with the chain rule as pθ(y|x) = Qm
i=1 pθ(yi|x, y<i).
With this property, the text generation task can be formulated as an Markov Decision Process (MDP)
problem consisting of (S, A, T, R, γ) in which, st ∈S represents the context information of current
trajectory, i.e., current status of the generation process, e.g., a partial response to a prompt; at ∈A
denotes a single action or sampled token from the vocabulary, leading to a transition to a new state
3

st+1, by concatenating st and at; rt = R(st, at) manifest the evaluation of the generation to the
prompt, reflecting the desirability or preferences of each state-action pair.
This MDP framework sets the stage for applying Reinforcement Learning (RL) methods to optimize
the policy πθ aiming to maximize the expected cumulative reward R. Base on these setups, we
describe the self-improving problem. Given a LLM πθ and an initial dataset D0, which consists
of N expert-generated prompt-response pairs {(x0
i , y0
i ) | i ∈[N]}, the goal of self-improving is
to iteratively refine πθ to maximize the reward. The refinement process includes learning from
synthesized prompts and corresponding responses. These responses are obtained using an advanced
search algorithm that navigates the space of possible responses to maximize the expected reward.
The detailed process is described in Algorithm 1 in Appendix. The primary challenges in forming an
effective self-improving loop lie in synthesizing suitable prompts, efficiently searching over a vast
action space, and obtaining precise feedback, which will be discussed in §4.
3.2
Monte Carlo Tree Search
MCTS is a sampling-based search algorithm for policy optimization in decision-making problems. It
would iteratively build a search tree, by repeating four phases: selection, expansion, evaluation, and
backpropagation. In the selection phase, it would recursively select the children from the root node
by Upper Confidence Bound (UCB) (Auer et al., 2002), UCB(i) = wi + C ∗
q
2 ∗ln Ni
ni , where ni
and Ni are the visit counts for the node i and its parent respectively, C represents a hyperparameter
balancing exploration and exploitation, and the wi is the average value of all descendant nodes of i.
4
ALPHALLM
4.1
Overview
The architecture of ALPHALLM is depicted in Figure 1, comprising three key components. Firstly,
the imagination component is tasked with synthesizing prompts as learning examples. Secondly,
an efficient search component, named ηMCTS, is proposed to search high-quality trajectories for
optimizing the policy. Lastly, the search process is guided by critics specifically designed to provide
reliable signals.
4.2
Data Synthesizing
Let D0 = {(xi, yi) | i ∈[N]} denote the initial dataset consisting of N expert-generated prompt-
response pairs. The data synthesizing process aims to expand this dataset by generating a set of
synthesized prompts D1 = {(x1
i , · · · ) | i ∈[N]}. The generation of each synthesized prompt x1
i
can be mathematically described as a transformation g applied to one or more examples from D0,
x1
i = g(x0
i1, · · · , x0
im, π0) where x0
i1, · · · , x0
im are selected examples from D0. The transformation
function g controls the synthesis process, which can be a learnable function, manually defined heuristic
rules, a strong LLM or the policy model itself π0 equipped with data synthesis instructions. The data
synthesizing process aims to enrich the diversity and complexity presented for the training of the
policy model. Among various strategies, such as Self-instruct (Wang et al., 2022), Evol-instruct (Xu
et al., 2023), we opt for a method akin to that described in Yu et al. (2023).
4.3
ηMCTS
4.3.1
Option-level MCTS
When applying MCTS to LLMs, it is natural to perform token-level search, where each token is
considered as an action (Liu et al., 2023). However, the substantial vocabulary size typical of
LLMs presents a significant challenge i.e., conducting a deep search in such a vast space becomes
increasingly complex as the search space expands exponentially. To mitigate this, some efforts
proposed a sentence-level search, treating each sentence or step as a search node (Feng et al., 2023).
While this method reduces the search space, it might compromise the flexibility and effectiveness
of applying MCTS to LLMs, which is particularly true for tasks where subtle variations in token
can dramatically impact the outcome, or where a more comprehensive search beyond a sentence is
necessary.
4

Search Node
Example
Termination
Token-level
y0 →y1 →y2 →y3 →y5 →y6 →y7 →y8
token
Sentence-level
y0y1y2
→y4y5y6
→y7y8y9y10
new line
Option-level
y0 →y1y2
→y4y5y6
y7y8y9
→y10
termination function
Table 1: Comparative illustration of token-level, sentence-level, and option-level MCTS search nodes.
y denotes a token sampled from the policy model. The arrow →represents the transition from one
search node to the subsequent node within the search process.
Inspired by Sutton et al. (1999a); De Waard et al. (2016), we use the term option as a search node
and propose option-level MCTS where each option represents a sequence of tokens, which can
range from multiple tokens to several sentences. A comparisons of different levels search is listed
in Table 1. Mathematically, an option o = ⟨I, π, β⟩, where I ⊆S is a set of initial states for
the option; π : S × A →[0, 1] is a policy to generate actions, which in our case is a LLM; and
β : S+ →[0, 1] is the termination function. Starting from a state st, we can choose all the options
for which st ∈I. Once an option is chosen, the policy π will generate actions for several steps until
the option terminates according to the termination function β. The option-level MCTS consists of
stages including selection, expansion, simulation, and backpropagation. The option-level formulation
offers more flexibility compared to the sentence-level, as a new line can be treated as a special case
of the termination function, as demonstrated in Table 1. Additional detailed steps of the option-level
MCTS can be found in Appendix A.2.
4.3.2
Importance-Based Adaptive Branching
In previous works related to option/sentence level tree search (Feng et al., 2023; Yao et al., 2024),
it was a common practice to assume that each node in the tree has the same predefined width, i.e.,
branching factor. This assumption was due to the fact that unlike token-level MCTS with a limited
action space, the sample space at the option-level is exceedingly large, with an unlimited number of
token combinations. As a result, it was necessary to set a predefined maximum width for each node.
However, this predefined branching factor is hard to set, as an improper choice can lead to a search
tree that is either too shallow or too thin, resulting in an inefficient exploration of the search space.
To quantify the error induced by the branching factor limit, we defined the branching error Eϕ(t). For
a node t with a branching factor of mt, it aims to use the mt child options oi
t ∼Dchildren
t
(where
i ∈{1, . . . , mt}) to represent all possible options. Consequently, for a legal option oj
t ∼π(st)
from the option space, we can calculate the minimal value difference between it and the mt existing
options, which captures the error associated with representing other possible options using the mt
available options. It can be formulated as Eϕ(t) = Eoj
t∼π(st)[minoi
t |vπ
ϕ([st, oj
t]) −vπ
ϕ([st, oi
t])|],
where vπ
ϕ is the value function which will be detailed in §4.4. Here we define the importance of
node st as I(st) = maxoi
t |vπ
ϕ([st, oi
t]) −vπ
ϕ(st)|. For simplicity, we assume that the value of the
children nodes are uniformly distributed (a detailed analysis of the Gaussian distribution can be found
in Appendix A.4). Under this assumption, we show in Appendix A.3 that Eϕ(t) ≤I(st)
mt−1. While Eϕ
is less than some ϵ, we aim to use a smaller total number of nodes for efficiency.
Theorem 4.1. The optimal branching factor mt in a tree search is set such that mt−1 is proportional
to the node importance I(st), under the condition I(st)
mt−1 ≤ϵ. Refer to Appendix A.3 for the detailed
proof.
A similar concept has also been proposed in Taylor et al. (2014); Clouse (1996). Intuitively,
I(st) captures the maximum value deviation from the current state. When this value is small,
there is no need to explore further on this node, as there will not be a significant difference by
rolling out on this node. Conversely, if the value is large, it is worth trying different children.
We set the number of children allowed for a node n(st) (after extracting 1) to be linear with this
importance, using a factor α. In practice, to avoid extreme cases of large variance of I(st) in the
early stage, we bound the number of children by depth-dependent constants cmin(t) and cmax(t),
n(st) = max (cmin(t), min (⌊αI(st)⌋+ 1, cmax(t))) .
5

4.3.3
State Merge
With n(st) determined, another issue is that options under the same node may be very similar, leading
to many unnecessary sub-trees. Since we cannot directly control the ot ∼π(st), one strategy to
mitigate this issue is to utilize the concept of move groups, as discussed in Van Eyck & Müller
(2012). By merging similar nodes into the same group, we can increase the diversity among groups,
thereby covering a larger problem space with limited search rollouts and making the search process
more efficient.
Here, we adapt the definition of node predicate pvM from Abel et al. (2018) and Fu et al. (2024) to
represent whether two nodes are extremely similar. In practice, each time we generate a new option
from the policy, we use heuristic functions as pvM to check its similarity with all existing groups. The
heuristic function can either be a faster rule-based measurement (e.g., edit distance) or a model-based
method (e.g., prompting a language model). Based on this, we decide whether to merge this option
with a previous one or create a new group.
4.3.4
Fast Rollout with Specialized LM
The simulation operation which employs a rollout policy to project future trajectories from a given
state, is crucial for an effective MCTS. This process significantly improves the efficiency of explo-
ration and exploitation, and enhances the accuracy of reward estimation2. Estimations made at the
end of trajectories tend to have lower bias but higher variance; thus, simulating multiple possible
trajectories yields low-bias, low-variance estimates, enabling a more informed and effective search
process. Ideally, πθ would serve as the rollout policy, yet its computational demands render it imprac-
tical for the rapid simulations required by MCTS. To address this challenge, we propose the use of a
smaller, specialized LM as the fast rollout policy πfast. Given a state st, the fast rollout policy πfast
efficiently continues generation until it reaches a termination condition, denoted as πfast(st).
4.4
Critic
In ALPHALLM, we design three types of critic models to guide the search process.
Value Function
The value function, denoted as vπ(s), represents the expected return starting
from state s and following policy π thereafter, given by vπ(s) = Eτ∼π[R(τ)|s0 = s] where R(τ)
represents the discounted return of trajectory τ. To train a parameterized value function vπ
ϕ(s),
given the prompts D = {(xi, · · · ) | i ∈[N]}, for each prompt xi, we generate multiple trajectories
τ j
i = {xi, oj
i1, oj
i2, · · · , oj
iT } by following policy π for J times. A final reward rj
i is assigned to
indicate whether τ j
i aligns with yi—for example, rewarding trajectories that contain correct answers
in mathematical tasks or closely follow instructions as ground truth. We then construct a dataset
Dvalue = {(sj
it, vj
it) | i ∈[N], t ∈[T], j ∈[J]} where sj
it = [xi · oj
<it] and vj
it = rj
i . The value
function vπ
ϕ is optimized by minimizing the mean squared error: Lϕ = −E(s,v)∼Dvalue(vπ
ϕ(s) −v)2.
Similar to (Feng et al., 2023), vπ
ϕ is a LLM with an MLP layer on top to output a scalar on each
token, using the scalar prediction at the last token of each state as the value.
PRM
The value function often struggles with credit assignment problem (Sutton, 1984) and its
learning could be inefficient due to delayed and sparse rewards (Sutton & Barto, 2018). Therefore,
we propose to incorporate PRM that introduces process supervision (Lightman et al., 2023) for direct
option assessment. PRM generates intrinsic rewards (Chentanez et al., 2004) to encourage explorations
of advantageous options, effectively mitigating issues of reward sparsity by providing immediate,
action-specific rewards. Given a state st and an option ot at time t, the PRM aims to predict the
immediate reward rPRM
t
that results from taking option ot in state st. Formally, the PRM is a function
R(st, ot) →rPRM
t
. While PRM ideally requires quality labels for each state (Uesato et al., 2022), due
to the high cost and time involved in obtaining these, MC estimation with prefix sampling (Wang
et al., 2023) is used as a proxy, which aligns with the objective of the value function. Instead
of adding a MLP layer on top of the policy model for outputting a scalar reward (Ouyang et al.,
2022), we formulate PRM as a text generation task to best leverage LLM’s intrinsic knowledge
2Typically, the closer the simulation is to the termination state, the more accurate the reward estimation
becomes.
6

for assessing the quality of an option. We adapt the dataset constructed for the value function as
DPRM = {(sit, ot, rPRM
t
)|i ∈[N], t ∈[T]} where rPRM
t
is the textual description of the reward, e.g., an
option can be regarded as good if vit is larger than certain threshold. To train PRM, we initialize it
from the policy model π and use the following prompt templates and typical language model loss.
The prompt template is shown in Appendix A.5.
ORM
In additional to the value function and PRM, ORM is also used to guide MCTS. ORM is designed
to evaluate options sequences in their entirety, assessing the extent to which the complete trajectory
aligns with the desired end goal (Uesato et al., 2022; Lightman et al., 2023; Wang et al., 2023;
Feng et al., 2023). The outcome evaluation complements value function and PRM by offering a
comprehensive assessment of trajectories. Crucially, ORM plays a vital role in the simulation stage of
MCTS by providing more accurate signals on the terminal state, which in turn facilitates a more
balance between exploration and exploitation strategies. ORM is formulated as a text generation
task, similar to PRM. We leverage the same dataset for the value function training and construct
DORM = {(xi, oi
1:T , rORM
i
)|i ∈[N]}, where each instance includes a initial state or prompt xi, a
sequence of actions or options oi
1:T taken from that state, and a textual reward rORM
i
indicating the
sequence’s success or quality. Similarly, ORM is initialized from the policy model π and the following
prompt templates and language model loss are used for training. The prompt template is shown in
Appendix A.5.
The final score evaluation of a state s is a weighted sum of the value function, PRM, and ORM:
s(s) = βvalue · vπ
ϕ(s) + βPRM · PRM(s) + βORM · Eτ∼πfast(s)[ORM(τ)], where τ ∼πfast(s) represents
trajectories starting from s under πfast, and βvalue, βPRM, βORM are hyperparameters. In practice, we
found that the value function model has better precision and calibration, while PRM has superior recall
(Appendix A.10). Although ORM with fast rollouts provides low-bias, low-variance estimates, it still
inherits some bias from πfast. Thus, combining these critics yields a stronger evaluation signal.
4.5
Policy Self-Improvement
The policy improvement an iterative process with each iteration containing two main steps: data
generation and policy finetuning.
Data generation
In this step, we assume to have the current policy πθk and synthetic prompts
Dk = {xk
1, . . . } at the k-th round, where each xk
1 represents a question. We obtain the corresponding
training data Dk for policy πθk by firstly performing ηMCTS on Dk (§4.3) and then sampling a
trajectory yk
i from the corresponding tree for each question xk
i . Here we choose the trajectory that
yield the highest critic score on the leaf node for each input question. Next, we filter out instances
where the corresponding trajectory is substandard forming Dk = {(xk
i , yk
i ) | f(xk
i , yk
i ) > γ} where
f represents a function for quality scoring, and γ indicates a threshold. There can be several ways to
implement the function, and here we simply use the ORM (§4.4).
Policy finetuning
With the obtained training data Dk, we organize the data into the prompt
templates shown in Appendix A.5. Then the policy πθk is finetuned using target-loss: Lθk =
E(xk
i ,yk
i )∼Dk

log πθk(yk
i |xk
i )

, resulting in an updated policy πθk+1. We leave other training meth-
ods, such as DPO (Rafailov et al., 2023) or PPO (Schulman et al., 2017) in future work.
5
Experiments
5.1
Experiment Setups
ALPHALLM is generally applicable to a wide spectrum tasks. As an early exploration, in this paper,
we conduct experiments on mathematical reasoning problems where the learning signals are clear
to define i.e., , final answer is correct or wrong. We choose to evaluate on two widely used datasets
GSM8K (Cobbe et al., 2021) and MATH (Hendrycks et al., 2021). For GSM8K, we utilize the whole
test set while for MATH, due to computation constraints, we utilize a subset following the same
procedure of Lightman et al. (2023). We evaluate the performance of predicting answers correctly for
policy models. In addition, we calculate the average rollouts, represented by the number of nodes in
7

the tree, as a measure of computational efficiency. We compare the performance of ALPHALLM
with a suite of proprietary model, including OpenAI’s GPT-4 and GPT-3.5, Anthropic’s Claude-2, as
well as Google’s PaLM-2 and the gemini model family. To ensure a fair and consistent evaluation, we
employ CoT as our primary prompting method. Additionally, we conduct comparisons with strong
open-source models, including Llama-2-70b (Touvron et al., 2023a) and WizardMath-70B-V1.0 (Luo
et al., 2023).
We select Llama-2-70b as the policy model for the GSM8K dataset and WizardMath-70B-V1.0 for
the MATH dataset. To construct the training dataset for the value function, PRM and ORM, we generate
50 trajectories for each prompt and construct the training target following Section 4.4. Both PRM
and ORM are initialized using the weights from the policy model, while the value function uses a
smaller Llama-2-13b model, as we observed no performance gains from increasing the value function
model size. In the design of ORM, tool usage is not incorporated for GSM8K. However, for MATH,
we enhance ORM by incorporating tools like python sympy to assess the quality of a trajectory, in a
manner similar to that described by Gou et al. (2023). The training employ a learning rate of 1e-6 and
are trained for one epoch. For the fast rollout policy model, we opt for the Abel-002-7B model (Chern
et al., 2023) for both the GSM8K and MATH tasks for its high efficiency and superior performance.
For the MCTS parameters, they are configured at different scales, as shown in Appendix A.6. We set
βvalue, βPRM, and βORM all to 1.0.
For policy self-improving (§4.5), we train the policy model up to 3 epochs, setting batch size to
128, learning rate to 5 × 10−6 and minimal learning rate to 1 × 10−6. Linear warm-up and decay
is used with warm-up percent to be 10%. We perform early stopping based on a devset held out
from the training instances. For GSM8K experiments, we perform two rounds of self-improving,
synthesizing 6.4k and 7.9k prompts(Yu et al., 2023) respectively to obtain the corresponding MCTS
outputs for training. For MATH experiments, we only perform one round of self-improving due to
limited computation resources, and 5.9k prompts are synthesized.
The termination function for options can be either be learned or rule-based. In practice, for the
GSM8K dataset, the termination condition occurs at the end of each line. This is based on the typical
structure of this dataset, where each line represents a distinct step or point. For the MATH dataset,
due to its complexity and the base model’s tendency to generate many \n\n line breaks with some
less meaningful content between them, termination occurs at the end of a line if a formula pattern
is detected. During inference, if \n\n is encountered, we perform a rule-based check for formula
patterns. It terminates if a pattern is found or continues generating until the next \n\n.
5.2
Results
Table 2 lists the performance comparisons of various methods on the GSM8K and MATH datasets.
Our findings reveal that ALPHALLM, based on Llama-2-70B and WizardMath-70B-V1.0, utilizes
only final answer annotations and continues to improve through training on responses from ηMCTS.
This comparison underscores the efficacy and broad applicability of our imagination-searching-
criticizing self-improving framework. Moreover, when our model is augmented with ηMCTS decoding
strategy, its performance markedly improves, achieving scores of 88.9 and 48.7 on the GSM8K and
MATH datasets, respectively. Following two iterations of self-improvement using synthetic prompts,
ALPHALLM demonstrates performance comparable to that of GPT-4. This suggests a viable
approach to improving LLMs’ capabilities in complex problem-solving tasks in a self-improving
fashion, leveraging a minimal amount of labeled data. We also analyze the performance of various
search methods in Appendix A.8.
5.3
Ablation Study
We assess the effectiveness of each component in ALPHALLM and report the results on GSM8K in
Table 3(a). Vanilla MCTS, configured with only the value function and a fixed number of children per
node, achieves an accuracy of 79.5%. This serves as a reference point for evaluating the incremental
benefits introduced by each additional component. The use of adaptive branching increae the accuracy
to 84.9%. The addition of PRM improves the accuracy modestly to 85.9%, showing the effectivenss of
process supervision for searching. A more significant improvement is observed with the introduction
of ORM with fast rollout, which boosts the accuracy to 86.5%. Integrating state merging results in
8

Model
Decoding
#Annotation
RN
FA
SYN
GSM8K
MATH
GPT-3.5
Sampling
-
-
-
-
80.8
35.5
GPT-4
Sampling
-
-
-
-
92.0
42.5
GPT-4 (PAL)
Sampling
-
-
-
-
94.2
51.8
Gemini 1.0 Pro
Sampling
-
-
-
-
77.9
32.6
Gemini 1.0 Ultra
Sampling
-
-
-
-
88.9
53.2
Gemini 1.5 Pro
Sampling
-
-
-
-
92.5
58.5
Claude-2
Sampling
-
-
-
-
85.2
32.5
PaLM-2 540B
Sampling
-
-
-
-
80.7
34.3
Llama-2-70b
Greedy
0
×
×
×
57.8
-
Llama-2-70b SFT
Greedy
7.5k
✓
✓
×
69.3
-
WizardMath-70B-V1.0
Greedy
96k
✓
✓
×
-
20.7
ALPHALLM
Greedy
7.5k/7.5k
×
✓
✓
73.7
23.6
ALPHALLM
ηMCTS
7.5k/7.5k
×
✓
×
88.9
48.7
ALPHALLM
ηMCTS
7.5k/7.5k
×
✓
✓
92.0
51.0
Table 2: Comparison results of ALPHALLM on the GSM8K and MATH datasets. #Annotation
indicates the quantity of labeled data employed for fine-tuning policy or training critic models. The
annotation used for training are noted as RN for rationales and FA for final answers. SYN means
models trained on synthetic prompts, where trajectories were generated using ηMCTS.
AB
PRM
FR-ORM
SM
LG-#Rollout
Acc
×
×
×
×
×
79.5
✓
×
×
×
×
84.9
✓
✓
×
×
×
85.9
✓
✓
✓
×
×
86.5
✓
✓
✓
✓
×
87.0
✓
✓
✓
✓
✓
88.9
(a) Ablation study on GSM8K
TA-ORM
Option
Acc
#Rollout
×
×
38.8
201
✓
×
44.1
198
✓
✓
45.4
148
(b) Ablation study on MATH
Table 3: (a): Ablation studies on the GSM8K test set of various components of ηMCTS, including
adaptive branching, PRM, fast-rollout with ORM, state merge, and large number of rollouts. (b):
Ablation studies of the impacts of tool-augmented ORM and option-level formulation on MATH.
a further increase in accuracy, reaching 87.0%. Finally the combined of increasing the number of
rollouts with the other components yields the best performance on this task.
Table 3(b) presents the ablation study of option formulation and the tool-augmented critic on the
MATH dataset. Our proposed ηMCTS achieves an accuracy of 45.4 with 148 rollouts. When options
are excluded, reverting to essentially sentence-level MCTS, the performance decreases to 44.1 with
a noticeable increase in the number of rollouts to 198. This demonstrates that option formulation
introduces enhanced flexibility to MCTS, enabling better performance with fewer search efforts.
Furthermore, the most significant decrease in performance is observed when only intrinsic knowledge
is utilized for ORM, which drops to an accuracy of 38.8. This suggests that the absence of an external
tool critically impedes the ORM’s capability to effectively assess challenging math problems.
Figure 2 depicts a comparative results on GSM8K of two rounds of self-improving trained on
trajectories collected using reranking and ηMCTS. We report the performance of greedy decoding,
ηMCTS with a relatively small number of rollouts (50-60), and ηMCTS with a larger number of rollouts
(200-300) for each model. We observe that 1) Models trained on the trajectories from reranking or
ηMCTS outperform the initial policy by a significant margin. In addition, the performance can be
iteratively improved with training suggesting that self-improving has the potential to achieve continual
performance gain. 2) While both reranking and ηMCTS can generate high-quality trajectories for
self-improving , ηMCTS is performant with high efficiency and better accuracy. Models trained on
trajectories generated by it not only exceed the performance of those trained on reranked trajectories
9

Figure 2: Empirical analysis on GSM8K of different self-improving data collection methods and
number of iterations. Models are evaluated with greedy decoding, ηMCTS with small #rollout and
large #rollout.
but also, when decoded with ηMCTS, demonstrate on par performance with GPT-4, revealing that
ALPHALLM is an effective self-improving framework.
Method
Threshold
Acc
Edit distance
20
86.8
Edit distance
50
87.0
Cosine Similarity
0.7
86.3
Model-based
N/A
86.7
(a) Ablation on the choice of state merge func-
tions.
#Trajetory
Acc
1
85.9
4
86.5
8
86.7
(b) Ablation on the number of trajectories.
Table 4: (a): Ablation studies on the choice of heuristic/model-based functions in state merge on
GSM8K with base Llama2-70b. The model used in the model-based state merge is Llama-2-70b-chat.
(b): Ablation studies of the number of rollout trajectories in fast-rollout estimation on GSM8K with
base Llama2-70b.
We further analyze the impact of different hyperparameters and design choices for each component.
Table 4(a) shows that varying heuristic functions (with hyperparameters) for state merge has limited
impact on performance. Table 4(b) shows that, as the number of fast-rollouts increases, there is
a corresponding improvement in performance. This is due to the reduction in the variance of the
estimates. We used n = 4 in our experiments for better trade-off between performance and efficiency.
Additional ablations on the choice of fast-rollout models, are provided in Appendix A.7.
6
Conclusion
In this paper, we introduce ALPHALLM, an imagination-searching-criticizing framework designed
for the self-improvement of LLMs without the necessity of additional annotations. At the heart
of it is the integration of MCTS with LLMs. To tackle the inherent challenges associated with
this integration, including data scarcity, the vastness of search spaces, and the subjective nature
of feedback in language tasks, we introduce a data synthesizer for strategic prompt synthesis, an
optimized MCTS tailored for efficient search in language tasks, and a trio of critic models to provide
precise feedback. Our experimental findings on mathematical reasoning tasks reveal that ALPHALLM
significantly boosts the performance of LLMs without requiring extra data annotations. Moreover,
when decoded with ηMCTS, ALPHALLM performs comparably to GPT-4, highlighting the potential
for self-improvement in LLMs.
10

References
David Abel, Dilip Arumugam, Lucas Lehnert, and Michael Littman. State abstractions for lifelong
reinforcement learning. In International Conference on Machine Learning, pp. 10–19. PMLR,
2018.
Peter Auer, Nicolo Cesa-Bianchi, and Paul Fischer. Finite-time analysis of the multiarmed bandit
problem. Machine learning, 47:235–256, 2002.
Yuntao Bai, Saurav Kadavath, Sandipan Kundu, Amanda Askell, Jackson Kernion, Andy Jones, Anna
Chen, Anna Goldie, Azalia Mirhoseini, Cameron McKinnon, et al. Constitutional ai: Harmlessness
from ai feedback. arXiv preprint arXiv:2212.08073, 2022.
Maciej Besta, Nils Blach, Ales Kubicek, Robert Gerstenberger, Michal Podstawski, Lukas Gianinazzi,
Joanna Gajda, Tomasz Lehmann, Hubert Niewiadomski, Piotr Nyczyk, et al. Graph of thoughts:
Solving elaborate problems with large language models. In Proceedings of the AAAI Conference
on Artificial Intelligence, pp. 17682–17690, 2024.
Samuel R Bowman, Jeeyoon Hyun, Ethan Perez, Edwin Chen, Craig Pettit, Scott Heiner, Kamil˙e
Lukoši¯ut˙e, Amanda Askell, Andy Jones, Anna Chen, et al. Measuring progress on scalable
oversight for large language models. arXiv preprint arXiv:2211.03540, 2022.
Zixiang Chen, Yihe Deng, Huizhuo Yuan, Kaixuan Ji, and Quanquan Gu. Self-play fine-tuning
converts weak language models to strong language models. arXiv preprint arXiv:2401.01335,
2024.
Nuttapong Chentanez, Andrew Barto, and Satinder Singh. Intrinsically motivated reinforcement
learning. Advances in neural information processing systems, 17, 2004.
Ethan Chern, Haoyang Zou, Xuefeng Li, Jiewen Hu, Kehua Feng, Junlong Li, and Pengfei Liu.
Generative ai for math: Abel. https://github.com/GAIR-NLP/abel, 2023.
Hyung Won Chung, Le Hou, Shayne Longpre, Barret Zoph, Yi Tay, William Fedus, Yunxuan Li,
Xuezhi Wang, Mostafa Dehghani, Siddhartha Brahma, et al. Scaling instruction-finetuned language
models. arXiv preprint arXiv:2210.11416, 2022.
Jeffery Allen Clouse. On integrating apprentice learning and reinforcement learning. University of
Massachusetts Amherst, 1996.
Karl Cobbe, Vineet Kosaraju, Mohammad Bavarian, Mark Chen, Heewoo Jun, Lukasz Kaiser,
Matthias Plappert, Jerry Tworek, Jacob Hilton, Reiichiro Nakano, et al. Training verifiers to solve
math word problems. arXiv preprint arXiv:2110.14168, 2021.
Maarten De Waard, Diederik M Roijers, and Sander CJ Bakkes. Monte carlo tree search with options
for general video game playing. In 2016 IEEE Conference on Computational Intelligence and
Games (CIG), pp. 1–8. IEEE, 2016.
Ruomeng Ding, Chaoyun Zhang, Lu Wang, Yong Xu, Minghua Ma, Wei Zhang, Si Qin, Saravan
Rajmohan, Qingwei Lin, and Dongmei Zhang. Everything of thoughts: Defying the law of penrose
triangle for thought generation. arXiv preprint arXiv:2311.04254, 2023.
Xidong Feng, Ziyu Wan, Muning Wen, Ying Wen, Weinan Zhang, and Jun Wang. Alphazero-like tree-
search can guide large language model decoding and training. arXiv preprint arXiv:2309.17179,
2023.
Yangqing Fu, Ming Sun, Buqing Nie, and Yue Gao. Accelerating monte carlo tree search with
probability tree state abstraction. Advances in Neural Information Processing Systems, 36, 2024.
Zhibin Gou, Zhihong Shao, Yeyun Gong, Yujiu Yang, Minlie Huang, Nan Duan, Weizhu Chen,
et al. Tora: A tool-integrated reasoning agent for mathematical problem solving. arXiv preprint
arXiv:2309.17452, 2023.
Hongyi Guo, Yuanshun Yao, Wei Shen, Jiaheng Wei, Xiaoying Zhang, Zhaoran Wang, and Yang Liu.
Human-instruction-free llm self-alignment with limited samples. arXiv preprint arXiv:2401.06785,
2024.
11

Shibo Hao, Yi Gu, Haodi Ma, Joshua Hong, Zhen Wang, Daisy Wang, and Zhiting Hu. Reasoning
with language model is planning with world model. In Proceedings of the 2023 Conference on
Empirical Methods in Natural Language Processing, pp. 8154–8173, 2023.
Dan Hendrycks, Collin Burns, Saurav Kadavath, Akul Arora, Steven Basart, Eric Tang, Dawn Song,
and Jacob Steinhardt. Measuring mathematical problem solving with the math dataset, 2021.
Ruixin Hong, Hongming Zhang, Xinyu Pang, Dong Yu, and Changshui Zhang. A closer look
at the self-verification abilities of large language models in logical reasoning. arXiv preprint
arXiv:2311.07954, 2023.
Jie Huang, Xinyun Chen, Swaroop Mishra, Huaixiu Steven Zheng, Adams Wei Yu, Xinying Song,
and Denny Zhou. Large language models cannot self-correct reasoning yet. arXiv preprint
arXiv:2310.01798, 2023.
Aitor Lewkowycz, Anders Andreassen, David Dohan, Ethan Dyer, Henryk Michalewski, Vinay Ra-
masesh, Ambrose Slone, Cem Anil, Imanol Schlag, Theo Gutman-Solo, et al. Solving quantitative
reasoning problems with language models. Advances in Neural Information Processing Systems,
35:3843–3857, 2022.
Xian Li, Ping Yu, Chunting Zhou, Timo Schick, Luke Zettlemoyer, Omer Levy, Jason Weston, and
Mike Lewis. Self-alignment with instruction backtranslation. arXiv preprint arXiv:2308.06259,
2023.
Hunter Lightman, Vineet Kosaraju, Yura Burda, Harri Edwards, Bowen Baker, Teddy Lee, Jan
Leike, John Schulman, Ilya Sutskever, and Karl Cobbe. Let’s verify step by step. arXiv preprint
arXiv:2305.20050, 2023.
Jiacheng Liu, Andrew Cohen, Ramakanth Pasunuru, Yejin Choi, Hannaneh Hajishirzi, and Asli
Celikyilmaz. Making ppo even better: Value-guided monte-carlo tree search decoding. arXiv
preprint arXiv:2309.15028, 2023.
Jieyi Long. Large language model guided tree-of-thought. arXiv preprint arXiv:2305.08291, 2023.
Jelena Luketina, Nantas Nardelli, Gregory Farquhar, Jakob N. Foerster, Jacob Andreas, Edward
Grefenstette, Shimon Whiteson, and Tim Rocktäschel. A survey of reinforcement learning informed
by natural language. ArXiv, abs/1906.03926, 2019. URL https://api.semanticscholar.
org/CorpusID:182952502.
Haipeng Luo, Qingfeng Sun, Can Xu, Pu Zhao, Jianguang Lou, Chongyang Tao, Xiubo Geng,
Qingwei Lin, Shifeng Chen, and Dongmei Zhang. Wizardmath: Empowering mathematical
reasoning for large language models via reinforced evol-instruct. arXiv preprint arXiv:2308.09583,
2023.
Aman Madaan, Niket Tandon, Prakhar Gupta, Skyler Hallinan, Luyu Gao, Sarah Wiegreffe, Uri
Alon, Nouha Dziri, Shrimai Prabhumoye, Yiming Yang, et al. Self-refine: Iterative refinement
with self-feedback. Advances in Neural Information Processing Systems, 36, 2024.
Maxwell Nye, Anders Johan Andreassen, Guy Gur-Ari, Henryk Michalewski, Jacob Austin, David
Bieber, David Dohan, Aitor Lewkowycz, Maarten Bosma, David Luan, et al. Show your work:
Scratchpads for intermediate computation with language models. arXiv preprint arXiv:2112.00114,
2021.
R OpenAI. Gpt-4 technical report. arXiv, pp. 2303–08774, 2023.
Long Ouyang, Jeffrey Wu, Xu Jiang, Diogo Almeida, Carroll Wainwright, Pamela Mishkin, Chong
Zhang, Sandhini Agarwal, Katarina Slama, Alex Ray, et al. Training language models to follow
instructions with human feedback. Advances in Neural Information Processing Systems, 35:
27730–27744, 2022.
Baolin Peng, Xiujun Li, Lihong Li, Jianfeng Gao, Asli Celikyilmaz, Sungjin Lee, and Kam-Fai Wong.
Composite task-completion dialogue policy learning via hierarchical deep reinforcement learning.
In Proceedings of the 2017 Conference on Empirical Methods in Natural Language Processing.
Association for Computational Linguistics, 2017.
12

Rafael Rafailov, Archit Sharma, Eric Mitchell, Stefano Ermon, Christopher D Manning, and Chelsea
Finn. Direct preference optimization: Your language model is secretly a reward model. arXiv
preprint arXiv:2305.18290, 2023.
Rajkumar Ramamurthy, Prithviraj Ammanabrolu, Kianté Brantley, Jack Hessel, Rafet Sifa, Christian
Bauckhage, Hannaneh Hajishirzi, and Yejin Choi. Is reinforcement learning (not) for natural
language processing?: Benchmarks, baselines, and building blocks for natural language policy
optimization.
ArXiv, abs/2210.01241, 2022.
URL https://api.semanticscholar.org/
CorpusID:252693405.
William Saunders, Catherine Yeh, Jeff Wu, Steven Bills, Long Ouyang, Jonathan Ward, and Jan
Leike. Self-critiquing models for assisting human evaluators. arXiv preprint arXiv:2206.05802,
2022.
John Schulman, Filip Wolski, Prafulla Dhariwal, Alec Radford, and Oleg Klimov. Proximal policy
optimization algorithms. arXiv preprint arXiv:1707.06347, 2017.
David Silver, Aja Huang, Chris J Maddison, Arthur Guez, Laurent Sifre, George Van Den Driessche,
Julian Schrittwieser, Ioannis Antonoglou, Veda Panneershelvam, Marc Lanctot, et al. Mastering
the game of go with deep neural networks and tree search. nature, 529(7587):484–489, 2016.
David Silver, Thomas Hubert, Julian Schrittwieser, Ioannis Antonoglou, Matthew Lai, Arthur Guez,
Marc Lanctot, Laurent Sifre, Dharshan Kumaran, Thore Graepel, et al. Mastering chess and shogi
by self-play with a general reinforcement learning algorithm. arXiv preprint arXiv:1712.01815,
2017.
Kaya Stechly, Karthik Valmeekam, and Subbarao Kambhampati. On the self-verification limitations
of large language models on reasoning and planning tasks. arXiv preprint arXiv:2402.08115, 2024.
Zhiqing Sun, Yikang Shen, Qinhong Zhou, Hongxin Zhang, Zhenfang Chen, David Cox, Yiming
Yang, and Chuang Gan. Principle-driven self-alignment of language models from scratch with
minimal human supervision. arXiv preprint arXiv:2305.03047, 2023.
Richard S Sutton and Andrew G Barto. Reinforcement learning: An introduction. MIT press, 2018.
Richard S. Sutton, Doina Precup, and Satinder Singh. Between mdps and semi-mdps: A framework
for temporal abstraction in reinforcement learning. Artificial Intelligence, 112(1):181–211, 1999a.
ISSN 0004-3702. doi: https://doi.org/10.1016/S0004-3702(99)00052-1. URL https://www.
sciencedirect.com/science/article/pii/S0004370299000521.
Richard S Sutton, Doina Precup, and Satinder Singh. Between mdps and semi-mdps: A framework
for temporal abstraction in reinforcement learning. Artificial intelligence, 112(1-2):181–211,
1999b.
Richard Stuart Sutton. Temporal credit assignment in reinforcement learning. University of Mas-
sachusetts Amherst, 1984.
Matthew E Taylor, Nicholas Carboni, Anestis Fachantidis, Ioannis Vlahavas, and Lisa Torrey. Rein-
forcement learning agents providing advice in complex video games. Connection Science, 26(1):
45–63, 2014.
Gemini Team, Rohan Anil, Sebastian Borgeaud, Yonghui Wu, Jean-Baptiste Alayrac, Jiahui Yu, Radu
Soricut, Johan Schalkwyk, Andrew M Dai, Anja Hauth, et al. Gemini: a family of highly capable
multimodal models. arXiv preprint arXiv:2312.11805, 2023.
Hugo Touvron, Louis Martin, Kevin Stone, Peter Albert, Amjad Almahairi, Yasmine Babaei, Nikolay
Bashlykov, Soumya Batra, Prajjwal Bhargava, Shruti Bhosale, et al. Llama 2: Open foundation
and fine-tuned chat models. arXiv preprint arXiv:2307.09288, 2023a.
Hugo Touvron, Louis Martin, Kevin Stone, Peter Albert, Amjad Almahairi, Yasmine Babaei, Nikolay
Bashlykov, Soumya Batra, Prajjwal Bhargava, Shruti Bhosale, et al. Llama 2: Open foundation
and fine-tuned chat models. arXiv preprint arXiv:2307.09288, 2023b.
13

Jonathan Uesato, Nate Kushman, Ramana Kumar, Francis Song, Noah Siegel, Lisa Wang, Antonia
Creswell, Geoffrey Irving, and Irina Higgins. Solving math word problems with process-and
outcome-based feedback. arXiv preprint arXiv:2211.14275, 2022.
Karthik Valmeekam, Alberto Olmo, Sarath Sreedharan, and Subbarao Kambhampati. Large language
models still can’t plan (a benchmark for llms on planning and reasoning about change). arXiv
preprint arXiv:2206.10498, 2022.
Gabriel Van Eyck and Martin Müller. Revisiting move groups in monte-carlo tree search. In Ad-
vances in Computer Games: 13th International Conference, ACG 2011, Tilburg, The Netherlands,
November 20-22, 2011, Revised Selected Papers 13, pp. 13–23. Springer, 2012.
Peiyi Wang, Lei Li, Zhihong Shao, RX Xu, Damai Dai, Yifei Li, Deli Chen, Y Wu, and Zhifang
Sui. Math-shepherd: Verify and reinforce llms step-by-step without human annotations. CoRR,
abs/2312.08935, 2023.
Yizhong Wang, Yeganeh Kordi, Swaroop Mishra, Alisa Liu, Noah A Smith, Daniel Khashabi, and
Hannaneh Hajishirzi. Self-instruct: Aligning language model with self generated instructions.
arXiv preprint arXiv:2212.10560, 2022.
Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten Bosma, Fei Xia, Ed Chi, Quoc V Le, Denny
Zhou, et al. Chain-of-thought prompting elicits reasoning in large language models. Advances in
neural information processing systems, 35:24824–24837, 2022.
Yuxi Xie, Kenji Kawaguchi, Yiran Zhao, James Xu Zhao, Min-Yen Kan, Junxian He, and Michael
Xie. Self-evaluation guided beam search for reasoning. Advances in Neural Information Processing
Systems, 36, 2024.
Can Xu, Qingfeng Sun, Kai Zheng, Xiubo Geng, Pu Zhao, Jiazhan Feng, Chongyang Tao, and Daxin
Jiang. Wizardlm: Empowering large language models to follow complex instructions. arXiv
preprint arXiv:2304.12244, 2023.
Shunyu Yao, Dian Yu, Jeffrey Zhao, Izhak Shafran, Tom Griffiths, Yuan Cao, and Karthik Narasimhan.
Tree of thoughts: Deliberate problem solving with large language models. Advances in Neural
Information Processing Systems, 36, 2024.
Longhui Yu, Weisen Jiang, Han Shi, Jincheng Yu, Zhengying Liu, Yu Zhang, James T Kwok, Zhenguo
Li, Adrian Weller, and Weiyang Liu. Metamath: Bootstrap your own mathematical questions for
large language models. arXiv preprint arXiv:2309.12284, 2023.
Lifan Yuan, Ganqu Cui, Hanbin Wang, Ning Ding, Xingyao Wang, Jia Deng, Boji Shan, Huimin
Chen, Ruobing Xie, Yankai Lin, et al. Advancing llm reasoning generalists with preference trees.
arXiv preprint arXiv:2404.02078, 2024a.
Weizhe Yuan, Richard Yuanzhe Pang, Kyunghyun Cho, Sainbayar Sukhbaatar, Jing Xu, and Jason
Weston. Self-rewarding language models. arXiv preprint arXiv:2401.10020, 2024b.
Eric Zelikman, Yuhuai Wu, Jesse Mu, and Noah Goodman. Star: Bootstrapping reasoning with
reasoning. Advances in Neural Information Processing Systems, 35:15476–15488, 2022.
Eric Zelikman, Georges Harik, Yijia Shao, Varuna Jayasiri, Nick Haber, and Noah D Goodman.
Quiet-star: Language models can teach themselves to think before speaking. arXiv preprint
arXiv:2403.09629, 2024.
Tinghui Zhu, Kai Zhang, Jian Xie, and Yu Su. Deductive beam search: Decoding deducible rationale
for chain-of-thought reasoning. arXiv preprint arXiv:2401.17686, 2024.
14

ORM
Selection
Expansion
Simulation
Backpropagation
Value
PRM
𝑠!
𝑜!
𝑠!
𝑠"
𝑠#
𝑜"
𝑜$ …
Figure 3: An overview of the four operations of ηMCTS. A node is selected, expanded, simulated
with fast rollout policy until a terminal node is reached, then the signals from value function, PRM and
ORM are backpropagated.
A
Appendix
A.1
Imagination, Searching, Criticizing and Learning Loop
Algorithm 1: LLM self-improving loop
Input Initial dataset D0 = {(x0
i , y0
i ) | i ∈[N]}, policy model π0
θ, reward model R, number of
self-improving training loop K
Output θk
for k ←1, . . . , K do
Generate synthetic prompts [xk] = SYN(πk−1
θ
, Dk−1)
Collect trajectories with search algorithm, e.g., MCTS guided by R.
[ˆyk] = MCTS(πk−1
θ
, [xk])
Construct dataset Dk = {(xk, ˆyk)}
Update policy θk = arg minθ L(πk−1
θ
, Dk)
end
The algorithm is shown in Algorithm 1.
A.2
Option-level MCTS
As illustrated in Figure 3, option-level MCTS consists of the following operations:
• Selection Starting from the root node, we iteratively select the child node based on Equation ??.
• Expansion Once an expandable leaf node is selected, a new node is generated by starting with the
previous state of the parent node as the initial option state. The option is then sampled using the
policy π, and its completion is determined by the termination function β.
• Simulation The scaled reward of the newly expanded node, as well as some simulated future
trajectories are evaluated using the feedback functions, which is discussed in §4.4.
• Backpropagation The average value of the newly generated node and all its ancestors is updated
using the scaled reward from the evaluation step. Meanwhile, the visit counts for these nodes are
also increased by one.
A.3
Importance-Based Adaptive Branching Under Uniform Distribution
Let V = {vπ
ϕ(st, o1
t), vπ
ϕ(st, o2
t), ..., vπ
ϕ(st, omt
t )} be a set of mt values that are uniformly dis-
tributed. If the maximum and minimum values from V are vmax and vmin, the average gap between
two consecutive values is given by vmax−vmin
mt−1
. The upper bound of expected minimum distances
from a new value vnew to any value from V is achieved when vnew is consistently positioned at the
midpoint between two consecutive values, and it is given by vmax−vmin
2(mt−1) .
15

Since vmax −vmin = 2I(st) for a uniform distribution, we can conclude that Eϕ(t) ≤I(st)
mt−1.
Theorem 4.1. The optimal branching factor mt in a tree search is set such that mt−1 is proportional
to the node importance I(st), under the condition I(st)
mt−1 ≤ϵ.
Proof. We can have the optimization problem as:
minimize:
X
mt
subject to: I(st)
mt −1 ≤ϵ
Introduce the Lagrange multiplier λt for each constraint:
L(mt, λt) =
X
mt +
X
λt (ϵ(mt −1) −I(st))
Now, let’s find the gradient of the Lagrangian with respect to mt and λt and set them to zero:
∇mtL = 1 + ϵλt = 0
∇λtL = ϵ(mt −1) −I(st) = 0
From the first equation, we get:
λt = −1
ϵ
Substitute this value of λt into the second equation:
ϵ(mt −1) −I(st) = 0
Solving for mt, we get:
mt = I(st)
ϵ
+ 1
Thus, mt −1 is proportional to the node importance I(st).
A.4
Importance-Based Adaptive Branching Under Gaussian Distribution
If we assume that vπ
ϕ([st, oj
t]) and vπ
ϕ([st, oi
t]) are independent and identically distributed Gaussian
random variables:
vπ
ϕ([st, oj
t]), vπ
ϕ([st, oi
t]) ∼N(µ, σ2)
The difference Dij = vπ
ϕ([st, oj
t]) −vπ
ϕ([st, oi
t]) will follow a normal distribution with:
Dij ∼N(0, 2σ2)
To find the expected minimum absolute difference between vπ
ϕ([st, oj
t]) and the closest vπ
ϕ([st, oi
t]),
we need to consider the distribution of the minimum of mt Gaussian differences.
The expected minimum value of mt absolute differences can be approximated using properties of
order statistics for Gaussian distributions.
For a set of mt independent normal random variables with variance 2σ2, the expected minimum
absolute difference, E[mini |Dij|], can be approximated by:
Eϕ(t) ≈σ
√
2
√mt
16

This approximation arises from the fact that the expected minimum value of the absolute deviations
of normally distributed random variables scales with the inverse of the square root of the number of
samples.
Then, assume the range of the mt samples are Rm = max(vπ
ϕ([st, oi
t]) −min(vπ
ϕ([st, oi
t]), the
the expected range E[Rm] of mt samples from a normal distribution can be approximated using
properties of extreme values of Gaussian distributions. The range Rm can be approximated as:
Rm ≈σ(z0.9995 −z0.0005)
where zp is the p-th percentile of the standard normal distribution. It can converge to
Rm ≈σ
p
2 ln(mt)

2 −ln(ln(mt))
4 ln(mt)

For simplicity, we can approximate the range using the primary term, which captures the dominant
behavior:
Rm ≈σ
p
2 ln(mt)
Then we have
Eϕ(t) ≈
√
2
√mt
Rm
p
2 ln(mt)
Knowing that for all distributions,
I(st) ≥Rm
2
We have
Eϕ(t) ≤
I(st)
p
mt ln(mt)
Then to find the optimal mt, the optimization problem is
minimize:
X
mt
subject to:
I(st)
p
mt ln(mt)
≤ϵ
To solve this optimization problem, we can first rewrite the constraint in terms of mt.
mt ln(mt) ≥I2(st)
ϵ2
Now, let’s define a new function g(mt) = mt ln(mt). We want to find the minimum mt such that
g(mt) ≥I2(st)
ϵ2
. To do this, we can find the derivative of g(mt) and set it to zero to find the critical
points.
g′(mt) =
d
dmt
(mt ln(mt)) = ln(mt) + 1
Setting the derivative to zero:
ln(mt) = −1
mt = e−1
However, this critical point corresponds to a minimum of the function g(mt), and we are interested in
the minimum mt that satisfies the constraint g(mt) ≥I2(st)
ϵ2
. Since the function g(mt) is increasing
for mt > e−1, we can find the minimum mt by setting g(mt) = I2(st)
ϵ2
and solving for mt:
mt ln(mt) = I2(st)
ϵ2
This can not be solved directly, but we can still observe that there is a positive correlation between
mt and I(st).
17

Method
GSM8K
MATH
Small
Large
Small
Large
c
1.0
1.5
1.0
1.0
α
1.0
1.0
1.0
1.0
cmax(0)
60
60
60
60
cmax(t) where t > 0
10
10
10
10
cmin(0)
10
40
10
20
cmin(t) where t > 0
2
2
3
3
Table 5: Parameters for MCTS. The Small/Large means small #rollout and small #rollout
A.5
Prompt Templates
A.5.1
PRM
###You are given a math problem, followed by a step-by-step reasoning process. Your task is
to read the problem carefully, understand the solving steps, and check the correctness of the
last reasoning step. Output ’True’ if the last step is correct, and ’False’ otherwise.\n\n###
State\n{state}\n\n###Action\n{option}\n\n###Assessment\n{textual reward}
A.5.2
ORM
###Assess a solution including final answer to a given math problem by following below
steps.\n- Evaluate the method used for solving the problem.\n- Review each calculation step
for accuracy. Check for computational errors, incorrect formula applications, or arithmetic
mistakes.\n- The solution should use all the information provided in the question.\n- Examine
the final answer for correctness, considering the calculations and method used.\n.\n\n###
Prompt\n{prompt}\n\n###Trajectory\n{trajectory}\n\n###Assessment\n{textual
reward}
A.5.3
Policy Finetuning
For MATH experiments that take a WizardMath V1.0 70B as the policy, we adopt their proposed
system prompt for self-improving. For GSM8K experiments taking Llama2 70B pretrain as the
policy, we use the following system prompt.
A chat between a curious user and an artificial intelligence assistant.\n The assistant gives
helpful, detailed, and polite answers to the user’s questions.\n User: xi\n Assistant: yi
A.6
MCTS Details
We set the MCTS parameters in Table 5.
A.7
Additional Ablations
Fast-rollout model
Using Llama-2-70b instead of Abel-7B-002 improves performance by reducing
bias from a smaller model, but Abel-002-7B is faster with similar computational resources due to
higher concurrency and quicker processing. The details can be found in Table 6.
A.8
Search Comparison
Table 7 presents the performance of various methods applied to different number of responses,
from 10 to 50. Our analysis confirms several key findings: 1) Reranking utilizing ORM consistently
outperforms self-consistency techniques, indicating that ORM is capable of generating meaningful
18
