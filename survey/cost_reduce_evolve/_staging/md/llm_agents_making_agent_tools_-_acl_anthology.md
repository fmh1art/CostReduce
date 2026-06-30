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
tational requirements, data demands, and amount
of code involved. Lastly, deploying tools in high-
stakes applications demands rigorous validation,
testing, and quality assurance – standards that cur-
rent agent systems cannot realistically meet if re-
quired to develop such tools entirely from scratch.
Encouragingly, a growing emphasis on repro-
ducibility within the scientific community has led
to an increase in publicly released code accompa-
nying research papers (Zhou et al., 2024). Con-
sequently, a vast array of potential tools now exist
as standalone solutions. However, many research-
ers in fields like healthcare, biology, drug develop-
ment, R&D are unable to effectively use them due
to the technical skills required for their deployment.
Instead of building tools entirely from scratch,
we ask the following question: Can LLM agents
autonomously download, integrate, and execute
complex, existing tools to empower researchers
with minimal technical expertise in the future?
Towards this goal, we propose TOOLMAKER, an
agentic framework that autonomously generates
LLM-compatible tools from scientific papers and
their associated code repositories, bypassing the
need for human intermediaries to manually set up,
install, and adapt them to fit the requirements of
their applications. Given a task description, a sci-
entific paper, and its associated code repository,
TOOLMAKER generates an executable tool that en-
ables LLMs to perform the task (see Fig. 2).
To
evaluate
TOOLMAKER,
we
introduce
TM-BENCH, a benchmark comprising 15 diverse
tasks across various medical disciplines (pathology,
radiology, omics), as well as non-medical fields,
e.g. LLMs and 3D vision. Unlike existing bench-
marks (Jimenez et al., 2024; Zhuo et al., 2024; Jain
et al., 2024) which assume pre-installed depend-
encies for function implementation, TOOLMAKER
operates in a fully open-ended environment. Tasks
in our benchmark encompass the entire workflow:
downloading resources, managing and resolving
dependency issues, reading through large code-
Figure 2: Given a task description, a scientific paper, a
link to the associated code repository, and an example of
the tool invocation, TOOLMAKER creates (i) a Docker
container in which the tool can be executed, (ii) a Python
function that performs the task.
bases, and implementing, testing, and debugging
code. TM-BENCH includes over 100 unit tests to
objectively assess the generated tools’ correctness.
2
Related work
Agents
In addition to demonstrating impress-
ive capabilities in generating human-like text,
LLMs such as ChatGPT (Ouyang et al., 2022),
Claude (Anthropic, 2024), Gemini (Gemini Team,
2024) and Llama (Llama Team, 2024), on their
own, have shown strong potential in question an-
swering and reasoning on problems in natural sci-
ence related fields, like math (Shao et al., 2024),
chemistry (Bran et al., 2024) and healthcare (Sing-
hal et al., 2023). However, LLMs often struggle
solving more complex problems directly, especially
in situations that require intermediate results from
multiple steps (Valmeekam et al., 2023). To ad-
dress this, LLM agents have been developed which
enhance an LLM’s capabilities by integrating ex-
ternal tools (Schick et al., 2023).
In software engineering, a number of agentic
and workflow-based systems have been proposed
for solving GitHub issues (Wang et al., 2024; Yang
et al., 2024; Xia et al., 2024), as well as developing
entire software projects (Qian et al., 2024; Nguyen
et al., 2024; Hong et al., 2024). Among these,
OpenHands (Wang et al., 2024) achieves state-of-
the-art performance on SWE-Bench (Jimenez et al.,
2024), a benchmark for solving GitHub issues.
Medical LLM agents have been developed
for clinical decision-making and diagnostics,
26093

such as building risk calculators from publica-
tions (Jin et al., 2024), oncology agents that consult
guidelines and imaging tools (Ferber et al., 2024),
and multi-agent systems that enable collaboration
across clinicians, patients, and hospitals (Kim et al.,
2024; Li et al., 2025). Beyond clinical use, agents
have been proposed for bioinformatics tasks like
data extraction, pipeline execution, and hypothesis
testing (Ding et al., 2024; Xin et al., 2024), even
automating entire scientific projects, including lit-
erature reviews, experiment design, and manuscript
writing (Lu et al., 2024a; Schmidgall et al., 2025).
Nonetheless, regardless of domain, agentic sys-
tems remain constrained by the tools at their dis-
posal. For example, when tasked to solve a patho-
logy image classification problem, the AIDE ma-
chine learning agent (Schmidt et al., 2024) trains
a standard convolutional net (c.f. Fig. 2 in Chan
et al. (2024)). By contrast, a domain expert would
instead employ pathology foundation models, as
these have been designed specifically for this type
of problem (Chen et al., 2024; Zimmermann et al.,
2024; Wölflein et al., 2024). Thus, AIDE lacks the
necessary tools to solve the task effectively.
Tool creation
To address this, we consider the
problem of tool creation – enabling LLMs to cre-
ate their own tools, to dynamically expand their
capabilities at runtime. Tool creation is not to be
confused with tool learning, i.e. teaching LLMs
to utilise appropriate, human-crafted tools more
effectively which has been extensively studied in
recent years (Qin et al., 2024; Schick et al., 2023).
Previous work on tool creation (Cai et al., 2024;
Yuan et al., 2024; Qian et al., 2023) is limited to
crafting very simple tools because (i) they are craf-
ted from scratch, and (ii) these systems cannot inter-
act with the operating system (OS) by running bash
commands, reading/writing files, etc. (see Table 1).
Our approach addresses both of these limitations.
Method
Error
handling
OS
interaction
Complex
tasks
CRAFT (Yuan et al., 2024)
✗
✗
✗
CREATOR (Qian et al., 2023)
✓
✗
✗
LATM (Cai et al., 2024)
✓
✗
✗
TOOLMAKER (ours)
✓
✓
✓
Table 1: Comparison of tool creation methods. OS inter-
action refers to the ability to interact with the operating
system (e.g. read/write files, run commands, web brows-
ing). Complex tasks require installing and using external
dependencies (e.g. libraries, model weights).
Benchmarks
Various benchmarks have been pro-
posed specifically for tool creation, and software
engineering more generally.
Code generation
benchmarks (Zhuo et al., 2024; Jain et al., 2024)
assess the ability of LLMs to generate Python func-
tions for narrowly defined tasks (e.g. simple math-
ematical problems) using the Python standard lib-
rary. Tool creation benchmarks extend this idea,
enabling the LLM to decide the signature of the
Python function in addition to generating the im-
plementation itself (Yuan et al., 2024; Qian et al.,
2023; Cai et al., 2024). Yet, these existing code gen-
eration and tool creation benchmarks are limited
to simple Python functions – they cannot install
dependencies or directly interact with the OS.
On the other hand, software engineering bench-
marks assess LLM agents for solving GitHub is-
sues (Jimenez et al., 2024), creating ML mod-
els (Tang et al., 2024; Chan et al., 2024) and per-
forming repository-level scientific tasks (Majumder
et al., 2024; Chen et al., 2025; Bogin et al., 2024;
Liu et al., 2024). However, these benchmarks focus
on performing particular tasks, as opposed to cre-
ating a reusable tool to solve a class of problems.
We combine both streams (tool creation and soft-
ware engineering) by proposing a benchmark fo-
cused on real-world multi-step scientific tasks that
requires agents to (i) autonomously install neces-
sary dependencies (as opposed to implementing
simple Python functions), and (ii) produce a re-
usable tool that can be applied with different inputs
(as opposed to solving a single task instance).
3
TOOLMAKER
We design TOOLMAKER to autonomously convert
stand-alone code repositories from scientific pub-
lications into LLM-compatible tools. Each tool
should complete a specific, user-defined task. To
do so, we require a minimal tool definition (see
Fig. 2, top), consisting of:
1) a concise textual description of the task,
2) GitHub URL of the associated repository, and
3) a list of required input arguments, including
an example value for each argument.
This tool definition could in principle be rep-
resented as the signature of a Python function
with a docstring, like in existing code generation
tasks (Zhuo et al., 2024; Jain et al., 2024). How-
ever, unlike previous work, we require the LLM
to not only implement the function, but also to set
up the environment wherein the function will be
executed. The latter is necessary due to the com-
plexity of our tasks which require e.g. installing
external dependencies, downloading models, and
26094

Figure 3: TOOLMAKER workflow. Given a task description, a scientific paper, and its associated code repository,
TOOLMAKER generates an executable tool that enables a downstream LLM agent to perform the described task.
setting up configurations while considering system
and hardware specifications.
We structure TOOLMAKER as an agentic work-
flow (Fig. 3) consisting of two stages: environment
setup and tool implementation. During environ-
ment setup, TOOLMAKER produces a reproducible
“snapshot” of the system (a Docker image) wherein
the tool will run. Then, TOOLMAKER generates a
Python function that implements the desired task.
3.1
Workflow components
We define the state of the workflow at any point in
time to be a pair
s =
 h, e

∈H × E.
Here, h ∈H is the conversation history (the
ordered sequence of messages from the user, tools,
and the LLM), and e ∈E is the environment state
(represented by a checkpointed Docker container).
TOOLMAKER is built out of fundamental com-
ponents, each viewed as a function that acts on the
workflow state as
S 7→S × R,
where S = H × E is the space of all possible
workflow states, and R ⊇M ∪O is the set of
possible returns (e.g. a newly generated message in
M or an environment observation in O). We dis-
tinguish three types of components:
LLM calls
(H 7→H × M),
environment interactions
(E 7→E×O), and
agents (H×E 7→H×E×R).
3.1.1
LLM calls
An LLM can be viewed as a function
LLM : H →M,
which, given a conversation history, produces a
single new message. As a TOOLMAKER workflow
component, an LLM call ℓ: H →H×M takes the
workflow state’s conversation history h, appends
LLM(h), and returns the new message:
h 7→(h ⊕LLM(h), LLM(h)).
LLMs calls thus only update the conversation and
do not modify the environment. We use OpenAI’s
gpt-4o-2024-08-06 model for the LLM calls.
3.1.2
Environment interactions
An environment interaction is any action a ∈A
that can read from or write to the environment state
e. We may thus model it by
e 7→(e′, o),
where e′ is the updated environment state, and o ∈
O is the observation produced by the action.
The set of environment actions are
A =











