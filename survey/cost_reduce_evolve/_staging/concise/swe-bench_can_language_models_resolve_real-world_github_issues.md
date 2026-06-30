Published as a conference paper at ICLR 2024
SWE-BENCH:
CAN LANGUAGE MODELS RESOLVE
REAL-WORLD GITHUB ISSUES?
Carlos E. Jimenez* 1,2
John Yang* 1,2
Alexander Wettig1,2
Shunyu Yao1,2
Kexin Pei3
Ofir Press1,2
Karthik Narasimhan1,2
1Princeton University
2Princeton Language and Intelligence
3University of Chicago
ABSTRACT
Language models have outpaced our ability to evaluate them effectively, but for
their future development it is essential to study the frontier of their capabilities.
We find real-world software engineering to be a rich, sustainable, and challenging
testbed for evaluating the next generation of language models. To this end, we in-
troduce SWE-bench, an evaluation framework consisting of 2,294 software engi-
neering problems drawn from real GitHub issues and corresponding pull requests
across 12 popular Python repositories. Given a codebase along with a description
of an issue to be resolved, a language model is tasked with editing the codebase
to address the issue. Resolving issues in SWE-bench frequently requires under-
standing and coordinating changes across multiple functions, classes, and even
files simultaneously, calling for models to interact with execution environments,
process extremely long contexts and perform complex reasoning that goes far be-
yond traditional code generation tasks. Our evaluations show that both state-of-
the-art proprietary models and our fine-tuned model SWE-Llama can resolve only
the simplest issues. The best-performing model, Claude 2, is able to solve a mere
1.96% of the issues. Advances on SWE-bench represent steps towards LMs that
are more practical, intelligent, and autonomous.
1
INTRODUCTION
Language models (LMs) are rapidly being deployed in commercial products such as chatbots and
coding assistants. At the same time, existing benchmarks have become saturated (Kiela et al., 2021;
Ott et al., 2022) and fail to capture the frontier of what state-of-the-art LMs can and cannot do. There
is a need for challenging benchmarks that more accurately reflect real-world applications of LMs to
help shape their future development and usage (Srivastava et al., 2023).
euclidean_diff
matrix_transform
dstack_struct_col
vstack_struct_col
join_struct_col
Pre PR
Post PR
Tests
Unit Tests
data leak in GBDT due to warm
start (This is about the non-
histogram-based version of...
Issue
Codebase
sklearn/
examples/
setup.cfg
setup.py
README.rst
reqs.txt
 Language Model
Generated PR
sklearn
gradient_boosting.py
utils
helper.py
+20 -12
Figure 1: SWE-bench sources task instances from real-world Python repositories by connecting
GitHub issues to merged pull request solutions that resolve related tests. Provided with the issue
text and a codebase snapshot, models generate a patch that is evaluated against real tests.
Building a good benchmark is difficult since tasks must be challenging enough to stump existing
models, but model predictions must also be easy to verify (Mart´ınez-Plumed et al., 2021). Coding
∗Equal contribution. Correspondence to {carlosej,jy1682}@princeton.edu.
Data, code, and leaderboard at swebench.com
1

Published as a conference paper at ICLR 2024
tasks are appealing as they pose challenging problems to LMs yet generated solutions can be easily
verified by running unit tests. However, existing coding benchmarks, such as HumanEval (Chen
et al., 2021), mostly involve self-contained problems that can be solved in a few lines of code.
In the real world, software engineering is not as simple. Fixing a bug might involve navigating a
large repository, understanding the interplay between functions in different files, or spotting a small
error in convoluted code. Inspired by this, we introduce SWE-bench, a benchmark that evaluates
LMs in a realistic software engineering setting. As shown in Figure 1, models are tasked to resolve
issues (typically a bug report or a feature request) submitted to popular GitHub repositories. Each
task requires generating a patch describing changes to apply to the existing codebase. The revised
codebase is then evaluated using the repository’s testing framework.
SWE-bench offers several advantages over existing LM programming benchmarks. These include, a
realistic setting that utilizes user-submitted issues and solutions, diverse inputs featuring unique code
problems from 12 repositories, a robust framework for execution-based evaluation, and the ability
to continuously update the benchmark with new instances, requiring minimal human intervention.
We eva