RUN_BASH_COMMAND,
LIST_DIRECTORY,
READ_FILE,
WRITE_FILE,
BROWSE,
GOOGLE_DRIVE_LIST_FOLDER,
GOOGLE_DRIVE_DOWNLOAD_FILE,
RUN_IMPLEMENTATION











.
We distinguish between read-only actions and write
actions (Huyen, 2024). While read-only actions
Ar = {
,
,
,
} have e′ = e, write actions
Aw = {
,
,
,
} may modify e.
The
RUN_IMPLEMENTATION action is a spe-
cial action that allows TOOLMAKER to execute a
candidate tool implementation.
3.1.3
Agents
An agent π, illustrated in Fig. 4, chains multiple
LLM calls and environment interactions to accom-
plish a specific sub-task which is specified by a
26095

high-level instruction, mπ ∈M, e.g. “install this
repository and its dependencies”.
Figure 4: An agent uses a tool-augmented LLM to per-
form a specific sub-task, and returns the result. Mes-
sages are appended to the conversation history, and tool
calls enable the agent to interact with the environment.
Formally, an agent π maps the current workflow
state s = (h, e) to a new state sT = (hT , eT ) and
return value r ∈R:
(h, e) 7→(hT , eT , r).
The agent follows a sequence of state transitions
s0 →s1 →· · · →sT ,
where each state st = (ht, et) ∈S. At step t = 0,
the agent receives the initial state
s0 =
 h ⊕mπ, e

.
At each step t, the agent employs a special tool-
augmented LLM, denoted
LLMπ : H →Aπ ∪R,
which, given the current conversation ht, either
outputs an action at ∈Aπ (a tool call) or the fi-
nal result r ∈R of the sub-task. Here, Aπ ⊆
A \ {
RUN_IMPLEMENTATION} excludes dir-
ectly running candidate tool implementations, as
this is a separate step in the TOOLMAKER work-
flow. We implement the choice between Aπ and
R using OpenAI’s function calling and structured
output APIs respectively (OpenAI, 2025).
If
the
LLM
proposes
an
action
at
=
LLMπ(ht) ∈A, we execute at on the current
environment to obtain the observation and updated
environment state (et+1, ot) = at(et). We then
append both the tool call and its observation to the
conversation, forming the new state
st+1 = (ht ⊕at ⊕ot, et+1).
If instead LLMπ(ht) outputs a final result r ∈R,
the agent terminates and returns sT = (ht, et, r).
Algorithm 1 TOOLMAKER workflow.
Require: Tool definition mtool, initial environment e∅∈E
1: h∅←{mtool}
▷initialise conversation history
2: h, e, r ←
INSTALL_REPOSITORY(h∅, e∅)
3: ¯e ←e
▷snapshot of installed environment state
4: h, e, r ←
EXPLORE(h∅, ¯e)
5: h, m ←
PLAN(h)
6: ¯h ←h
▷snapshot of conversation history
7: h, mcode ←
IMPLEMENT(h)
8: σ ←∅
9: while true do
10:
e ←¯e
▷restore installed environment state
11:
h ←¯h ⊕σ ⊕mcode
▷restore conversation history
12:
e, o ←
RUN_IMPLEMENTATION(e, mcode)
13:
h, m ←
ASSESS_TOOL_OUTPUT(h ⊕o)
14:
if m is successful then
15:
return ¯e, mcode
16:
end if
17:
h, e, r ←
DIAGNOSE_ERROR(h ⊕o, e)
18:
h, mcode ←
REIMPLEMENT(h)
19:
h, msummary ←
SUMMARISE(h)
20:
σ ←σ ⊕msummary
21: end while
3.2
TOOLMAKER workflow
In this section, we describe our workflow in detail,
which at a high level is illustrated in Fig. 3, and in
pseudocode in Algorithm 1, using the three types
of components (
LLM calls,
environment in-
teractions, and
agents) introduced above.
TOOLMAKER’s initial conversation history h∅
is a system prompt that contains the tool definition
mtool. We provide the full prompts in Appendix D.
Environment setup
To obtain the state of the
execution environment necessary for the tool to ex-
ecute, we employ the
INSTALL_REPOSITORY
agent (line 2) that is instructed to install and set up
the repository. This agent clones and explores the
repository, reads documentation, and downloads
any dependencies it deems necessary such as mod-
els, datasets, and libraries. Each of these steps
involve planning and learning from previous obser-
vations such as error logs arising during execution.
The agent begins with a clean environment state
e∅(a python:3.12 Docker image). Importantly,
we record all write actions (Aw) that the agent
performs. Since each of these actions may be ex-
pressed as a bash command, we simply concatenate
their bash representations to obtain the environment
definition in the form of a bash script or Dockerfile.
Initial implementation
We first instruct an agent
(
EXPLORE) to explore the repository and gather
all information necessary to implement the tool.
Note that we do not carry over the conversation
history from the previous stage, in order to not
pollute the context with a large number of messages
26096

(by calling
EXPLORE on h∅, not h on line 4).
Next we perform an LLM call (
PLAN) to cre-
ate a step-by-step plan for the implementation. We
keep all messages (including actions and observa-
tions) in the conversation history, so this informa-
tion can be used to create the plan.
Then, we instruct the LLM (
IMPLEMENT) to
write the Python code for the tool based on the plan,
producing our first candidate implementation.
Closed-loop self-improvement
Now, we enter
the closed-loop self-improvement phase. First, we
reset the execution environment to the environment
definition ¯e because the agent may have performed
write actions in the past. We also restore the con-
versation history to immediately after generating
the implementation plan, but include summaries of
past appempts (described later).
After running the candidate Python function in
the execution environment using the example in-
vocation provided in the tool definition (line 12),
we instruct the LLM to assess whether the execu-
tion was successful (
ASSESS_TOOL_OUTPUT).
Specifically, we ask the LLM to check whether the
result returned by the tool is in line with the task de-
scription (i.e. if the result is plausible), and whether
the standard output and standard error streams con-
tain any indications of errors. If the LLM deemed
tool execution successful, we have arrived at our
final tool implementation, and exit the loop. Other-
wise, we continue the self-improvement loop.
Next, we instruct the
DIAGNOSE_ERROR
agent to gather information about the error in order
to diagnose its root cause and formulate a plan to
fix it. Importantly, we do not reset the execution en-
vironment – the agent is able to check intermediate
files and outputs created during tool execution.
Then, the LLM re-implements the tool based on
the current implementation, error diagnosis, and
plan to fix the error (
REIMPLEMENT). Finally,
we ask the LLM to summarise the current step
(
SUMMARISE), and append this summary to the
conversation history for the next iteration.
3.3
Execution environment
An important implementation detail is the exe-
cution environment, which is the environment in
which (i) actions (A) are performed throughout the
TOOLMAKER workflow, and (ii) wherein the final
tool created by TOOLMAKER will be executed.
The execution environment itself is stateful. Spe-
cifically, write actions Aw = {
,
,
,
}
may mutate environment state. However, we re-
quire the ability to roll back to previous states, e.g.
on line 10 of Algorithm 1, the execution envir-
onment is restored to the “freshly installed” state
¯e. Furthermore, the execution environment should
be sandboxed from the host system (for security
reasons), and it should be reproducible (so the gen-
erated tool can be executed on any machine).
We satisfy these requirements by implementing
the execution environment as a Docker container
that TOOLMAKER controls via an HTTP server
running inside the container, which can run the pre-
defined actions A. State restoration is achieved via
Docker’s checkpointing functionality.
4
Benchmark
To evaluate our approach, we collect a dataset of
15 diverse tasks spanning multiple scientific dis-
ciplines, which we refer to as TM-BENCH. The
tasks were curated in close collaboration with re-
searchers in medicine and life sciences to reflect
realistic problems in these fields, with a focus on
the medical domain (pathology, radiology, omics),
while also including some tasks from other areas
such as 3D vision, imaging, tabular data analysis,
and natural language processing to ensure broader
coverage of real-world scientific challenges.
Before including a task in TM-BENCH, we
manually implemented the intended tool using the
associated GitHub repository to ensure the task is
well-defined and solvable. This vetting process
gave us confidence that each task is meaningful,
correctly specified, and feasible. The resulting
benchmark covers a range of difficulty levels, from
simple tasks that can be achieved by calling an ex-
isting method, to more complex, multi-step tasks
that require orchestrating multiple function calls,
transforming data, and utilising GPUs.
Task definitions
As shown in Fig. 2 (top), each
task definition consists of: (i) a one-sentence task
description, (ii) the URL to the code repository,
(iii) a list of input arguments, alongside an example
invocation (see below), and (iv) a description of
the expected output. An overview of the tasks and
associated papers can be found in Table 2, and we
provide a full list of all task definitions with their
example invocations in Appendix C.
Invocations
A task invocation specifies a con-
crete value for each input argument, as well
as external files and directories that should be
made accessible from within the execution en-
vironment during the invocation. Indeed, most
tasks in TM-BENCH require external files, e.g.
26097

TOOLMAKER (ours)
OpenHands (Wang et al., 2024)
Task
Invoc.
Tests
Cost
Actions
Tokens
Invoc.
Tests
Cost
Actions
Tokens
Pathology
conch_extract_features (Lu et al., 2024b)
3/3
9/9
$0.35
15 (1⟲)
171,226
3/3
9/9
$0.08
5
51,701
musk_extract_features (Xiang et al., 2025)
3/3
6/6
$1.19
29 (6⟲)
696,386
✗
✗
$0.15
7
97,386
pathfinder_verify_biomarker (Liang et al., 2023)
0/2
4/6
$0.61
27 (1⟲)
356,825
0/2
4/6
$0.08
6
49,414
stamp_extract_features (El Nahhas et al., 2024)
3/3
12/12
$1.12
20 (4⟲)
631,138
0/3
3/12
$0.07
6
42,793
stamp_train_classification_model (El Nahhas et al., 2024)
3/3
9/9
$2.27
33 (9⟲)
1,249,521
0/3
0/9
$0.15
8
87,915
uni_extract_features (Chen et al., 2024)
3/3
9/9
$0.61
16 (4⟲)
326,806
✗
✗
$0.25
10
177,119
Radiology
medsam_inference (Ma et al., 2024)
3/3
6/6
$0.96
18 (6⟲)
508,954
✗
✗
$0.07
5
41,096
nnunet_train_model (Isensee et al., 2020)
0/2
0/4
$2.90
35 (9⟲)
1,792,291
0/2
0/4
$0.12
8
79,231
Omics
cytopus_db (Kunes et al., 2023)
3/3
12/12
$0.41
10 (3⟲)
185,912
✗
✗
$0.36
8
236,217
esm_fold_predict (Verkuil et al., 2022; Hie et al., 2022)
2/3
13/15
$0.66
20 (1⟲)
336,754
✗
✗
$0.11
6
69,493
Other
flowmap_overfit_scene (Smith et al., 2024)
2/2
6/6
$0.70
18 (5⟲)
358,552
✗
✗
$0.36
15
250,787
medsss_generate (Jiang et al., 2025)
3/3
6/6
$0.53
25 (3⟲)
282,771
3/3
6/6
$0.15
10
104,505
modernbert_predict_masked (Warner et al., 2024)
3/3
9/9
$0.66
20 (4⟲)
356,228
✗
✗
$0.13
10
82,930
retfound_feature_vector (Zhou et al., 2023)
3/3
6/6
$0.97
31 (5⟲)
561,936
0/3
0/6
$0.08
4
46,521
tabpfn_predict (Hollmann et al., 2025)
3/3
9/9
$0.23
10 (1⟲)
95,257
3/3
9/9
$0.07
4
36,320
Table 2: Performance of the tools created by TOOLMAKER and the OpenHands baseline (Wang et al., 2024) on
the benchmark tasks. ✗indicates that the environment installation failed. We use ⟲to indicate the number of
self-correcting iterations. Green cells indicate that the tool implementation is correct (all unit tests pass), yellow
indicates that at least one unit test failed, and red indicates that all unit tests failed.
stamp_train_classification_model takes an
input dataset of whole slide images (WSIs) and a
clinical data table, on which to train a classification
model using the STAMP (El Nahhas et al., 2024)
pipeline. Analysing and utilising datasets is a fun-
damental aspect of many real-world scientific tasks,
which is why TM-BENCH explicitly supports this
functionality, unlike many existing code generation
benchmarks (Zhuo et al., 2024; Jain et al., 2024).
Each task definition includes a single example
invocation, which may be used in the tool cre-
ation process. Crucially, this specification does
not include the expected return value, as the goal is
to autonomously implement and execute the task
without prior knowledge of the correct output.
Assessing correctness
TM-BENCH specifies 2-
3 additional test invocations per task, which are
different to the example invocation and held-out
from the tool creation process. We purposefully
chose different input argument values for the test
invocations (different datasets, images, paths, etc.)
to assess whether the implementations would gener-
alise to other inputs, and to ensure that implement-
ations did not ‘hard-code’ the example invocation.
For each test invocation, TM-BENCH includes
unit tests to assess whether the tool produces the
expected output by checking various properties of
the return value and output files. We opted for unit
tests over simple equality checks (e.g. strict or near-
exact matches to reference outputs, as used in previ-
ous benchmarks (Bogin et al., 2024)) because unit
tests can accommodate more complex criteria, such
as verifying the shape of generated feature vectors
or checking that a segmentation model produces
plausibly sized masks. Specifically, we employ
unit tests to verify correctness through assertions
on: structure (dimensions and types of return val-
ues), values (range, accuracy, and statistical proper-
ties of return values), files (existence, format, and
content of files produced by the tool, if applicable),
and execution (errors/crashes).
To ensure an unbiased assessment of tool imple-
mentations, the unit tests and test invocations are
used strictly for evaluation and are not available
during tool creation. TM-BENCH comprises 15
tasks, with a total of 42 test invocations (average
2.8 per task) and 124 unit tests (average 8.3 per
task). We consider a tool implementation correct
only if it passes all unit tests of its test invocations.
5
Results
TM-BENCH can evaluate any “tool maker” that
produces an environment definition
and a tool
implementation
. However, to the best of our
knowledge, no existing approaches are specifically
designed to address the “paper repository →LLM
tool” problem. In order to nonetheless facilitate
comparison with prior work, we adapt the Open-
Hands (Wang et al., 2024) to this setting. Open-
Hands is a software engineering agent that achieves
SOTA performance on SWE-bench (Jimenez et al.,
2024). We instruct OpenHands to generate the
same artifacts as TOOLMAKER: an environment
definition
(expressed as a bash script to be run
in a fresh python:3.12 Docker image to create the
environment state required for the tool to execute)
and a tool implementation
(a Python function).
To ensure a fair comparison, we reuse large parts
of the TOOLMAKER prompts in the prompts we
supply to the OpenHands, and add additional in-
structions to encourage OpenHands to test the arti-
facts it creates. We use gpt-4o for the OpenHands
baseline, but also ablate the choice of LLM in Sec-
tion 5.1. The full prompts for TOOLMAKER and
26098

OpenHands are listed in Appendices D and E.
Performance
In Table 2, we report the perform-
ance of TOOLMAKER and OpenHands on all tasks
in TM-BENCH, reporting correctness, cost, num-
ber of tokens, number of actions performed in the
tool creation process (both stages), and the num-
ber of self-correcting iterations. We consider a
test invocation successful (“Tests” column marked
green ) if all of the unit tests that are associated
with it pass. Similarly, a tool implementation is
correct (“Invoc.” column marked green ) if all of
its test invocations are successful, i.e. all of the unit
tests associated with its test invocations pass.
TOOLMAKER significantly outperforms Open-
Hands, achieving an accuracy of 80% (correctly
implementing 12/15 tasks) while OpenHands was
only able to correctly implement 20% (3/15 tasks).
For the esm_fold_predict (Verkuil et al.,
2022) task, TOOLMAKER generates a partially cor-
rect implementation ( yellow ) that passes two out
of three test invocations. The goal of this task is
to predict the contact map of a protein from its
sequence. Upon inspection, we determined that
the failed test invocation was different from the
other invocations: it contained a mask token in the
input sequence which was not present in the task
definition’s example invocation. However, when
including such a mask token in the example in-
vocation and re-running TOOLMAKER, the tool
implementation passed all test invocations. This
highlights that the example invocation in the task
definition needs to be representative of the task.
By contrast, OpenHands fails to generate correct
tool implementations for most tasks, primarily due
to errors at the environment setup stage. Nearly half
of its environment definitions were invalid, causing
installation scripts to crash before execution. Even
among the tasks where OpenHands successfully
generated an environment definition, only three
implementations passed all unit tests.
This poor performance can largely be attrib-
uted to issues during environment setup.
Spe-
cifically, OpenHands often created installation
scripts without testing them, omitted essential setup
commands previously executed manually, or over-
looked dependencies necessary for tool execution.
In contrast, TOOLMAKER inherently avoids such
pitfalls by automatically capturing every installa-
tion command and resetting the execution environ-
ment between iterations, ensuring reproducible and
robust tool implementations.
Multi-step
tools
A
remarkable
feature
of
TOOLMAKER is that it is able to create tools that
require multiple steps to complete. For example,
the stamp_train_classification_model task
provides a dataset of pathology WSIs and a table
of clinical data, and requires the tool implementa-
tion to use the STAMP pipeline (El Nahhas et al.,
2024) to train a classification model that predicts a
specific biomarker from the WSI images. This task
requires multiple steps to complete: after down-
loading and installing the STAMP repository and
its dependencies, the tool implementation needs to
use STAMP to (1) perform feature extraction on the
WSI images, and (2) train a classification model
using the extracted features and the clinical data.
The self-correcting loop allows TOOLMAKER to
realise that it needs to perform feature extraction
before it can train a classification model, and to
subsequently implement the tool function to per-
form both steps, illustrated in Fig. 3 (right). For
this particular task, TOOLMAKER performs 9 self-
correcting iterations, executing 33 actions in total,
before arriving at the final implementation.
Cost
TOOLMAKER performs an average of 21.8
actions during tool creation, costing on average
$0.94 per tool, while OpenHands performs 7.5
actions on average ($0.15 per tool). The three
tools that OpenHands correctly implemented were
among the cheapest for TOOLMAKER, requiring
the fewest actions and self-correcting iterations.
This shows OpenHands can implement very “easy”
tools, but fails to generalise to more complex tasks.
5.1
Ablations
Paper summaries
Since each task is based on
one or more research papers, we perform an ab-
lation study to determine whether we can inject
useful information from the papers into the tool
creation process. Instead of directly including the
full paper text in the prompts which would require
too many tokens, we first provide the full text to
gpt-4o and instruct it to summarise it with re-
spect to the task at hand. Then, we provide these
task-specific and paper-specific summaries in the
prompts for TOOLMAKER and OpenHands.
The results in Table 3 indicate that including
paper summaries does not increase the performance
of either approach. However, it does decrease the
average number of actions and, for TOOLMAKER,
the average number of self-correcting iterations
required to create the tools. For example, while
TOOLMAKER required 9 iterations (33 actions) to
26099

Method
Tools
Invoc.
Tests
Cost
Actions
TOOLMAKER* (ours)
12/15
37/42
116/124
$0.94
21.8
(with paper summary)
11/15
34/42
113/124
$0.71
18.1
(using o3-mini)
9/15
28/42
107/124
$0.55
14.1
OpenHands* (Wang et al., 2024)
3/15
9/42
31/124
$0.15
7.5
(with paper summary)
3/15
9/42
31/124
$0.12
6.6
(using o3-mini)
1/15
2/42
15/124
$0.04
1.9
(using Claude 3.5 Sonnet)
2/15
6/42
19/124
$0.13
5.2
Table 3: Ablation results. Rows marked with asterisk
correspond to the results in Table 2. We report the num-
ber of correct tools, invocations, and tests, as well as the
per-tool average cost and number of actions performed.
create the stamp_train_classification_model
tool, this decreased to only 5 iterations (15 actions)
when using the paper summary (see Appendix B.1).
Choice of LLM
We also evaluate TOOLMAKER
and OpenHands using OpenAI’s o3-mini model
instead of gpt-4o, and find that while this reduces
cost, it also degrades performance in both cases.
Finally, since OpenHands achieved SOTA perform-
ance on SWE-bench (Jimenez et al., 2024) using
Claude 3.5 Sonnet (Anthropic, 2024), we re-run the
OpenHands baseline using this model, but find that
it performs worse than using gpt-4o (see Table 3).
6
Conclusion
In this work, we showed that autonomous tool
creation can go beyond simple Python functions
and produce tools for real-world scientific tasks.
We introduced TOOLMAKER, a framework that
autonomously transforms scientific code repos-
itories into LLM-compatible tools, potentially
drastically reducing the technical overhead in fu-
ture for developing agents with specialised tool-
sets.
In evaluations across multiple scientific
domains, TOOLMAKER surpassed the state-of-
the-art software engineering agent, OpenHands,
achieving 80% accuracy. Additionally, we release
TM-BENCH as a comprehensive benchmark to
spur further advancements in agentic tool creation.
We acknowledge that automated tool creation in
life sciences carries significant risks that require
careful consideration. The ability to autonomously
implement complex biochemical tools could po-
tentially be misused for creating harmful agents
or bioweapons. Additionally, fully automated re-
search systems might inadvertently generate dan-
gerous compounds or protocols without proper
oversight. These risks underscore the importance
of developing robust safety measures and ethical
guidelines alongside technical capabilities. Non-
etheless, by removing technical barriers to tool
creation, TOOLMAKER brings us closer to a future
where the pace of scientific discovery is limited by
computational capacity, not human resources.
Acknowledgments
We
thank
Junhao
Liang,
Michaela
Unger,
and David Charatan for contributing tasks to
TM-BENCH. We also appreciate Jan Clusmann,
Tim Lenz, and Lina Hadji-Kyriacou for their feed-
back on the manuscript, and thank Nathaly Dongo
and Annelies Blätterlein for logo design.
Funding
GW is supported by SCADS.AI,
Lothian NHS, and in part by funding from the
European Union’s Horizon 2020 research and
innovation programme (KATY, 101017453). JNK
is supported by the German Cancer Aid (DEC-
ADE, 70115166), the German Federal Ministry of
Education and Research (PEARL, 01KD2104C;
CAMINO, 01EO2101; TRANSFORM LIVER,
031L0312A; TANGERINE, 01KT2302 through
ERA-NET Transcan; Come2Data, 16DKZ2044A;
DEEP-HCC,
031L0315A;
DECIPHER-M,
01KD2420A;
NextBIG,
01ZU2402A),
the
German Academic Exchange Service (SECAI,
57616814), the German Federal Joint Committee
(TransplantKI,
01VSF21048),
the
European
Union’s Horizon Europe research and innovation
programme (ODELIA, 101057091;
GENIAL,
101096312), the European Research Council (ERC;
NADIR, 101114631), the National Institutes of
Health (EPICO, R01 CA263318) and the National
Institute for Health and Care Research (NIHR)
Leeds Biomedical Research Centre (grant number
NIHR203331). The views expressed are those of
the author(s) and not necessarily those of the NHS,
the NIHR or the Department of Health and Social
Care.
This work was funded by the European
Union. Views and opinions expressed are however
those of the author(s) only and do not necessarily
reflect those of the European Union. Neither the
European Union nor the granting authority can be
held responsible for them.
Limitations
While TOOLMAKER addresses the challenge of
tool creation, we acknowledge that fully autonom-
ous scientific discovery remains constrained by
physical experimentation. TOOLMAKER does not
address this aspect, but we believe that with an in-
creasing proportion of life science research being
conducted in silico, it provides a building block for
autonomous scientific workflows in future.
Our framework assumes that the referenced code
repositories are reasonably well-structured, up-to-
26100

date, and documented. In practice, however, open-
source repositories may be poorly documented or
incomplete, making them challenging to install
autonomously. In fact, there is no guarantee that
any given repository will be installable and usable
as a tool. For TM-BENCH, we manually curated
the tasks such that we were able to successfully in-
stall and use the repository ourselves. This way, we
ensured the tasks were possible in the first place.
While TM-BENCH contains over 100 unit tests,
passing these tests does not guarantee correctness
in all real-world scenarios. Scientific workflows of-
ten involve edge cases or unexpected patterns that
are not captured by a small set of tests. Moreover,
high-stakes applications such as clinical research
would naturally demand additional layers of rigor-
ous validation and oversight by domain experts.
Finally, while TM-BENCH pins the exact com-
mits of the referenced repositories, external factors
such as repository deletion, force-pushing changes,
or renaming branches, could affect reproducibility.
References
Anthropic.
2024.
The
Claude
3
Model
Family:
Opus,
Sonnet,
Haiku.
https://www-cdn.anthropic.com/
de8ba9b01c9ab7cbabf5c33b80b7bbc618857627/
Model_Card_Claude_3.pdf.
[Accessed 20-01-
2025].
Ben Bogin, Kejuan Yang, Shashank Gupta, Kyle
Richardson, Erin Bransom, Peter Clark, Ashish
Sabharwal, and Tushar Khot. 2024. SUPER: Evalu-
ating agents on setting up and executing tasks from
research repositories. In Proceedings of the 2024
Conference on Empirical Methods in Natural Lan-
guage Processing, pages 12622–12645. Association
for Computational Linguistics.
Andres M. Bran, Sam Cox, Oliver Schilter, Carlo Bal-
dassari, Andrew D. White, and Philippe Schwaller.
2024. Augmenting large language models with chem-
istry tools. Nature Machine Intelligence, 6(5):525–
535.
Tianle Cai, Xuezhi Wang, Tengyu Ma, Xinyun Chen,
and Denny Zhou. 2024. Large language models as
tool makers. In The Twelfth International Conference
on Learning Representations.
Jun Shern Chan, Neil Chowdhury, Oliver Jaffe, James
Aung, Dane Sherburn, Evan Mays, Giulio Starace,
Kevin Liu, Leon Maksin, Tejal Patwardhan, Lilian
Weng, and Aleksander M ˛adry. 2024. Mle-bench:
Evaluating machine learning agents on machine
learning engineering. Preprint, arXiv:2410.07095.
Richard J. Chen, Tong Ding, Ming Y. Lu, Drew
F. K. Williamson, Guillaume Jaume, Andrew H.
Song, Bowen Chen, Andrew Zhang, Daniel Shao,
Muhammad Shaban, Mane Williams, Lukas Olden-
burg, Luca L. Weishaupt, Judy J. Wang, Anurag
Vaidya, Long Phi Le, Georg Gerber, Sharifa Sahai,
Walt Williams, and Faisal Mahmood. 2024. Towards
a general-purpose foundation model for computa-
tional pathology. Nature Medicine, 30(3):850–862.
Ziru Chen, Shijie Chen, Yuting Ning, Qianheng Zhang,
Boshi Wang, Botao Yu, Yifei Li, Zeyi Liao, Chen
Wei, Zitong Lu, Vishal Dey, Mingyi Xue, Frazier N.
Baker, Benjamin Burns, Daniel Adu-Ampratwum,
Xuhui Huang, Xia Ning, Song Gao, Yu Su, and Huan
Sun. 2025. Scienceagentbench: Toward rigorous as-
sessment of language agents for data-driven scientific
discovery. Preprint, arXiv:2410.05080.
Ning Ding, Shang Qu, Linhai Xie, Yifei Li, Zaoqu Liu,
Kaiyan Zhang, Yibai Xiong, Yuxin Zuo, Zhangren
Chen, Ermo Hua, Xingtai Lv, Youbang Sun, Yang Li,
Dong Li, Fuchu He, and Bowen Zhou. 2024. Auto-
mating exploratory proteomics research via language
models. Preprint, arXiv:2411.03743.
Omar S. M. El Nahhas, Marko van Treeck, Georg
Wölflein, Michaela Unger, Marta Ligero, Tim Lenz,
Sophia J. Wagner, Katherine J. Hewitt, Firas Khader,
Sebastian Foersch, Daniel Truhn, and Jakob Nikolas
Kather. 2024. From whole-slide image to biomarker
prediction: end-to-end weakly supervised deep learn-
ing in computational pathology. Nature Protocols.
Dyke Ferber, Omar S. M. El Nahhas, Georg Wölflein,
Isabella C. Wiest, Jan Clusmann, Marie-Elisabeth
Leßman, Sebastian Foersch, Jacqueline Lammert,
Maximilian Tschochohei,
Dirk Jäger,
Manuel
Salto-Tellez, Nikolaus Schultz, Daniel Truhn, and
Jakob Nikolas Kather. 2024. Autonomous artificial
intelligence agents for clinical decision making in
oncology. Preprint, arXiv:2404.04667.
Shanghua Gao, Ada Fang, Yepeng Huang, Valentina Gi-
unchiglia, Ayush Noori, Jonathan Richard Schwarz,
Yasha Ektefaie, Jovana Kondic, and Marinka Zitnik.
2024. Empowering biomedical discovery with ai
agents. Cell, 187(22):6125–6151.
Gemini Team. 2024.
Gemini:
A family of
highly capable multimodal models.
Preprint,
arXiv:2312.11805.
Brian Hie, Salvatore Candido, Zeming Lin, Ori Ka-
beli, Roshan Rao, Nikita Smetanin, Tom Sercu, and
Alexander Rives. 2022. A high-level programming
language for generative protein design.
Preprint,
bioRxiv:2022.12.21.521526.
Noah Hollmann, Samuel Müller, Lennart Purucker,
Arjun Krishnakumar, Max Körfer, Shi Bin Hoo,
Robin Tibor Schirrmeister, and Frank Hutter. 2025.
Accurate predictions on small data with a tabular
foundation model. Nature, 637(8045):319–326.
Sirui Hong, Mingchen Zhuge, Jonathan Chen, Xiawu
Zheng, Yuheng Cheng, Jinlin Wang, Ceyao Zhang,
Zili Wang, Steven Ka Shing Yau, Zijuan Lin, Liyang
26101

Zhou, Chenyu Ran, Lingfeng Xiao, Chenglin Wu,
and Jürgen Schmidhuber. 2024. MetaGPT: Meta pro-
gramming for a multi-agent collaborative framework.
In The Twelfth International Conference on Learning
Representations.
Chip Huyen. 2024. AI engineering. O’Reilly Media,
Sebastopol, CA.
Fabian Isensee, Paul F. Jaeger, Simon A. A. Kohl, Jens
Petersen, and Klaus H. Maier-Hein. 2020. nnu-net:
a self-configuring method for deep learning-based
biomedical image segmentation. Nature Methods,
18(2):203–211.
Naman Jain, King Han, Alex Gu, Wen-Ding Li, Fanjia
Yan, Tianjun Zhang, Sida Wang, Armando Solar-
Lezama, Koushik Sen, and Ion Stoica. 2024. Live-
codebench: Holistic and contamination free evalu-
ation of large language models for code. Preprint,
arXiv:2403.07974.
Shuyang Jiang, Yusheng Liao, Zhe Chen, Ya Zhang,
Yanfeng Wang, and Yu Wang. 2025. Meds3: Towards
medical small language models with self-evolved
slow thinking. Preprint, arXiv:2501.12051.
Carlos E Jimenez, John Yang, Alexander Wettig,
Shunyu Yao, Kexin Pei, Ofir Press, and Karthik R
Narasimhan. 2024. SWE-bench: Can language mod-
els resolve real-world github issues? In The Twelfth
International Conference on Learning Representa-
tions.
Qiao Jin, Zhizheng Wang, Yifan Yang, Qingqing Zhu,
Donald Wright, Thomas Huang, W John Wilbur, Zhe
He, Andrew Taylor, Qingyu Chen, and Zhiyong Lu.
2024. Agentmd: Empowering language agents for
risk prediction with large-scale clinical tool learning.
Preprint, arXiv:2402.13225.
Yubin Kim, Chanwoo Park, Hyewon Jeong, Yik Siu
Chan, Xuhai Xu, Daniel McDuff, Hyeonhoon Lee,
Marzyeh Ghassemi, Cynthia Breazeal, and Hae Won
Park. 2024. Mdagents: An adaptive collaboration
of llms for medical decision-making. In The Thirty-
eighth Annual Conference on Neural Information
Processing Systems.
Russell Z. Kunes, Thomas Walle, Max Land, Tal Nawy,
and Dana Pe’er. 2023. Supervised discovery of inter-
pretable gene programs from single-cell data. Nature
Biotechnology, 42(7):1084–1095.
Haitao Li, Junjie Chen, Jingli Yang, Qingyao Ai, Wei
Jia, Youfeng Liu, Kai Lin, Yueyue Wu, Guozhi
Yuan, Yiran Hu, Wuyue Wang, Yiqun Liu, and Min-
lie Huang. 2024. Legalagentbench: Evaluating llm
agents in legal domain. Preprint, arXiv:2412.17259.
Junkai Li, Yunghwei Lai, Weitao Li, Jingyi Ren, Meng
Zhang, Xinhui Kang, Siyu Wang, Peng Li, Ya-Qin
Zhang, Weizhi Ma, and Yang Liu. 2025. Agent hos-
pital: A simulacrum of hospital with evolvable med-
ical agents. Preprint, arXiv:2405.02957.
Junhao Liang, Weisheng Zhang, Jianghui Yang, Mei-
long Wu, Qionghai Dai, Hongfang Yin, Ying Xiao,
and Lingjie Kong. 2023. Deep learning supported
discovery of biomarkers for clinical prognosis of liver
cancer. Nature Machine Intelligence, 5(4):408–420.
Tianyang Liu, Canwen Xu, and Julian McAuley. 2024.
Repobench: Benchmarking repository-level code
auto-completion systems. In The Twelfth Interna-
tional Conference on Learning Representations.
Llama Team. 2024. The llama 3 herd of models. Pre-
print, arXiv:2407.21783.
Chris Lu, Cong Lu, Robert Tjarko Lange, Jakob Foer-
ster, Jeff Clune, and David Ha. 2024a. The ai scient-
ist: Towards fully automated open-ended scientific
discovery. Preprint, arXiv:2408.06292.
Ming Y. Lu, Bowen Chen, Drew F. K. Williamson,
Richard J. Chen, Ivy Liang, Tong Ding, Guillaume
Jaume, Igor Odintsov, Long Phi Le, Georg Ger-
ber, Anil V. Parwani, Andrew Zhang, and Faisal
Mahmood. 2024b. A visual-language foundation
model for computational pathology. Nature Medi-
cine, 30(3):863–874.
Jun Ma, Yuting He, Feifei Li, Lin Han, Chenyu You,
and Bo Wang. 2024. Segment anything in medical
images. Nature Communications, 15(1).
Bodhisattwa Prasad Majumder, Harshit Surana, Dhruv
Agarwal, Bhavana Dalvi Mishra, Abhijeetsingh
Meena, Aryan Prakhar, Tirth Vora, Tushar Khot,
Ashish Sabharwal, and Peter Clark. 2024. Discov-
erybench: Towards data-driven discovery with large
language models. Preprint, arXiv:2407.01725.
Minh Huynh Nguyen, Thang Phan Chau, Phong X.
Nguyen, and Nghi D. Q. Bui. 2024. Agilecoder: Dy-
namic collaborative agents for software development
based on agile methodology.
OpenAI. 2025. Openai developer platform. https:
//platform.openai.com/docs. [Accessed 15-02-
2025].
Long Ouyang, Jeffrey Wu, Xu Jiang, Diogo Almeida,
Carroll Wainwright, Pamela Mishkin, Chong Zhang,
Sandhini Agarwal, Katarina Slama, Alex Ray, John
Schulman, Jacob Hilton, Fraser Kelton, Luke Miller,
Maddie Simens, Amanda Askell, Peter Welinder,
Paul F Christiano, Jan Leike, and Ryan Lowe. 2022.
Training language models to follow instructions with
human feedback. In Advances in Neural Information
Processing Systems, volume 35, pages 27730–27744.
Curran Associates, Inc.
Chen Qian, Wei Liu, Hongzhang Liu, Nuo Chen, Yufan
Dang, Jiahao Li, Cheng Yang, Weize Chen, Yusheng
Su, Xin Cong, Juyuan Xu, Dahai Li, Zhiyuan Liu,
and Maosong Sun. 2024. ChatDev: Communicative
agents for software development. In Proceedings
of the 62nd Annual Meeting of the Association for
Computational Linguistics, pages 15174–15186. As-
sociation for Computational Linguistics.
26102

Cheng Qian, Chi Han, Yi Fung, Yujia Qin, Zhiyuan
Liu, and Heng Ji. 2023. CREATOR: Tool creation
for disentangling abstract and concrete reasoning of
large language models. In The 2023 Conference on
Empirical Methods in Natural Language Processing.
Yujia Qin, Shengding Hu, Yankai Lin, Weize Chen,
Ning Ding, Ganqu Cui, Zheni Zeng, Xuanhe Zhou,
Yufei Huang, Chaojun Xiao, Chi Han, Yi Ren Fung,
Yusheng Su, Huadong Wang, Cheng Qian, Run-
chu Tian, Kunlun Zhu, Shihao Liang, Xingyu Shen,
Bokai Xu, Zhen Zhang, Yining Ye, Bowen Li, Ziwei
Tang, Jing Yi, Yuzhang Zhu, Zhenning Dai, Lan Yan,
Xin Cong, Yaxi Lu, Weilin Zhao, Yuxiang Huang,
Junxi Yan, Xu Han, Xian Sun, Dahai Li, Jason Phang,
Cheng Yang, Tongshuang Wu, Heng Ji, Guoliang Li,
Zhiyuan Liu, and Maosong Sun. 2024. Tool learning
with foundation models. ACM Comput. Surv., 57(4).
Timo Schick, Jane Dwivedi-Yu, Roberto Dessi, Roberta
Raileanu,
Maria Lomeli,
Eric Hambro,
Luke
Zettlemoyer, Nicola Cancedda, and Thomas Scialom.
2023. Toolformer: Language models can teach them-
selves to use tools. In Advances in Neural Inform-
ation Processing Systems, volume 36, pages 68539–
68551. Curran Associates, Inc.
Samuel Schmidgall, Yusheng Su, Ze Wang, Ximeng
Sun, Jialian Wu, Xiaodong Yu, Jiang Liu, Zicheng
Liu, and Emad Barsoum. 2025. Agent laboratory:
Using llm agents as research assistants. Preprint,
arXiv:2501.04227.
Dominik Schmidt, Zhengyao Jiang, and Yuxiang Wu.
2024. Introducing Weco AIDE — weco.ai. https:
//www.weco.ai/blog/technical-report.
[Ac-
cessed 20-01-2025].
Zhihong Shao, Peiyi Wang, Qihao Zhu, Runxin Xu,
Junxiao Song, Xiao Bi, Haowei Zhang, Mingchuan
Zhang, Y. K. Li, Y. Wu, and Daya Guo. 2024.
Deepseekmath: Pushing the limits of mathemat-
ical reasoning in open language models. Preprint,
arXiv:2402.03300.
Karan Singhal, Shekoofeh Azizi, Tao Tu, S. Sara Mah-
davi, Jason Wei, Hyung Won Chung, Nathan Scales,
Ajay Tanwani, Heather Cole-Lewis, Stephen Pfohl,
Perry Payne, Martin Seneviratne, Paul Gamble, Chris
Kelly, Abubakr Babiker, Nathanael Schärli, Aakank-
sha Chowdhery, Philip Mansfield, Dina Demner-
Fushman, Blaise Agüera y Arcas, Dale Webster,
Greg S. Corrado, Yossi Matias, Katherine Chou,
Juraj Gottweis, Nenad Tomasev, Yun Liu, Alvin Ra-
jkomar, Joelle Barral, Christopher Semturs, Alan
Karthikesalingam, and Vivek Natarajan. 2023. Large
language models encode clinical knowledge. Nature,
620(7972):172–180.
Cameron Smith, David Charatan, Ayush Tewari, and
Vincent Sitzmann. 2024. Flowmap: High-quality
camera poses, intrinsics, and depth via gradient des-
cent. Preprint, arXiv:2404.15259.
Kyle Swanson, Wesley Wu, Nash L. Bulaong, John E.
Pak, and James Zou. 2024. The virtual lab: Ai agents
design new sars-cov-2 nanobodies with experimental
validation.
Xiangru Tang, Yuliang Liu, Zefan Cai, Yanjun Shao,
Junjie Lu, Yichi Zhang, Zexuan Deng, Helan Hu,
Kaikai An, Ruijun Huang, Shuzheng Si, Sheng
Chen, Haozhe Zhao, Liang Chen, Yan Wang, Tianyu
Liu, Zhiwei Jiang, Baobao Chang, Yin Fang, Yujia
Qin, Wangchunshu Zhou, Yilun Zhao, Arman Co-
han, and Mark Gerstein. 2024. Ml-bench: Evaluat-
ing large language models and agents for machine
learning tasks on repository-level code. Preprint,
arXiv:2311.09835.
Karthik Valmeekam, Sarath Sreedharan, Matthew Mar-
quez, Alberto Olmo, and Subbarao Kambhampati.
2023. On the planning abilities of large language
models (a critical investigation with a proposed
benchmark). Preprint, arXiv:2302.06706.
Robert Verkuil,
Ori Kabeli,
Yilun Du,
Basile
I. M. Wicky, Lukas F. Milles, Justas Dauparas,
David Baker, Sergey Ovchinnikov, Tom Sercu,
and Alexander Rives. 2022.
Language mod-
els generalize beyond natural proteins.
Preprint,
bioRxiv:2022.12.21.521521.
Xingyao Wang, Boxuan Li, Yufan Song, Frank F. Xu,
Xiangru Tang, Mingchen Zhuge, Jiayi Pan, Yueqi
Song, Bowen Li, Jaskirat Singh, Hoang H. Tran, Fuqi-
ang Li, Ren Ma, Mingzhang Zheng, Bill Qian, Yan-
jun Shao, Niklas Muennighoff, Yizhe Zhang, Biny-
uan Hui, Junyang Lin, Robert Brennan, Hao Peng,
Heng Ji, and Graham Neubig. 2024. Openhands: An
open platform for ai software developers as generalist
agents. Preprint, arXiv:2407.16741.
Benjamin Warner, Antoine Chaffin, Benjamin Clavié,
Orion Weller, Oskar Hallström, Said Taghadouini,
Alexis Gallagher, Raja Biswas, Faisal Ladhak, Tom
Aarsen, Nathan Cooper, Griffin Adams, Jeremy
Howard, and Iacopo Poli. 2024.
Smarter, better,
faster, longer: A modern bidirectional encoder for
fast, memory efficient, and long context finetuning
and inference. Preprint, arXiv:2412.13663.
Georg Wölflein, Dyke Ferber, Asier Rabasco Me-
neghetti, Omar S. M. El Nahhas, Daniel Truhn, Zun-
amys I. Carrero, David J. Harrison, Ognjen Arand-
jelovi´c, and Jakob Nikolas Kather. 2024. A good
feature extractor is all you need for weakly super-
vised pathology slide classification. In European
Conference on Computer Vision (ECCV). Springer.
BioImage Computing Workshop.
Chun Xia, Yinlin Deng, Soren Dunn, and Lingming
Zhang. 2024. Agentless: Demystifying llm-based
software engineering agents.
Jinxi Xiang, Xiyue Wang, Xiaoming Zhang, Yinghua
Xi, Feyisope Eweje, Yijiang Chen, Yuchen Li, Colin
Bergstrom, Matthew Gopaulchan, Ted Kim, Kun-
Hsing Yu, Sierra Willens, Francesca Maria Olguin,
Jeffrey J. Nirschl, Joel Neal, Maximilian Diehn, Sen
Yang, and Ruijiang Li. 2025.
A vision-language
foundation model for precision oncology. Nature.
26103

Qi Xin, Quyu Kong, Hongyi Ji, Yue Shen, Yuqi Liu,
Yan Sun, Zhilin Zhang, Zhaorong Li, Xunlong Xia,
Bing Deng, and Yinqi Bai. 2024. Bioinformatics
agent (bia): Unleashing the power of large language
models to reshape bioinformatics workflow.
John Yang, Carlos E Jimenez, Alexander Wettig, Kilian
Lieret, Shunyu Yao, Karthik R Narasimhan, and Ofir
Press. 2024.
SWE-agent: Agent-computer inter-
faces enable automated software engineering.
In
The Thirty-eighth Annual Conference on Neural In-
formation Processing Systems.
Lifan Yuan, Yangyi Chen, Xingyao Wang, Yi Fung,
Hao Peng, and Heng Ji. 2024. CRAFT: Customizing
LLMs by creating and retrieving from specialized
toolsets. In The Twelfth International Conference on
Learning Representations.
Siqi Zhou, Lukas Brunke, Allen Tao, Adam W. Hall,
Federico Pizarro Bejarano, Jacopo Panerati, and An-
gela P. Schoellig. 2024. What is the impact of re-
leasing code with publications?: Statistics from the
machine learning, robotics, and control communities.
IEEE Control Systems, 44(4):38–46.
Yukun Zhou, Mark A. Chia, Siegfried K. Wagner,
Murat S. Ayhan, Dominic J. Williamson, Robbert R.
Struyven, Timing Liu, Moucheng Xu, Mateo G. Loz-
ano, Peter Woodward-Court, Yuka Kihara, Naomi
Allen, John E. J. Gallacher, Thomas Littlejohns,
Tariq Aslam, Paul Bishop, Graeme Black, Panagiotis
Sergouniotis, Denize Atan, Andrew D. Dick, Cathy
Williams, Sarah Barman, Jenny H. Barrett, Sarah
Mackie, Tasanee Braithwaite, Roxana O. Carare,
Sarah Ennis, Jane Gibson, Andrew J. Lotery, Jay
Self, Usha Chakravarthy, Ruth E. Hogg, Euan Pa-
terson, Jayne Woodside, Tunde Peto, Gareth Mckay,
Bernadette Mcguinness, Paul J. Foster, Konstantinos
Balaskas, Anthony P. Khawaja, Nikolas Pontikos,
Jugnoo S. Rahi, Gerassimos Lascaratos, Praveen J.
Patel, Michelle Chan, Sharon Y. L. Chua, Alexan-
der Day, Parul Desai, Cathy Egan, Marcus Frut-
tiger, David F. Garway-Heath, Alison Hardcastle,
Sir Peng T. Khaw, Tony Moore, Sobha Sivaprasad,
Nicholas Strouthidis, Dhanes Thomas, Adnan Tufail,
Ananth C. Viswanathan, Bal Dhillon, Tom Macgil-
livray, Cathie Sudlow, Veronique Vitart, Alexander
Doney, Emanuele Trucco, Jeremy A. Guggeinheim,
James E. Morgan, Chris J. Hammond, Katie Wil-
liams, Pirro Hysi, Simon P. Harding, Yalin Zheng,
Robert Luben, Phil Luthert, Zihan Sun, Martin McK-
ibbin, Eoin O’Sullivan, Richard Oram, Mike Weedon,
Chris G. Owen, Alicja R. Rudnicka, Naveed Sattar,
David Steel, Irene Stratton, Robyn Tapp, Max M.
Yates, Axel Petzold, Savita Madhusudhan, Andre
Altmann, Aaron Y. Lee, Eric J. Topol, Alastair K.
Denniston, Daniel C. Alexander, and Pearse A.
Keane. 2023.
A foundation model for generaliz-
able disease detection from retinal images. Nature,
622(7981):156–163.
Terry Yue Zhuo, Minh Chien Vu, Jenny Chim, Han Hu,
Wenhao Yu, Ratnadira Widyasari, Imam Nur Bani
Yusuf, Haolan Zhan, Junda He, Indraneil Paul, Simon
Brunner, Chen Gong, Thong Hoang, Armel Randy
Zebaze, Xiaoheng Hong, Wen-Ding Li, Jean Kad-
dour, Ming Xu, Zhihan Zhang, Prateek Yadav, Na-
man Jain, Alex Gu, Zhoujun Cheng, Jiawei Liu,
Qian Liu, Zijian Wang, David Lo, Binyuan Hui,
Niklas Muennighoff, Daniel Fried, Xiaoning Du,
Harm de Vries, and Leandro Von Werra. 2024. Big-
codebench: Benchmarking code generation with di-
verse function calls and complex instructions. Pre-
print, arXiv:2406.15877.
Eric Zimmermann, Eugene Vorontsov, Julian Viret,
Adam
Casson,
Michal
Zelechowski,
George
Shaikovski, Neil Tenenholtz, James Hall, David
Klimstra, Razik Yousfi, Thomas Fuchs, Nicolo Fusi,
Siqi Liu, and Kristen Severson. 2024. Virchow2:
Scaling self-supervised mixed magnification models
in pathology. Preprint, arXiv:2408.00738.
26104

A
TOOLMAKER
A.1
Detailed workflow description
We provide a detailed description of every step in the TOOLMAKER workflow to supplement Algorithm 1
and our discussion thereof in Section 3.2.
A.1.1
Setting up the environment
The environment definition is a state of the world (e.g. the operating system) that is required for the
tool created by TOOLMAKER to execute. We can represent this state as a sequence of actions (e.g. bash
commands or instructions in a Dockerfile, as shown in Fig. 2, left) that mutate a known initial state (e.g. a
freshly installed operating system) to the state required for the tool to execute.
To obtain the state of the execution environment necessary for the tool to execute, we employ an
agent
that is instructed to install and set up the repository (we provide the full prompt in Appendix D).
This agent will clone and explore the repository, read documentation, and download any dependencies
it deems necessary such as models, datasets, and libraries. Each of these steps involve planning and
learning from previous mistakes such as error logs arising during execution. The agent begins with a
clean state (a python:3.12 Docker image). Importantly, we record all actions
that the agent performs.
Since each of the write actions can be expressed as a bash command, we can simply concatenate the bash
representations of all recorded write actions to obtain the environment definition in the form of a bash
script or Dockerfile.
A.1.2
Initial tool implementation
Equipped with the environment definition, which allows TOOLMAKER to reset the state of the execution
environment to the state in which the tool should be executed, it can now implement the tool itself. Note
that we do not carry over the conversation history from the previous stage, in order to not pollute the
context window with a large number of messages that are irrelevant for this stage.
Gather information
We first instruct an agent to explore the installed repository and gather all
information necessary to implement the tool. We include the tool definition (see Fig. 3, top left) as a
Python function signature with a docstring in the initial prompt, so that it can use the information it has
already gathered to create the plan.
Create a plan
Then, we perform an LLM call to create a step-by-step plan for the tool implementa-
tion. Here, we keep all of the agent’s messages (including actions and observations) in the conversation
history, so that it can use the information it has already gathered to create the plan.
Implement the tool function
Next, we instruct the LLM to implement the tool based on the plan.
Again, we keep the entire conversation history in the context window of the LLM call, so that it can refer
to previous messages. We now have our first candidate implementation of the tool function.
We use OpenAI’s o1-mini-2024-09-12 model for the planning step as well as the implementation
step, to take advantage of its reasoning and code generation capabilities.
A.1.3
Closed-loop self-improvement
Run the tool
Before executing the candidate implementation, we reset the execution environment to
the environment definition because the agent may have performed write actions in the past (either in the
process of exploring the repository, or in a previous iteration of the loop). Then, we run the candidate
Python function in the execution environment, using the example invocation provided in the tool definition.
Assess tool execution
We instruct the LLM to assess whether the execution was successful, based
on the result returned by the function, as well as the standard output and standard error streams produced
during function execution. Specifically, we ask the LLM to check whether the result returned by the tool
is in line with the task description (i.e. if the result is plausible), and whether the standard output and
standard error streams contain any indications of errors. If the LLM determines that the tool execution was
successful, we have arrived at our final tool implementation, and exit the loop. Otherwise, we continue
the self-improvement loop.
26105

TOOLMAKER (ours)
OpenHands (Wang et al., 2024)
Task
Invoc.
Tests
Cost
Actions
Tokens
Invoc.
Tests
Cost
Actions
Tokens
Pathology
conch_extract_features (Lu et al., 2024b)
3/3
9/9
$0.57
15 (2⟲)
274,256
✗
✗
$0.10
6
65,957
musk_extract_features (Xiang et al., 2025)
0/3
3/6
$0.68
19 (3⟲)
355,561
✗
✗
$0.12
6
77,416
pathfinder_verify_biomarker (Liang et al., 2023)
0/2
4/6
$0.75
25 (1⟲)
473,741
0/2
4/6
$0.11
7
69,545
stamp_extract_features (El Nahhas et al., 2024)
3/3
12/12
$1.13
20 (4⟲)
649,284
0/3
3/12
$0.08
7
52,596
stamp_train_classification_model (El Nahhas et al., 2024)
3/3
9/9
$0.76
15 (5⟲)
393,150
0/3
0/9
$0.25
11
143,934
uni_extract_features (Chen et al., 2024)
3/3
9/9
$0.53
14 (3⟲)
268,481
3/3
9/9
$0.14
5
87,344
Radiology
medsam_inference (Ma et al., 2024)
3/3
6/6
$0.40
11 (2⟲)
181,604
0/3
0/6
$0.09
4
50,053
nnunet_train_model (Isensee et al., 2020)
0/2
0/4
$0.32
13
213,901
0/2
0/4
$0.11
4
65,458
Omics
cytopus_db (Kunes et al., 2023)
3/3
12/12
$0.89
15 (8⟲)
501,078
0/3
0/12
$0.13
7
87,369
esm_fold_predict (Verkuil et al., 2022; Hie et al., 2022)
2/3
13/15
$0.96
22 (1⟲)
563,759
✗
✗
$0.10
5
63,723
Other
flowmap_overfit_scene (Smith et al., 2024)
2/2
6/6
$2.14
42 (12⟲)
1,204,247
✗
✗
$0.07
4
46,316
medsss_generate (Jiang et al., 2025)
3/3
6/6
$0.76
28 (3⟲)
423,235
3/3
6/6
$0.15
12
101,581
modernbert_predict_masked (Warner et al., 2024)
3/3
9/9
$0.26
11 (1⟲)
106,456
✗
✗
$0.14
9
84,959
retfound_feature_vector (Zhou et al., 2023)
3/3
6/6
$0.29
11 (2⟲)
126,270
0/3
0/6
$0.15
7
96,780
tabpfn_predict (Hollmann et al., 2025)
3/3
9/9
$0.26
10 (1⟲)
104,749
3/3
9/9
$0.08
5
49,357
Table 4: Results (with paper summary in context).
TOOLMAKER (ours)
OpenHands (Wang et al., 2024)
Task
Invoc.
Tests
Cost
Actions
Tokens
Invoc.
Tests
Cost
Actions
Tokens
Pathology
conch_extract_features (Lu et al., 2024b)
0/3
6/9
$0.22
15 (2⟲)
232,441
✗
✗
$0.04
0
28,880
musk_extract_features (Xiang et al., 2025)
0/3
3/6
$0.24
18 (2⟲)
247,840
✗
✗
$0.03
2
22,820
pathfinder_verify_biomarker (Liang et al., 2023)
0/2
4/6
$0.10
11 (1⟲)
85,312
✗
✗
$0.04
2
25,797
stamp_extract_features (El Nahhas et al., 2024)
3/3
12/12
$0.18
14 (1⟲)
187,972
✗
✗
$0.04
2
23,343
stamp_train_classification_model (El Nahhas et al., 2024)
3/3
9/9
$0.38
17 (4⟲)
403,138
0/3
0/9
$0.04
2
32,516
uni_extract_features (Chen et al., 2024)
3/3
9/9
$0.58
12 (8⟲)
563,488
0/3
0/9
$0.03
2
22,905
Radiology
medsam_inference (Ma et al., 2024)
0/3
3/6
$0.87
15 (14⟲)
868,977
0/3
0/6
$0.04
2
23,410
nnunet_train_model (Isensee et al., 2020)
0/2
0/4
$2.74
25 (30⟲)
3,165,597
0/2
0/4
$0.06
2
36,563
Omics
cytopus_db (Kunes et al., 2023)
3/3
12/12
$0.22
9 (4⟲)
214,546
0/3
0/12
$0.04
2
23,522
esm_fold_predict (Verkuil et al., 2022; Hie et al., 2022)
2/3
13/15
$0.24
11 (1⟲)
270,976
0/3
9/15
$0.03
2
22,344
Other
flowmap_overfit_scene (Smith et al., 2024)
2/2
6/6
$0.29
18 (3⟲)
295,054
2/2
6/6
$0.04
2
24,332
medsss_generate (Jiang et al., 2025)
3/3
6/6
$0.60
15 (8⟲)
653,697
✗
✗
$0.03
2
21,574
modernbert_predict_masked (Warner et al., 2024)
3/3
9/9
$0.54
14 (4⟲)
589,902
✗
✗
$0.03
2
22,617
retfound_feature_vector (Zhou et al., 2023)
3/3
6/6
$0.51
10 (8⟲)
490,555
✗
✗
$0.03
2
23,345
tabpfn_predict (Hollmann et al., 2025)
3/3
9/9
$0.54
8 (8⟲)
583,062
0/3
0/9
$0.03
2
23,468
Table 5: Results (using o3-mini).
Diagnose error
We instruct an agent to gather information about the error in order to diagnose its
root cause, and to formulate a plan to fix the error. Importantly, we do not reset the execution environment
– the agent is able to check intermediate files and outputs created during tool execution.
Re-implement the tool function
We perform an LLM call to re-implement the tool based on the
current implementation, the error diagnosis, and the plan to fix the error.
Summarise the attempt
Given the conversation history of the current attempt, we instruct the LLM
to summarise the attempt (i.e. the diagnosed error and steps taken to fix the error).
This concludes the current attempt. We reset the state of the execution environment to the environment
definition. We also reset the conversation history to the state before the current attempt started (i.e.
immediately after the initial implementation of the tool function). However, we append the summaries of
all past attempts including the current one to the conversation history, and also include the current version
of the code implementation. Then, we continue with the next iteration of the loop, i.e. go back to the start
of Section A.1.3.
B
Extended results
B.1
Per-task ablation results
In Tables 4 to 6, we provide detailed extended results for the ablations in a format similar to Table 2 in the
main paper.
B.2
Raw unit test results
We provide the raw unit test results for all tasks in Tables 7 and 8 for the main experiments and Tables 9
to 13 for the ablations.
B.3
Transitions between tool calls
In Fig. 5, we show the transitions between tool calls by TOOLMAKER.
26106

OpenHands (Wang et al., 2024)
Task
Invoc.
Tests
Cost
Actions
Tokens
Pathology
conch_extract_features (Lu et al., 2024b)
3/3
9/9
$0.12
4
59,911
musk_extract_features (Xiang et al., 2025)
0/3
0/6
$0.09
3
45,985
pathfinder_verify_biomarker (Liang et al., 2023)
0/2
4/6
$0.09
4
49,661
stamp_extract_features (El Nahhas et al., 2024)
✗
✗
$0.05
2
24,799
stamp_train_classification_model (El Nahhas et al., 2024)
0/3
0/9
$0.13
6
66,376
uni_extract_features (Chen et al., 2024)
0/3
0/9
$0.11
8
82,516
Radiology
medsam_inference (Ma et al., 2024)
0/3
0/6
$0.10
4
50,830
nnunet_train_model (Isensee et al., 2020)
0/2
0/4
$0.07
2
28,216
Omics
cytopus_db (Kunes et al., 2023)
0/3
0/12
$0.08
4
49,682
esm_fold_predict (Verkuil et al., 2022; Hie et al., 2022)
0/3
0/15
$0.13
5
68,098
Other
flowmap_overfit_scene (Smith et al., 2024)
✗
✗
$0.08
4
41,152
medsss_generate (Jiang et al., 2025)
3/3
6/6
$0.07
3
37,926
modernbert_predict_masked (Warner et al., 2024)
0/3
0/9
$0.61
20
542,207
retfound_feature_vector (Zhou et al., 2023)
✗
✗
$0.09
4
50,891
tabpfn_predict (Hollmann et al., 2025)
0/3
0/9
$0.10
5
56,150
Table 6: Results (using Claude 3.5 Sonnet).
run_bash_command
80
google_drive_download_ﬁle
2
END
15
read_ﬁle
16
browse
4
write_ﬁle
1
list_directory
13
google_drive_list_folder
2
1
2
2
29
11
64
3
1
34
START
15
15
3
1
1
2
1
2
1
1
60
1
19
Figure 5: Transitions between tool calls by TOOLMAKER.
26107

Passed
Category
Task
Call
Test
Pathology
conch_extract_features (Lu et al., 2024b)
kather100k_muc
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_jpg
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_png
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
musk_extract_features (Xiang et al., 2025)
kather100k_muc
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_jpg
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_png
test_shape_and_type
✓
test_status
✓
pathfinder_verify_biomarker (Liang et al., 2023)
crc_str_fraction_score
test_pvalue_crc_str
✗
test_status
✓
test_types
✓
crc_tum_fraction_score
test_pvalue_crc_tum
✗
test_status
✓
test_types
✓
stamp_extract_features (El Nahhas et al., 2024)
brca_single
test_num_processed_slides
✓
test_output_files_exist
✓
test_output_files_have_correct_shape_and_type
✓
test_status
✓
crc_single
test_num_processed_slides
✓
test_output_files_exist
✓
test_output_files_have_correct_shape_and_type
✓
test_status
✓
crc
test_num_processed_slides
✓
test_output_files_exist
✓
test_output_files_have_correct_shape_and_type
✓
test_status
✓
stamp_train_classification_model (El Nahhas et al., 2024)
crc_braf
test_num_params
✓
test_status
✓
test_trained_model_exists
✓
crc_kras
test_num_params
✓
test_status
✓
test_trained_model_exists
✓
crc_msi
test_num_params
✓
test_status
✓
test_trained_model_exists
✓
uni_extract_features (Chen et al., 2024)
kather100k_muc
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_jpg
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_png
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
Radiology
medsam_inference (Ma et al., 2024)
cucumber
test_output_file
✓
test_status
✓
other_output_file
test_output_file
✓
test_status
✓
png
test_output_file
✓
test_status
✓
nnunet_train_model (Isensee et al., 2020)
prostate
test_status
✗
test_trained_model_exists
✗
spleen
test_status
✗
test_trained_model_exists
✗
Omics
cytopus_db (Kunes et al., 2023)
B_and_CD4_T
test_output_file_contains_all_keys
✓
test_output_file_exists
✓
test_status
✓
test_types
✓
Treg_and_plasma_and_B_naive
test_output_file_contains_all_keys
✓
test_output_file_exists
✓
test_status
✓
test_types
✓
leukocytes
test_output_file_contains_all_keys
✓
test_output_file_exists
✓
test_status
✓
test_types
✓
esm_fold_predict (Verkuil et al., 2022; Hie et al., 2022)
protein2_with_mask
test_contact_map_values
✗
test_sequence_representation_values
✗
test_status
✓
test_type_contact_map
✓
test_type_sequence_representation
✓
protein2
test_contact_map_values
✓
test_sequence_representation_values
✓
test_status
✓
test_type_contact_map
✓
test_type_sequence_representation
✓
protein3
test_contact_map_values
✓
test_sequence_representation_values
✓
test_status
✓
test_type_contact_map
✓
test_type_sequence_representation
✓
Other
flowmap_overfit_scene (Smith et al., 2024)
llff_fern
test_correct_number_of_frames
✓
test_status
✓
test_types_and_shapes
✓
llff_orchids
test_correct_number_of_frames
✓
test_status
✓
test_types_and_shapes
✓
medsss_generate (Jiang et al., 2025)
motor_vehicle_accident
test_response_is_str
✓
test_status
✓
nsclc
test_response_is_str
✓
test_status
✓
pediatric_rash
test_response_is_str
✓
test_status
✓
modernbert_predict_masked (Warner et al., 2024)
future_of_ai
test_prediction_contains_original_sentence
✓
test_prediction
✓
test_status
✓
meaning_of_life
test_prediction_contains_original_sentence
✓
test_prediction
✓
test_status
✓
walking
test_prediction_contains_original_sentence
✓
test_prediction
✓
test_status
✓
retfound_feature_vector (Zhou et al., 2023)
cucumber_different_filename
test_shape_and_type
✓
test_status
✓
jpg
test_shape_and_type
✓
test_status
✓
png
test_shape_and_type
✓
test_status
✓
tabpfn_predict (Hollmann et al., 2025)
diabetes
test_number_of_probs
✓
test_status
✓
test_types
✓
heart_disease
test_number_of_probs
✓
test_status
✓
test_types
✓
parkinsons
test_number_of_probs
✓
test_status
✓
test_types
✓
Table 7: Raw results (TOOLMAKER, without paper summary in context).
26108

Passed
Category
Task
Call
Test
Pathology
conch_extract_features (Lu et al., 2024b)
kather100k_muc
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_jpg
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
tcga_brca_patch_png
test_feature_values
✓
test_shape_and_type
✓
test_status
✓
musk_extract_features (Xiang et al., 2025)
kather100k_muc
test_shape_and_type
✗
test_status
✗
tcga_brca_patch_jpg
test_shape_and_type
✗
test_status
✗
tcga_brca_patch_png
test_shape_and_type
✗
test_status
✗
pathfinder_verify_biomarker (Liang et al., 2023)
crc_str_fraction_score
test_pvalue_crc_str
✗
test_status
✓
test_types
✓
crc_tum_fraction_score
test_pvalue_crc_tum
✗
test_status
✓
test_types
✓
stamp_extract_features (El Nahhas et al., 2024)
brca_single
test_num_processed_slides
✗
test_output_files_exist
✗
test_output_files_have_correct_shape_and_type
✓
test_status
✗
crc_single
test_num_processed_slides
✗
test_output_files_exist
✗
test_output_files_have_correct_shape_and_type
✓
test_status
✗
crc
test_num_processed_slides
✗
test_output_files_exist
✗
test_output_files_have_correct_shape_and_type
✓
test_status
✗
stamp_train_classification_model (El Nahhas et al., 2024)
crc_braf
test_num_params
✗
test_status
✗
test_trained_model_exists
✗
crc_kras
test_num_params
✗
test_status
✗
test_trained_model_exists
✗
crc_msi
test_num_params
✗
test_status
✗
test_trained_model_exists
✗
uni_extract_features (Chen et al., 2024)
kather100k_muc
test_feature_values
✗
test_shape_and_type
✗
test_status
✗
tcga_brca_patch_jpg
test_feature_values
✗
test_shape_and_type
✗
test_status
✗
tcga_brca_patch_png
test_feature_values
✗
test_shape_and_type
✗
test_status
✗
Radiology
medsam_inference (Ma et al., 2024)
cucumber
test_output_file
✗
test_status
✗
other_output_file
test_output_file
✗
test_status
✗
png
test_output_file
✗
test_status
✗
nnunet_train_model (Isensee et al., 2020)
prostate
test_status
✗
test_trained_model_exists
✗
spleen
test_status
✗
test_trained_model_exists
✗
Omics
cytopus_db (Kunes et al., 2023)
B_and_CD4_T
test_output_file_contains_all_keys
✗
test_output_file_exists
✗
test_status
✗
test_types
✗
Treg_and_plasma_and_B_naive
test_output_file_contains_all_keys
✗
test_output_file_exists
✗
test_status
✗
test_types
✗
leukocytes
test_output_file_contains_all_keys
✗
test_output_file_exists
✗
test_status
✗
test_types
✗
esm_fold_predict (Verkuil et al., 2022; Hie et al., 2022)
protein2_with_mask
test_contact_map_values
✗
test_sequence_representation_values
✗
test_status
✗
test_type_contact_map
✗
test_type_sequence_representation
✗
protein2
test_contact_map_values
✗
test_sequence_representation_values
✗
test_status
✗
test_type_contact_map
✗
test_type_sequence_representation
✗
protein3
test_contact_map_values
✗
test_sequence_representation_values
✗
test_status
✗
test_type_contact_map
✗
test_type_sequence_representation
✗
Other
flowmap_overfit_scene (Smith et al., 2024)
llff_fern
test_correct_number_of_frames
✗
test_status
✗
test_types_and_shapes
✗
llff_orchids
test_correct_number_of_frames
✗
test_status
✗
test_types_and_shapes
✗
medsss_generate (Jiang et al., 2025)
motor_vehicle_accident
test_response_is_str
✓
test_status
✓
nsclc
test_response_is_str
✓
test_status
✓
pediatric_rash
test_response_is_str
✓
test_status
✓
modernbert_predict_masked (Warner et al., 2024)
future_of_ai
test_prediction_contains_original_sentence
✗
test_prediction
✗
test_status
✗
meaning_of_life
test_prediction_contains_original_sentence
✗
test_prediction
✗
test_status
✗
walking
test_prediction_contains_original_sentence
✗
test_prediction
✗
test_status
✗
retfound_feature_vector (Zhou et al., 2023)
cucumber_different_filename
test_shape_and_type
✗
test_status
✗
jpg
test_shape_and_type
✗
test_status
✗
png
test_shape_and_type
✗
test_status
✗
tabpfn_predict (Hollmann et al., 2025)
diabetes
test_number_of_probs
✓
test_status
✓
test_types
✓
heart_disease
test_number_of_probs
✓
test_status
✓
test_types
✓
parkinsons
test_number_of_probs
✓
test_status
✓
test_types
✓
Table 8: Raw results (OpenHands (Wang et al., 2024), without paper summary in context).
26109
