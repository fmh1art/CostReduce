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
We evaluate multiple state-of-the-art LMs on SWE-bench and find that they fail to solve all except
the simplest issues. Using a BM25 retriever, Claude 2 is only able to resolve 1.96% of the issues.
In addition to SWE-bench our contributions include the release of a training dataset, SWE-bench-
train, which is essential for advancing open model development in this challenging domain. This
dataset comprises a collection of 19,000 non-testing task instances derived from 37 repositories.
Utilizing SWE-bench-train, we release two fine-tuned models, SWE-Llama 7b and 13b, based on
the CodeLlama (Rozi`ere et al., 2023) model. We find that in some settings SWE-Llama 13b is
competitive with Claude 2 and is capable of processing contexts exceeding 100,000 tokens.
2
SWE-BENCH
SWE-bench is a benchmark featuring GitHub issues from popular repositories that report bugs or
request new features, and pull requests that make changes to the repository to resolve these issues.
The task is to generate a pull request that addresses a given issue and passes tests related to the issue.
2.1
BENCHMARK CONSTRUCTION
GitHub is a rich data source for software development, but repositories, issues, and pull requests can
be noisy, ad-hoc, or poorly documented or maintained. To find high-quality task instances at scale,
we use a 3-stage pipeline as follows.
Resolves an issue
Contributes tests
✓ 
✓ 
Attribute Filter
2
1
Scrape PRs
12 popular repositories
>90% Python Code
3
Installs successfully
PR passes all tests
✓
✓
Execution Filter
Figure 2: SWE-bench task instances are created from merged pull requests that resolve an issue,
contributes tests, and install successfully.
Stage I: Repo selection and data scraping. We start by collecting pull requests (PRs) from 12
popular open-source Python repositories on GitHub, producing about ∼90,000 PRs in total. We
focus on popular repositories as they tend be better maintained, have clear contributor guidelines,
and have better test coverage. Each PR has an associated codebase specified by it’s base commit.
Stage II: Attribute-based filtering. We create candidate tasks by selecting the merged PRs that (1)
resolve a GitHub issue and (2) make changes to the test files of the repository, which indicates that
the user likely contributed tests to check whether the issue has been resolved.
Stage III: Execution-based filtering. For each candidate task, we apply the PR’s test content, and
log the associated test results before and after the PR’s other content is applied. We filter out task
instances without at least one test where its status changes from a fail to pass (henceforth referred
to as fail-to-pass test). We also filter out instances that result in installation or runtime errors.
2

Published as a conference paper at ICLR 2024
Through these stages of filtering, the original 90,000 PRs are filtered down to the 2,294 task in-
stances which comprise SWE-bench. A final breakdown of these task instances across repositories
is presented in Figure 3, and Table 1 highlights the key features of SWE-bench task instances. We
highlight that the codebases are large with thousands of files, and the reference pull requests often
make changes to multiple files at once. Technical details about SWE-bench’s construction pipeline
are discussed in Appendix A. Additional dataset statistics are in Appendix A.5.
2.2
TASK FORMULATION
Model input. A model is given an issue text description and a complete codebase. The model is
then tasked to make an edit to the codebase to resolve the issue. In practice, we represent edits as
patch files, which specify which lines in the codebase to modify in order to resolve the issue.
Evaluation metrics. To evaluate a proposed solution, we apply the generated patch, using unix’s
patch program, to the codebase and then execute the unit and system tests associated with the
task instance. If the patch applies successfully and all of these tests pass we consider the proposed
solution to have successfully resolved the issue. The metric for our benchmark is the percentage of
task instances that are resolved. Additional technical details in Appendix A.4.
2.3
FEATURES OF SWE-BENCH
Traditional benchmarks in NLP typically involve only short input and output sequences and consider
somewhat “contrived” problems created specifically for the benchmark. In contrast, SWE-bench’s
realistic construction setting imbues the dataset with unique properties, which we discuss below.
Real-world software engineering tasks. Since each task instance in SWE-bench consists of a
large and complex codebase and a description of a relevant issue, solving SWE-bench requires
demonstrating sophisticated skills and knowledge possessed by experienced software engineers but
are not commonly evaluated in traditional code generation benchmarks.
Continually updatable. Our collection process can be easily applied to any Python repository on
GitHub and requires minimal human intervention. Therefore, we can extend SWE-bench with a
continual supply of new task instances and evaluate LMs on issues created after their training date,
which ensures that the solution was not included in their training corpus.
Diverse long inputs. Issue descriptions are typically long and detailed (195 words on average), and
codebases regularly contain many thousands of files. Solving SWE-bench requires identifying the
relatively small number of lines that need to be edited to solve an issue amongst a sea of context.
Robust evaluation. For each task instance, there is at least one fail-to-pass test which was used
to test the reference solution, and 40% of instances have at least two fail-to-pass tests. These tests
evaluate whether the model addressed the problem in the issue. In addition, a median of 51 additional
tests run to check whether prior functionality is properly maintained.
Cross-context code editing. Unlike prior settings that may constrain edit scope to an individ-
ual function or class (e.g., Chen et al., 2021; Cassano et al., 2022) or provide cloze-style fill-in
blanks (e.g., Lu et al., 2021; Fried et al., 2023), SWE-bench does not provide such explicit guid-
ance. Rather than merely having to produce a short code snippet, our benchmark challenges models
to generate revisions in multiple locations of a large codebase. SWE-bench’s reference solutions
average editing 1.7 files, 3.0 functions, and 32.8 lines (added or removed).
Wide scope for possible solutions. The task of repository-scale code editing can serve as a level
playing field to compare approaches ranging from retrieval and long-context models to decision-
making agents, which could reason and act in code. SWE-bench also allows creative freedom, as
models can generate novel solutions that may deviate from the reference PR.
3
SWE-LLAMA: FINE-TUNING CODELLAMA FOR SWE-BENCH
It is important to benchmark the performance of open models on SWE-bench alongside proprietary
models. At the time of writing, only the CodeLlama models (Rozi`ere et al., 2023) are able to handle
the very long contexts necessary. However, we observe that the off-the-shelf CodeLlama variants
3

Published as a conference paper at ICLR 2024
astropy (95)
django (850)
flask (11)
matplotlib (184)
pylint (57)
pytest (119)
requests (44)
scikit-learn (229)
seaborn (22)
sphinx (187)
sympy (386)
xarray (110)
Figure 3:
Distribution of SWE-bench tasks
(in parenthesis) across 12 open source GitHub
repositories that each contains the source code
for a popular, widely downloaded PyPI package.
Table 1: Average and maximum numbers char-
acterizing different attributes of a SWE-bench
task instance. Statistics are micro-averages cal-
culated without grouping by repository.
Mean
Max
Issue Text
Length (Words)
195.1
4477
Codebase
# Files (non-test)
3,010
5,890
# Lines (non-test)
438K
886K
Gold Patch
# Lines edited
32.8
5888
# Files edited
1.7
31
# Func. edited
3
36
Tests
# Fail to Pass
9.1
1633
# Total
120.8
9459
are not capable of following the detailed instructions to generate repository-wide code edits, and
typically output placeholder responses or unrelated code. To better evaluate the capabilities of these
models, we perform supervised fine-tuning on the 7 billion- and 13 billion-parameter CodeLlama-
Python models. The resulting models are specialized repository editors that can run on consumer
hardware and resolve GitHub issues.
Training data. We follow our data collection procedure and collect 19,000 issue-PR pairs from an
additional 37 popular Python package repositories. In contrast to Section 2.1, we do not require
that pull requests contribute test changes. This allows us to create a much larger training set to use
for supervised fine-tuning. To eliminate the risk of data contamination, the set of repositories in the
training data is disjoint from those included in the evaluation benchmark.
Training details. Given the instructions, an issue text from GitHub and the relevant code files as the
prompt, we finetune SWE-Llama to generate the patch that solved the given issue (the “gold patch”).
For memory efficiency, we fine-tune only the weights of the attention sublayer using LoRA Hu et al.
(2022), and exclude training sequences with more than 30,000 tokens, reducing the effective size of
the training corpus to 10,000 instances. More details are provided in Appendix B.
4
EXPERIMENTAL SETUP
In this section we explain how inputs are constructed to run SWE-bench evaluation. In addition, we
review the models that we evaluate in this work.
4.1
RETRIEVAL-BASED APPROACH
SWE-bench instances provide an issue description and a codebase as input to the model. While
issues descriptions are usually short (195 words on average as shown in Table 1), codebases consist
of many more tokens (438K lines on average) than can typically be fit into an LMs context window.
Then the question remains of exactly how to choose the relevant context to provide to the model?
To address this issue for our baselines, we simply use a generic retrieval system to select the files
to insert as context. In particular, we evaluate models under two relevant context settings: 1) sparse
retrieval and 2) an oracle retrieval.
Sparse retrieval. Dense retrieval methods are ill-suited to our setting due to very long key and
query lengths, and especially the unusual setting of retrieving code documents with natural language
queries. Therefore, we choose to use BM25 retrieval (Robertson et al., 2009) to retrieve relevant files
to provide as context for each task instance. We experiment with three different maximum context
limits, and simply retrieve as many files as fits within the specified limit. We evaluate each model
on all limits that fit within its context window and report the best performance. From observation,
models perform best on the shortest context window, as shown in Table 2.
“Oracle” retrieval. For analysis purposes we also consider a setting where we “retrieve” the files
edited by the reference patch that solved the issue on GitHub. This “oracle” setting is less realistic,
4

Published as a conference paper at ICLR 2024
since an engineer working on addressing an issue may not know a priori which files need to be
modified. In addition, this setting is also not necessarily comprehensive since edited files alone may
not include all the required context to understand exactly how software will behave when interacting
with unseen parts of the code.
We compare the BM25 retrieval results with those of the “oracle” retrieval setting, as shown in
Table 3. We observe that in approximately 40% of instances, BM25 retrieves a superset of the
oracle files for the 27,000-token context limit. However, in almost half of the instances with the
27,000-token limit, it retrieves none of the files from the “oracle” context.
4.2
INPUT FORMAT
Once the retrieved files are selected using one of the two methods above, we construct the input
to the model consisting of task instructions, the issue text, retrieved files and documentation, and
finally an example patch file and prompt for generating the patch file. Examples of instances and
further details on this formulation are provided in Appendix D.
4.3
MODELS
Due to the need to process long sequence lengths, there are only a few models that are currently
suitable for SWE-bench. Thus we evaluate ChatGPT-3.5 (gpt-3.5-turbo-16k-0613), GPT-4
(gpt-4-32k-0613), Claude 2, and SWE-Llama with their context limits shown in Table 4.
Table 2: Model resolve rates with BM25 re-
trieval, with different maximum context lengths.
Max. Content
Model
13k
27k
50k
Claude 2
1.96
1.87
1.22
SWE-Llama 7b
0.70
0.31
0.00
SWE-Llama 13b
0.70
0.48
0.00
Table 3: BM25 recall with respect to oracle files
for different maximum context lengths.
BM25 Recall
13k
27k
50k
Avg.
29.58
44.41
51.06
All
26.09
39.83
45.90
Any
34.77
51.27
58.38
Table 4: We compare the different context lengths and proportion of the “oracle” retrieval setting
covered. Models with shorter context lengths are thus inherently disadvantaged. Note that descrip-
tions of token-lengths is a relative non-standard measure (e.g. Llama-tokenized sequences are 42%
longer on average than the equivalent sequence tokenized for GPT-4).
ChatGPT-3.5
GPT-4
Claude 2
SWE-Llama
Max. Tokens
16,385
32,768
100,000
≥100,000
% of Instances
58.1%
84.1%
96.4%
≥94.8%
5
RESULTS
We report results for models using different retrieval mechanisms and prompting styles, then provide
some analysis and insight into model performance and difficulty. We summarize models’ perfor-
mance using BM25 retrieval in Table 5. Across the board, models struggle significantly to resolve
issues. The best performing model, Claude 2, is only able to resolve 1.96% of the issues.
To analyze the importance of the retriever to the overall system results, we present the “oracle”
retrieval results in Appendix Table 18. There, Claude 2 is able to resolve 4.8% of issues using the
“oracle” retriever. We further analyze the importance of context in the discussion below.
Difficulty differs across repositories. When breaking performance down by repository, all models
trend similarly across different repositories as show in Figure 4. Despite this, the issues resolved by
each model do not necessarily overlap extensively. For example, in the “oracle” setting Claude 2 and
5

Published as a conference paper at ICLR 2024
Table 5: We compare models against each other using the BM25 retriever as described in Section 4.
∗Due to budget constraints we evaluate GPT-4 on a 25% random subset of SWE-bench.
Model
% Resolved
% Apply
Claude 2
1.96
43.07
ChatGPT-3.5
0.17
26.33
GPT-4∗
0.00
14.83
SWE-Llama 7b
0.70
51.74
SWE-Llama 13b
0.70
53.62
astropy
django
matplotlib
seaborn
flask
requests
xarray
pylint
pytest
scikit-learn
sphinx
sympy
0
5
10
15
% Resolved
ChatGPT-3.5
Claude 2
SWE-Llama 13b
Figure 4: Resolution rate for three models across the 12 repositories represented in SWE-bench in
the “Oracle” retrieval setting.
SWE-Llama 13b perform comparably, with each model resolving 110 and 91 instances respectively.
Yet of these instances, Claude 2 only solves 42% of the instances solved by SWE-Llama.
This may also be related to the presence of images in issues, which can be encoded into the is-
sue markdown with embedded image links (i.e. ![image][https://...]). Some repositories
naturally feature more instances with images; for example 32% of matplotlib and 10% of seaborn
instances contain embedded images in their issue text compared to just 2% of all instances. Solving
these instances may require multi-modal LMs or some kind of external tool use to process images.
Difficulty correlates with context length. Chat models may be pre-trained on long sequences of
code but are typically asked to generate shorter coder snippets with limited context provided to
frame the question. As shown in Figure 5, we see that as total context length increases, Claude 2’s
performance drops considerably; behavior that is also observed in other models. In our evaluation
settings, models see a lot of code that may not be directly related to solving the issue at hand, and
they seem to frequently struggle with localizing problematic code needing to be updated. This result
corroborates other studies showing that models become distracted by additional context and may be
sensitive to the relative location of target sequences (Liu et al., 2023b). Even when increasing the
maximum context size for BM25 would increase recall with respect to the oracle files, performance
drops, as shown in Table 2, as models are simply ineffective at localizing problematic code.
<20k
20k-50k
50k-100k
>100k
# of Input Tokens
0
10
20
30
40
50
% of Tasks
<500
500-1k
1k-2k
>2k
# of Issue Tokens
Status
Resolved
Applied
Figure 5: We compare the performance of Claude 2 on tasks
partitioned by total input length and by only the issue length.
Table 6: We show the results for
the “Oracle”-collapsed retrieval
setting, which uses oracle files but
collapses code that isn’t directly
modified by the PR ±15 lines.
Model
“Oracle”-collapsed
Resolved
Applied
ChatGPT-3.5
1.09
40.93
Claude 2
5.93
68.18
GPT-4
3.40
48.65
Further investigating this, we provide an input ablation on the “oracle” retrieval context, “oracle”-
collapsed, where retrieved files are collapsed entirely, except for the lines actually edited by the
true pull request (with ±15 lines of buffer) shown in Table 6. In this setting, we see increases in
performance, with GPT-4 jumping from 1.3% to 3.4% and Claude 2 from 4.8% to 5.9%.
6

Published as a conference paper at ICLR 2024
Difficulty does not correlate with issue resolution date. In Table 7 we show model results in the
“oracle” retrieval setting, partitioned by date, for PRs created before or after 2023. We find that for
most models there’s little difference in performance before or after this date, with the exception of
GPT-4. We consider this result to be largely promising as it suggests that despite models having
been exposed to some version of an repository’s codebase, they are unlikely to “cheat” to address
issues simply by generating a more recent version of the repository.
Table 7: We compare performance on task instances from before and after 2023 in the “Oracle”
retrieval setting. Most models show little difference in performance. ∗Due to budget constraints,
GPT-4 is evaluated on a 25% random subset of SWE-bench tasks, which may impact performance.
Claude 2
ChatGPT-3.5
GPT-4∗
SWE-Llama 7b
SWE-Llama 13b
Before 2023
4.87
0.49
1.96
2.95
3.98
After 2023
4.23
0.77
0.0
3.46
3.85
Finetuned models are sensitive to context distribution shifts. The finetuned models SWE-Llama
7b and 13b perform surprisingly poorly with BM25 retrieved context. As these models were fine-
tuned using the “oracle” retrieval as context, we suspect this shift in context makes it difficult for
the model to perform reliably. For instance, SWE-Llama was trained to edit every file included as
context whereas in the BM25 setting many files provided in context are not expected to be changed.
Generating patches is easier than generating whole files. Models are often trained using standard
code files and likely rarely see patch files. We generally formulate our task to have models generate
patch files as opposed to recreating the entire file with their proposed change, since patch files will
usually be a much more efficient representation of a file change. As shown in Table 5, we observe
that models still struggle with generating well-formatted patch files. So we experiment with asking
models to instead regenerate entire files with their proposed changes to resolve the issue. In this
setting, we find that models generally perform worse at this task than when generating patch files;
for instance, Claude 2 scores at 2.2% compared to 4.8% in the main table for “oracle” retrieval.
Even when controlling for instance length, generating on the shorter half of the task instances by
input tokens yields 3.9% compared to 7.8% for generating patches with Claude 2.
Language models tend to generate shorter, simpler edits. Model generated patch files tend to
add and remove fewer lines than their respective gold patch. As shown in Table 8, compared to an
average gold patch, model generated patch files that apply correctly are less than half the total length
(74.5 versus 30.1 lines) of gold edit patch files, and rarely edit more than a single file.
Table 8: Average edits of model generated patches in the “oracle” retrieval setting across success-
fully applied patches. For the task instances specific to each model, we calculate the same statistics
across the gold patches. Avg Gold shows statistics macro-averaged over each models’ respective
gold patches. All Gold shows statistics for all gold patches unconditioned on model performance.
Model
Total Lines
Added
Removed
Functions
Files
Claude 2
19.6
4.2
1.9
1.1
1.0
Gold
44.1
12.0
5.8
2.1
1.2
ChatGPT-3.5
30.1
3.8
2.7
1.6
1.0
Gold
39.6
9.5
6.1
1.9
1.2
GPT-4
20.9
4.4
1.5
1.0
1.0
Gold
33.6
8.4
3.8
1.9
1.1
SWE-Llama 13b
17.6
1.6
1.2
1.2
1.1
Gold
37.8
10.0
4.4
1.9
1.1
SWE-Llama 7b
16.7
1.3
1.2
1.2
1.1
Gold
40.2
11.3
4.9
1.9
1.1
Avg Gold
39.1
10.2
5.0
1.9
1.1
All Gold
74.5
22.3
10.5
3.0
1.7
7

Published as a conference paper at ICLR 2024
5.1
A QUALITATIVE ANALYSIS OF SWE-LLAMA GENERATIONS
We select 11 generations from SWE-Llama and Claude 2 to better understand the quality of the task
and generated patches under the “oracle” retrieval setting. Here we discuss an example from SWE-
Llama and our overall findings, with in-depth analyses for other examples shown in Appendix F.
Gold Patch
     
 
(self, section: str) -> List[str]: 
-        
 self.
(_(
), self._consume_fields()) 
+        
 self._config.napoleon_use_param: 
+            
+            fields = self.
(multiple=True) 
+            
 self.
(fields) 
+        
: 
+            fields = self.
() 
+            return self.
(_(
), fields)
def
return
if
return
else
_parse_other_parameters_section
_format_fields
_consume_fields
_format_docutils_params
_consume_fields
_format_fields
'Other Parameters'
'Other Parameters'
# Allow to declare multiple parameters at once (ex: x, y: int) 
sphinx/ext/napoleon/docstring.py
Generated Patch
     
 
(self, section: str) -> List[str]: 
        
 self.
(_(
), self._consume_fields()) 
        return self._format_docutils_params(self._consume_fields())
def
return
_parse_other_parameters_section
_format_fields
-
'Other Parameters'
+
sphinx/ext/napoleon/docstring.py
Generated Patch Test Results
PASSED
PASSED
PASSED
PASSED
PASSED
FAILED
FAILED
NumpyDocstringTest (test_yield_types) 
TestNumpyDocstring (test_escape_args_and_kwargs 1) 
TestNumpyDocstring (test_escape_args_and_kwargs 2)
TestNumpyDocstring (test_escape_args_and_kwargs 3) 
TestNumpyDocstring (test_pep526_annotations) 
NumpyDocstringTest (test_parameters_with_class_reference) 
TestNumpyDocstring (test_token_type_invalid)
===== 2 failed, 45 passed, 8 warnings in 5.16s =====
Model Input
You will be provided with a partial code base and an issue
statement explaining a problem to resolve.
napoleon_use_param should also affect "other 
parameters" section Subject: napoleon_use_param 
should also affect "other parameters" section
def
self
return
def
self
if
 
(
, se...
    
 self.
(_(
 
(
, section):
    
    fields = self.
()
    
 self._config.napoleon_use_param: ...
_parse_other_parameters_section
_format_fields
_parse_parameters_section
_consume_fields
    # type: (unicode) -> List[unicode]
# type: (unicode) -> List[unicode]
'Other Para...

### Problem
Currently, napoleon always renders the Other parameters 
section as if napoleon_use_param was False, see source
Figure 6: We show an example of an formatted task instance, a model prediction, and the testing
framework logs. In the patches, red highlights are deletions. Green highlights are additions.
We’ll consider the task instance sphinx-doc sphinx-8713 from the Sphinx documenta-
tion generator, shown in Figure 6. The issue states that the napoleon extension of Sphinx is
not properly formatting the documentation keyword “Other Parameters” when the config setting
napoleon.use_param is set to True. The issue text further provides a detailed code snippet of
where the problematic source code is suspected to be, as well as some code examples for reproduc-
ing the error and additional information related to package versions. For this particular instance, the
model did not resolve the task, failing to pass some of the tests resolved by the gold solution.
In the “oracle” retrieval setting, the model input provides this issue text along with some instruc-
tions, the full contents of files edited by the gold patch, and an example of the diff format we
expect the answer to be in. The total model input consists of 1,558 lines of context or 20,882 to-
kens. When comparing the gold patch and the model’s patch, we find an obvious mistake. While
the model edits the correct function,
_parse_other_parameters_section
at line 684 in
sphinx/ext/napoleon/docstring.py , it changes the function to behave as if
napoleon.
use_param were always True instead of checking the config setting first and copying what the
_parse_parameters_section
does, like the gold patch. In the tests,
test_parameters
_with_class_reference directly compares the documentation produced using a config where
napoleon_use_param is set to False, which catches the model’s error immediately.
Comparing results across all the examples we consider, we notice a few prominent trends in behav-
ior. Models tend to write primitive Python code and do not leverage existing third-party libraries or
the rest of the codebase for their solutions. Models’ generations also reflect a “greedy” approach
of solving the problem exactly, with little regard for code style or logical constraints that might be
reflected by the codebase (i.e. using relative instead of absolute imports). In contrast, we observe
that many gold patches will make structural improvements that cover a much larger scope of the
codebase; these edits not only resolve the issue, but also anticipate and solve potential future issues.
6
RELATED WORK
Evaluation of LMs. Several recent works for evaluating LMs have either proposed a collection
of mutually distinct tasks spanning across multiple domains (Hendrycks et al., 2021; Liang et al.,
2022; Srivastava et al., 2023) or turned to the web as an interactive setting featuring tasks that require
8

Published as a conference paper at ICLR 2024
multiple steps to solve (Yao et al., 2022; Zhou et al., 2023; Deng et al., 2023; Liu et al., 2023d). There
are several drawbacks with such a “potpourri” style setup. First, each task tends to narrowly focus on
one or a few skills, resulting in challenges that are typically too simple, pigeonhole the model into a
reduced role, and do not provide models with the bandwidth to exercise their versatility or potentially
demonstrate new abilities (Srivastava et al., 2023). Consequently, a model’s performance on such
task conglomerations may not yield actionable, deep insights regarding its capabilities and how to
improve them (Schlangen, 2019; Mart´ınez-Plumed et al., 2021; Bowman & Dahl, 2021). SWE-
bench addresses these shortcomings, as our work demonstrates that it is significantly challenging,
presents a wide range of possibilities for improving LMs to solve this task, and is easy to refresh
over time with new task instances, each of which introduce novel, nuanced, and practical challenges.
Code Generation Benchmarks. HumanEval (Chen et al., 2021) is the current standard in a long-
standing pursuit of synthesizing code from natural language descriptions (Yu et al., 2018; Austin
et al., 2021; Hendrycks et al., 2021; Li et al., 2022a; Zan et al., 2023). In the past year, subsequent
benchmarks have sought to augment HumanEval with extensions to different languages (Cassano
et al., 2022; Athiwaratkun et al., 2023; Orlanski et al., 2023), variations in edit scope (Yu et al.,
2023; Du et al., 2023), similar but novel code completion tasks (Muennighoff et al., 2023), and
more testing (Liu et al., 2023a). Simultaneously, separate works have sought to introduce new cod-
ing paradigms (Yin et al., 2022; Yang et al., 2023) or design library-specific problems (Lai et al.,
2022; Zan et al., 2022). Instead of partitioning problems into siloed datasets and curtailing them
for simplicity’s sake, SWE-bench’s collection procedure transforms the source code with minimal
post-processing, preserving a much broader set of challenges grounded in real-world software en-
gineering beyond closed form completion, such as patch generation, reasoning over long contexts,
navigating a codebase directory, and capturing dependency-based relationships across modules.
ML for Software Engineering. To overcome traditional program analysis techniques that may not
scale or incorporate natural language, one direction of current software engineering research is to
use neural networks, including LMs, to automate real-world software development processes (Ma-
niatis et al., 2023; Zheng et al., 2023; Hou et al., 2023). Use cases include automating commit
generation (Jung, 2021; Liu et al., 2023c), PR review (Yang et al., 2016; Li et al., 2022b; Tufano
et al., 2021), bug localization Kim et al. (2019); Chakraborty et al. (2018), testing (Kang et al., 2023;
Xia et al., 2023; Wang et al., 2023), and program repair (Gupta et al., 2017; Allamanis et al., 2017;
Monperrus, 2018; Jiang et al., 2018; Goues et al., 2019; Gao et al., 2022; Dinh et al., 2023; Motwani
& Brun, 2023). Most relevant to SWE-bench are works that have sought to apply LMs towards au-
tomated program repair (Xia & Zhang, 2022; 2023; Fan et al., 2023; Sobania et al., 2023), guiding
code editing with commits (Chakraborty & Ray, 2021; Zhang et al., 2022; Fakhoury et al., 2023).
However, none of the existing datasets (Just et al., 2014; Karampatsis & Sutton, 2019) present code
context at the scale of SWE-bench. Moreover, SWE-bench can be easily extended to new program-
ming languages and repositories, and it provides a significantly more realistic and challenging arena
to carry out experiments towards augmenting LMs with software engineering tools and practices.
7
DISCUSSION
Limitations and future directions. SWE-bench task instances are all in Python; we hope to apply
SWE-bench’s task instance collection procedure to expand its coverage to more programming lan-
guages and domains. Second, our experiments aim to establish a baseline of the simplest and most
straight-forward approaches for this task; we do not intend to constrain future methodologies to the
same type of approach and encourage future work to investigate different methods (e.g., agent-based
approaches, tool augmented LMs). Lastly, while this work evaluates models using execution-based
code testing, relying solely on this method is insufficient to guarantee reliable performance of model
generations, as we find automated code generations from LMs can frequently be less comprehensive,
efficient, or readable compared to human-written solutions.
Conclusion. The complexity of real-world software development processes extends far beyond
just code completion. By drawing on the open-source collaborative pipeline, SWE-bench creates
a faithful mirror of real world coding environments. This more realistic environment encourages
creative solutions that can have immediate applicability in open-source software development. We
hope that this benchmark and our other contributions can serve as valuable assets in the future
development of LMs that are more practical, intelligent, and autonomous.
9

Published as a conference paper at ICLR 2024
8
ETHICS STATEMENT
SWE-bench is collected entirely from public repositories with licenses that permit software usage
that our contributions are in accordance with. Details of the licenses are included in Table 12.
During the collection or evaluation processes, we do not collect information about GitHub users,
and the SWE-bench task instances do not use GitHub data beyond what is offered via the public API
and website. Our contributions do not involve any human subject participation; we do not perform
crowdsourcing or recruit human task workers for any part of SWE-bench, including its collection
and evaluation procedures along with the experiments. SWE-bench’s filtering criteria for GitHub
repositories based on popularity does not implicitly or explicitly rely on any discriminative or biased
heuristics for repository selection. For the dataset release, we plan to open source the SWE-bench
task instances, the collection and evaluation infrastructure, the experimental results, the training
data used for fine-tuning SWE-Llama models, and the SWE-Llama model weights. Following best
practice precedents, we will also put forth ample documentation to describe each component and
its use, and we will also put in place convenient communication channels for soliciting feedback to
improve SWE-bench. SWE-bench does not put forth any immediately harmful insights. We briefly
discuss the potential impact of SWE-bench’s usage in Section E.
9
REPRODUCIBILITY STATEMENT
For our submission, we have uploaded the entirety of the source code as a zipped file that has been
properly anonymized. We have organized the codebase such that separate directories correspond to
different contributions within the main paper (i.e. dataset collection, evaluation, open source model
inference, SWE-Llama training, etc.). The source code contains inline documentation that details
purpose and usage of different parts of the codebase. In addition, we also include the full set of 2294
SWE-bench task instances that contains all the components discussed in the main paper. Beyond the
documentation in the source code, we include thorough technical details for the collection pipeline
and evaluation procedures in Section A.2 and Section A.4 that complements the original details in
Section 2 of the main paper. These sections fully cover the logic presented in the code and can
be helpful for understanding it. Moving forward, as discussed in the ethics statement, we plan to
more formally release SWE-bench to the public as an open source repository with thorough details
that describes the benchmark, outlines the code, and details its usage. A major component of SWE-
bench is the collection framework, which will be part of the open sourced code. Because of its easily
maintainable design, as discussed in the main paper, our hope and belief is that SWE-bench should
be highly reproducible.
10
ACKNOWLEDGEMENTS
We thank Danqi Chen, Tri Dao, Zexuan Zhong, Tianyu Gao, Will Merrill, Mengzhou Xia, Dan
Friedman, Adithya Bhaskar, Austin Watkins, Aatmik Gupta, and Richard Zhu for their valuable
feedback and advice. We acknowledge support from the National Science Foundation under Grant
No. 2239363 and an Oracle Collaborative Research award. Any opinions, findings, conclusions, or
recommendations expressed in this material are those of the author(s) and do not necessarily reflect
the views of the National Science Foundation.
10

Published as a conference paper at ICLR 2024
REFERENCES
Miltiadis Allamanis, Marc Brockschmidt, and Mahmoud Khademi. Learning to represent programs
with graphs. arXiv preprint arXiv:1711.00740, 2017.
Ben Athiwaratkun, Sanjay Krishna Gouda, Zijian Wang, Xiaopeng Li, and Yuchen Tian et. al. Multi-
lingual evaluation of code generation models, 2023.
Jacob Austin, Augustus Odena, Maxwell Nye, Maarten Bosma, Henryk Michalewski, David Dohan,
Ellen Jiang, Carrie Cai, Michael Terry, Quoc Le, and Charles Sutton. Program synthesis with large
language models, 2021.
Samuel R. Bowman and George E. Dahl. What will it take to fix benchmarking in natural language
understanding?, 2021.
Federico Cassano, John Gouwar, Daniel Nguyen, Sydney Nguyen, Luna Phipps-Costin, Donald
Pinckney, Ming-Ho Yee, Yangtian Zi, Carolyn Jane Anderson, Molly Q Feldman, Arjun Guha,
Michael Greenberg, and Abhinav Jangda. Multipl-e: A scalable and extensible approach to bench-
marking neural code generation, 2022.
Saikat Chakraborty and Baishakhi Ray. On multi-modal learning of editing source code, 2021.
Saikat Chakraborty, Yujian Li, Matt Irvine, Ripon Saha, and Baishakhi Ray. Entropy guided spec-
trum based bug localization using statistical language model. arXiv preprint arXiv:1802.06947,
2018.
Mark Chen, Jerry Tworek, Heewoo Jun, Qiming Yuan, Henrique Ponde de Oliveira Pinto, and
Jared Kaplan et. al. Evaluating large language models trained on code, 2021.
Tri Dao, Dan Fu, Stefano Ermon, Atri Rudra, and Christopher R´e. Flashattention: Fast and memory-
efficient exact attention with io-awareness. Advances in Neural Information Processing Systems,
35:16344–16359, 2022.
Xiang Deng, Yu Gu, Boyuan Zheng, Shijie Chen, Samuel Stevens, Boshi Wang, Huan Sun, and
Yu Su. Mind2web: Towards a generalist agent for the web, 2023.
Tuan Dinh, Jinman Zhao, Samson Tan, Renato Negrinho, Leonard Lausen, Sheng Zha, and George
Karypis. Large language models of code fail at completing code with potential bugs. arXiv
preprint arXiv:2306.03438, 2023.
Xueying Du, Mingwei Liu, Kaixin Wang, Hanlin Wang, Junwei Liu, Yixuan Chen, Jiayi Feng,
Chaofeng Sha, Xin Peng, and Yiling Lou. Classeval: A manually-crafted benchmark for evaluat-
ing llms on class-level code generation, 2023.
Sarah Fakhoury, Saikat Chakraborty, Madan Musuvathi, and Shuvendu K. Lahiri. Towards generat-
ing functionally correct code edits from natural language issue descriptions, 2023.
Zhiyu Fan, Xiang Gao, Martin Mirchev, Abhik Roychoudhury, and Shin Hwei Tan. Automated
repair of programs from large language models, 2023.
Daniel Fried, Armen Aghajanyan, Jessy Lin, Sida Wang, Eric Wallace, Freda Shi, Ruiqi Zhong,
Wen tau Yih, Luke Zettlemoyer, and Mike Lewis. Incoder: A generative model for code infilling
and synthesis, 2023.
Xiang Gao, Yannic Noller, and Abhik Roychoudhury. Program repair, 2022.
Claire Le Goues, Michael Pradel, and Abhik Roychoudhury. Automated program repair. Commu-
nications of the ACM, 62(12):56–65, 2019.
David Gros, Prem Devanbu, and Zhou Yu. Ai safety subproblems for software engineering re-
searchers, 2023.
Rahul Gupta, Soham Pal, Aditya Kanade, and Shirish Shevade. Deepfix: Fixing common c lan-
guage errors by deep learning. In Proceedings of the aaai conference on artificial intelligence,
volume 31, 2017.
11

Published as a conference paper at ICLR 2024
Maurice H. Halstead. Elements of Software Science (Operating and programming systems series).
Elsevier Science Inc., USA, 1977. ISBN 0444002057.
Dan Hendrycks, Steven Basart, Saurav Kadavath, Mantas Mazeika, Akul Arora, Ethan Guo, Collin
Burns, Samir Puranik, Horace He, Dawn Song, and Jacob Steinhardt. Measuring coding challenge
competence with apps, 2021.
Xinyi Hou, Yanjie Zhao, Yue Liu, Zhou Yang, Kailong Wang, Li Li, Xiapu Luo, David Lo, John
Grundy, and Haoyu Wang. Large language models for software engineering: A systematic litera-
ture review, 2023.
Edward J Hu, Yelong Shen, Phillip Wallis, Zeyuan Allen-Zhu, Yuanzhi Li, Shean Wang, Lu Wang,
and Weizhu Chen. LoRA: Low-rank adaptation of large language models. In International Con-
ference on Learning Representations, 2022.
URL https://openreview.net/forum?
id=nZeVKeeFYf9.
Sam Ade Jacobs, Masahiro Tanaka, Chengming Zhang, Minjia Zhang, Leon Song, Samyam Ra-
jbhandari, and Yuxiong He. Deepspeed ulysses: System optimizations for enabling training of
extreme long sequence transformer models, 2023.
Jiajun Jiang, Yingfei Xiong, Hongyu Zhang, Qing Gao, and Xiangqun Chen. Shaping program
repair space with existing patches and similar code. In Proceedings of the 27th ACM SIGSOFT
international symposium on software testing and analysis, pp. 298–309, 2018.
Tae-Hwan Jung. Commitbert: Commit message generation using pre-trained programming language
model, 2021.
Ren´e Just, Darioush Jalali, and Michael D. Ernst. Defects4J: A Database of existing faults to enable
controlled testing studies for Java programs. In ISSTA 2014, Proceedings of the 2014 International
Symposium on Software Testing and Analysis, pp. 437–440, San Jose, CA, USA, July 2014. Tool
demo.
Sungmin Kang, Juyeon Yoon, and Shin Yoo. Large language models are few-shot testers: Exploring
llm-based general bug reproduction, 2023.
Rafael-Michael Karampatsis and Charles Sutton.
How often do single-statement bugs occur?
the manysstubs4j dataset. 2020 IEEE/ACM 17th International Conference on Mining Software
Repositories (MSR), pp. 573–577, 2019. URL https://api.semanticscholar.org/
CorpusID:173188438.
Douwe Kiela, Max Bartolo, Yixin Nie, Divyansh Kaushik, Atticus Geiger, and Zhengxuan Wu et.
al. Dynabench: Rethinking benchmarking in nlp, 2021.
Yunho Kim, Seokhyeon Mun, Shin Yoo, and Moonzoo Kim. Precise learn-to-rank fault localization
using dynamic and static features of target programs. ACM Transactions on Software Engineering
and Methodology (TOSEM), 28(4):1–34, 2019.
Yuhang Lai, Chengxi Li, Yiming Wang, Tianyi Zhang, Ruiqi Zhong, Luke Zettlemoyer, Scott Wen
tau Yih, Daniel Fried, Sida Wang, and Tao Yu. Ds-1000: A natural and reliable benchmark for
data science code generation, 2022.
Yujia Li, David Choi, Junyoung Chung, Nate Kushman, Julian Schrittwieser, and R´e mi Leblond
et. al. Competition-level code generation with AlphaCode. Science, 378(6624):1092–1097, dec
2022a. doi: 10.1126/science.abq1158. URL https://doi.org/10.1126%2Fscience.
abq1158.
Zhiyu Li, Shuai Lu, Daya Guo, Nan Duan, Shailesh Jannu, Grant Jenks, Deep Majumder, Jared
Green, Alexey Svyatkovskiy, Shengyu Fu, and Neel Sundaresan. Automating code review activi-
ties by large-scale pre-training, 2022b.
Percy Liang, Rishi Bommasani, Tony Lee, Dimitris Tsipras, Dilara Soylu, and Michihiro Yasunaga
et. al. Holistic evaluation of language models, 2022.
12

Published as a conference paper at ICLR 2024
Jiawei Liu, Chunqiu Steven Xia, Yuyao Wang, and Lingming Zhang. Is your code generated by
chatgpt really correct? rigorous evaluation of large language models for code generation. arXiv
preprint arXiv:2305.01210, 2023a.
Nelson F. Liu, Kevin Lin, John Hewitt, Ashwin Paranjape, Michele Bevilacqua, Fabio Petroni,
and Percy Liang.
Lost in the middle:
How language models use long contexts, 2023b.
arXiv:2307.03172.
Shangqing Liu, Yanzhou Li, Xiaofei Xie, and Yang Liu. Commitbart: A large pre-trained model for
github commits, 2023c.
Xiao Liu, Hao Yu, Hanchen Zhang, Yifan Xu, Xuanyu Lei, and Hanyu Lai et. al. Agentbench:
Evaluating llms as agents, 2023d.
Shuai Lu, Daya Guo, Shuo Ren, Junjie Huang, Alexey Svyatkovskiy, and Ambrosio Blanco et.
al. Codexglue: A machine learning benchmark dataset for code understanding and generation.
CoRR, abs/2102.04664, 2021.
Petros Maniatis, Daniel Tarlow, and Google DeepMind.
Large sequence models for soft-
ware development activities, 2023. URL https://blog.research.google/2023/05/
large-sequence-models-for-software.html.
Fernando Mart´ınez-Plumed, Pablo Barredo, Se´an ´O h´Eigeartaigh, and Jos´e Hern´andez-Orallo. Re-
search community dynamics behind popular ai benchmarks. Nature Machine Intelligence, 3:581
– 589, 2021. URL https://api.semanticscholar.org/CorpusID:236610014.
Thomas J. McCabe. A complexity measure. IEEE Transactions on Software Engineering, SE-2(4):
308–320, 1976. doi: 10.1109/TSE.1976.233837.
Martin Monperrus. Automatic software repair. ACM Computing Surveys, 51(1):1–24, jan 2018. doi:
10.1145/3105906. URL https://doi.org/10.1145%2F3105906.
Manish Motwani and Yuriy Brun. Better automatic program repair by using bug reports and tests
together, 2023.
Niklas Muennighoff, Qian Liu, Armel Zebaze, Qinkai Zheng, Binyuan Hui, Terry Yue Zhuo,
Swayam Singh, Xiangru Tang, Leandro von Werra, and Shayne Longpre. Octopack: Instruc-
tion tuning code large language models, 2023.
Gabriel Orlanski, Kefan Xiao, Xavier Garcia, Jeffrey Hui, Joshua Howland, Jonathan Malmaud, Ja-
cob Austin, Rishabh Singh, and Michele Catasta. Measuring the impact of programming language
distribution, 2023.
Simon Ott, Adriano Barbosa-Silva, Kathrin Blagec, Janina Brauner, and Matthias Samwald. Map-
ping global dynamics of benchmark creation and saturation in artificial intelligence.
Nature
Communications, 13, 2022. URL https://api.semanticscholar.org/CorpusID:
247318891.
Stephen Robertson, Hugo Zaragoza, et al. The probabilistic relevance framework: Bm25 and be-
yond. Foundations and Trends® in Information Retrieval, 3(4):333–389, 2009.
Baptiste Rozi`ere, Jonas Gehring, Fabian Gloeckle, Sten Sootla, Itai Gat, and Xiaoqing Ellen Tan et.
al. Code llama: Open foundation models for code, 2023.
David Schlangen. Language tasks and language games: On methodology in current natural language
processing research, 2019.
Dominik Sobania, Martin Briesch, Carol Hanna, and Justyna Petke. An analysis of the automatic
bug fixing performance of chatgpt, 2023.
Aarohi Srivastava, Abhinav Rastogi, Abhishek Rao, and Abu Awal Md Shoeb et. al. Beyond the
imitation game: Quantifying and extrapolating the capabilities of language models, 2023.
Rosalia Tufano, Luca Pascarella, Michele Tufano, Denys Poshyvanyk, and Gabriele Bavota. To-
wards automating code review activities, 2021.
13

Published as a conference paper at ICLR 2024
Junjie Wang, Yuchao Huang, Chunyang Chen, Zhe Liu, Song Wang, and Qing Wang. Software
testing with large language model: Survey, landscape, and vision, 2023.
Chunqiu Steven Xia and Lingming Zhang. Less training, more repairing please: revisiting automated
program repair via zero-shot learning. In Proceedings of the 30th ACM Joint European Software
Engineering Conference and Symposium on the Foundations of Software Engineering, pp. 959–
971, 2022.
Chunqiu Steven Xia and Lingming Zhang. Conversational automated program repair, 2023.
Chunqiu Steven Xia, Matteo Paltenghi, Jia Le Tian, Michael Pradel, and Lingming Zhang. Universal
fuzzing via large language models. arXiv preprint arXiv:2308.04748, 2023.
John Yang, Akshara Prabhakar, Karthik Narasimhan, and Shunyu Yao. Intercode: Standardizing
and benchmarking interactive coding with execution feedback, 2023.
Xin Yang, Raula Gaikovina Kula, Norihiro Yoshida, and Hajimu Iida. Mining the modern code
review repositories: A dataset of people, process and product. In Proceedings of the 13th Inter-
national Conference on Mining Software Repositories, pp. 460–463, 2016.
Shunyu Yao, Howard Chen, John Yang, and Karthik Narasimhan.
Webshop: Towards scalable
real-world web interaction with grounded language agents, 2022.
Pengcheng Yin, Wen-Ding Li, Kefan Xiao, Abhishek Rao, Yeming Wen, Kensen Shi, Joshua How-
land, Paige Bailey, Michele Catasta, Henryk Michalewski, Alex Polozov, and Charles Sutton.
Natural language to code generation in interactive data science notebooks, 2022.
Hao Yu, Bo Shen, Dezhi Ran, Jiaxin Zhang, Qi Zhang, Yuchi Ma, Guangtai Liang, Ying Li, Tao
Xie, and Qianxiang Wang. Codereval: A benchmark of pragmatic code generation with generative
pre-trained models, 2023.
Tao Yu, Rui Zhang, Kai Yang, Michihiro Yasunaga, Dongxu Wang, Zifan Li, James Ma, Irene
Li, Qingning Yao, Shanelle Roman, Zilin Zhang, and Dragomir Radev. Spider: A large-scale
human-labeled dataset for complex and cross-domain semantic parsing and text-to-SQL task. In
Proceedings of the 2018 Conference on Empirical Methods in Natural Language Processing, pp.
3911–3921, Brussels, Belgium, October-November 2018. Association for Computational Lin-
guistics. doi: 10.18653/v1/D18-1425. URL https://aclanthology.org/D18-1425.
Daoguang Zan, Bei Chen, Dejian Yang, Zeqi Lin, Minsu Kim, Bei Guan, Yongji Wang, Weizhu
Chen, and Jian-Guang Lou. Cert: Continual pre-training on sketches for library-oriented code
generation, 2022.
Daoguang Zan, Bei Chen, Fengji Zhang, Dianjie Lu, Bingchao Wu, Bei Guan, Yongji Wang, and
Jian-Guang Lou. Large language models meet nl2code: A survey, 2023.
Jiyang Zhang, Sheena Panthaplackel, Pengyu Nie, Junyi Jessy Li, and Milos Gligoric. Coditt5:
Pretraining for source code and natural language editing, 2022.
Zibin Zheng, Kaiwen Ning, Jiachi Chen, Yanlin Wang, Wenqing Chen, Lianghong Guo, and We-
icheng Wang. Towards an understanding of large language models in software engineering tasks.
arXiv preprint arXiv:2308.11396, 2023.
Shuyan Zhou, Frank F. Xu, Hao Zhu, Xuhui Zhou, Robert Lo, Abishek Sridhar, Xianyi Cheng,
Yonatan Bisk, Daniel Fried, Uri Alon, and Graham Neubig. Webarena: A realistic web environ-
ment for building autonomous agents, 2023.
14

Published as a conference paper at ICLR 2024
APPENDIX
In the appendix, we provide more thorough details regarding the dataset construction process, eval-
uation pipeline, and characterization of the SWE-bench benchmark.
A
BENCHMARK DETAILS
This section complements Section 2 with a more technical and fine-grained summary of the data col-
lection, execution-based validation, and evaluation procedures, along with a fuller characterization
of the task instances.
A.1
HIGH LEVEL OVERVIEW
Pull request scraping. From a list of the top 5,000 most downloaded PyPI libraries during August
2023, we select the top 100 packages, identify each library’s corresponding open-source GitHub
repository, verify which packages have licenses allowing for free software use, and collect all PRs for
these repositories via the GitHub developer API. We elect to source problems from well-trafficked
repositories because widespread use usually suggests that the repository has extensive documenta-
tion, structured open-source development guidelines, and working, well-formatted code.
Task instance construction. We construct candidate task instances from PRs that satisfy three
conditions. First, the PR’s status must be Merged. A Merged status indicates that the PR’s associated
code changes were accepted and incorporated into its parent repository. Second, the PR resolves one
or more issues in its repository. An issue is defined according to its canonical usage in GitHub as
a digital ticket for tracking bugs, enhancements, or any general development goals for a software
project. We scan a PR’s title, body, and commit messages for linked issues (i.e. “fixes #24”). Third,
the PR must introduce one or more new tests. A new test is counted when a PR’s code changes edits
a file path containing a testing-related keyword (e.g. “test”, “testing”).
A PR that satisfies these criteria is then converted into a candidate task instance such as the example
in Figure 7. The codebase C is identified by the repository’s owner/name moniker and the pull
request’s base commit. Recovering the actual codebase from this information is straightforward.
We create mirrors of the original GitHub repositories, where each mirror is uniquely identified as
owner name. Cloning a repository’s corresponding mirror and checking out the base commit
yields C in its pre-PR state. The problem statement P is an aggregate of all related issues’ titles and
descriptions along with any subsequent comments written before the timestamp of the PR’s initial
commit to avoid leakage of solution details. A PR’s code changes are separated into a test patch
and a gold patch δ. T consists of all tests from files edited in the test patch. As shown in Figure 7,
both T and δ are stored as patch files. Further details about parsing PR and semantic data is in
Appendix A.2.
Execution-based validation. We verify the usability of a task instance via execution. For each
candidate, we first define a virtual environment to serve as an execution context, then install C
before applying any patches, and finally run T once before and once after the solution δ is applied.
A candidate is removed from consideration for the final dataset if any step in the verification process
fails. In addition, to ensure that a solution δ is non-trivial, we compare the pre-solution and post-
solution validation logs to check for whether there are one or more tests in T where the status
changes from fail to pass. Lastly, we exclude task instances with tests that invoke newly created
functions or classes first introduced in the solution δ. Since naming such constructs is typically
an arbitrary process and usually not explicitly specified in the problem statement, resolving tests
such as these may be an impossible task even for human developers. Information about execution
contexts, codebase installation, determining test statuses from logs, and more are in Appendix A.3.
Continuous Updates. SWE-bench’s collection process is easily extensible to any open source code
repositories, allowing for easy and low-maintenance extension to new programming languages and
code domains. This design also provides SWE-bench with temporal robustness; as new language
models trained on more recent source code are released over time, SWE-bench can simply be up-
dated to produce new task instances based on PRs created after any LM’s training date.
https://hugovk.github.io/top-pypi-packages/
15

Published as a conference paper at ICLR 2024
Gold Patch
sklearn/ensemble/_hist_gradient_boosting/gradient_boosting.py
1041   
 
(self): 
1042 +   
 (self.loss 
 
 and 
1043 +     self.n_trees_per_iteration_ 
 ): 
1044 +     
 
(
1045 +                      
1046 +                      
) 
1047 + 
1048     
 self.loss 
 
:
1049       
 self.n_trees_per_iteration_ 
 : 
1050         
 _LOSSES[
]()
def
if
raise
if
if
return
_get_loss
==
== 1
==
== 1
'categorical_crossentropy'
"'categorical_crossentropy' is not suitable for " 
"a binary classification problem. Please use " 
"'auto' or 'binary_crossentropy' instead."
'auto'
'binary_crossentropy'
ValueError
Test Patch
418     
 stump_clf.
( , y_isnan).
( , y_isnan) 
 
419
420 + 
 
():
421 +   # categorical_crossentropy should only be used if there
422 +   # are more than two classes present. PR #14869
423 +     [[ ], [ ]]
424 +   y  [ , ]
425 +   gbrt  
(loss
)
426 +   
 pytest.raises(
, match
):
427 +     gbrt.
( , y)
428 
429   
 
assert
def
with
fit
score
test_crossentropy_binary_problem
fit
@pytest.mark.parametrize(
, [
,
])
X
X
X
HistGradientBoostingClassifier
ValueError
X
==
'categorical_crossentropy'
"'crossentropy' not suitable"
"scoring"
'loss'
1
=
1
0
=
0
1
=
=
=
None
sklearn/ensemble/_hist_gradient_boosting/tests/test_gradient_boosting.py
Problem Statement
HGBC with categorical_crossentropy fails silently on binary classification
Ping @NicolasHug @ogrisel
gives:
And 
 works fine. 
 
should either generalize or raise an error on binary classification.
binary_crossentropy
categorical_crossentropy
import
as
from
import
from
import
 numpy 
 np
 sklearn.experimental 
 enable_hist_gradient_boosting
 sklearn.ensemble 
 
  [[ , ], [ , ], [ , ], [ , ], [ , ]]
y  [ , , , , ]
gb  
(loss
,
                                    min_samples_leaf
)
gb.
( , y)
(gb.
([[ , ]]))
(gb.
([[ , ]]))
HistGradientBoostingClassifier

X
HistGradientBoostingClassifier
X
=
1
0
1
0
1
0
0
1
1
1
=
1
1
1
0
1
=
=
=1
1
0
0
1
'categorical_crossentropy'
fit
print
predict
print
predict
[0]
[0]
Metadata
Repo
Created At
Instance ID
scikit-learn/scikit-learn
scikit-learn__scikit-learn-14869
Aug 31, 2019
Issue #s
Pull Number
[14858]
14869
1018f9f...
Base Commit
Figure 7: SWE-bench task instance example. Problem statement P is an aggregation of the issues
related to the pull request. Codebase C corresponds to a repository and base commit. The tests T
and solution D are derived from the original PR’s associated code changes. Stylized for readability.
A.2
CONSTRUCTION PROCESS
We discuss additional details regarding the conversion of a pull request object into a candidate task
instance. At a high level, the main goal of this conversion is to acquire relevant information for
putting together the codebase C, problem statement P, unit tests T, and solution δ components
introduced in Section 2. To this end, a SWE-bench task instance consists of the following fields,
presented in the following Table 9. Collectively, the fields correspond to the four task instance
modules.
Field
Description
base commit
(str) The commit ID that the original PR is applied on top of
created at
(date) Datetime object of when PR was first created (not merged)
hints text
(str) Natural language suggestions for how to solve problem
instance id
(str) A unique identifier created from repo and pull number
issue numbers
(list) List of issue numbers that the original pull request resolves
patch
(str) .patch-format styled string that is a reference solution
to the problem, extracted from the original PR’s code changes
problem statement
(str) Natural language description of desired change to codebase
pull number
(int) The pull number of the original pull request
test patch
(str) .patch-format styled string containing unseen tests
for checking if a task was solved, extracted from the original
PR’s code changes
version
(str) Release version (w.r.t. repo) during which PR was created
repo
(str) The repository the task instance originates from
FAIL TO PASS
(list) List of tests that change in status from fail to pass
PASS TO PASS
(list) List of tests that change in status from pass to pass
env install commit
(str) Base commit at which to install necessary
dependencies for running task instance.
Table 9: Description of each field of a SWE-bench task instance object. See § A.2 for details
regarding how each field is collected.
Problem Statement. The problem statement P for each task instance is readily available as the
problem statement field. The problem statement is an aggregate of all issues’ first comments
along with any comments attached to those issues that were created before the creation date of
the PR’s initial commit. We crawl for issues from PR’s title, body, and commit messages. After
16

Published as a conference paper at ICLR 2024
concatenating these components’ text data, we first remove any Markdown-style comments, then
look through the remaining text for references to issue numbers (a pound # sign followed by a
number) and check whether the word preceding the issue number reference is included in a set of
keywords suggesting that the issue was resolved by the PR (e.g. “closes”, “fixes”, “resolves”). The
found issues are recorded in the issue numbers field, then separate web requests are made to
retrieve each issue’s data. To form the problem statement, each issue’s title and body are
added together and then concatenated with the next issue’s if there are multiple. It is also during this
step that the hints text field is created and collected from the PR’s comment section, where text
from comments created before the PR’s initial commit. The intuition for this collection methodology
is that such PR comments would likely contain natural language and pseudo-code suggestions to
the original human task worker regarding how to complete the problem at hand. The experiments
presented in this work do not make use of hints text, but we believe this information may be
interesting for future investigations.
Codebase. The codebase C content is not stored in plaintext for every task instance. Rather, the
task instance contains a reference to the relevant codebase via the repo and base commit field.
Both fields are available in the original PR’s data. To make retrieval of the codebase C from these
two elements reproducible and reliable, we create mirrors of the original repository. Mirrors for
the repository constituting both the evaluation and fine tuning data are collected and open-sourced
under the SWE-bench GitHub organization. Because an original repository’s code may be subject to
changes in its commit and edit history outside of the authors’ control, we choose to create a mirror
repository to ensure that later modifications to the codebase do not potentially render a task instance
unusable due to a corruption or removal of the associated base commit. Additionally, we create
a mirror instead of cloning and storing the latest version of a repository. This is because a mirror
retains the original commit hashes, history, branches, and tags, serving as a faithful and complete
history of the technical details of the original repository. A mirror does not retain stars, watchers,
issues, or pull requests from the original repository.
We create a mirror from a repository after and within the same day when task instances were col-
lected. The mirror retains the original repository’s “owner/name” moniker, except that the “/”
character is converted to a “ ” to confirm to GitHub naming conventions. Given this infrastructure,
retrieving a task instance’s codebase is straightforward. First, the correct mirror can be cloned from
the SWE-bench organization using repo. Next, within the local copy of the mirror, checking out
the base commit will reset the repository to codebase C. To proceed to another task instance
from the same repository, git version control is used to automatically remove any modifications
associated with the current task instance before checking out the next task instance’s base commit.
Solution, Test Patches. The solution δ and tests T are derived from the file changes data, or diff,
of a PR. As mentioned in Section 2.1, the original diff along with solution δ and tests T are
represented as a .patch file, a format for efficiently specifying transformations to line-based text
files. Generally speaking, a .patch is structured as a list of blocks, where each block consists of
a header and one or more hunks that collectively correspond to changes to a single file. The header
contains metadata specifying a file path and line numbers, while the actual modifications to the
target file are encoded as multiple lines prefixed by “+” and “-” to indicate additions and removals.
To create the tests T, we first identifying every unique block within the patch, then pick out and
conglomerate blocks with file paths that contain testing-related keywords (e.g. “tests”, “testing”).
The remaining blocks are merged to form the solution δ. We validate the robustness of the script
written to parse correctly T and δ by applying both patches to the corresponding codebase C and
running the tests; we then check that the results reproduce the behavior of the base PR’s diff data.
The solution δ is saved as the patch field while the tests T are saved as the test patch field.
Remaining Fields. The created at field is a timestamp that specifies when the base PR was cre-
ated. We retain the created at field from the original data and use this field to perform temporal
analysis of model performance. The version field is a string that corresponds to the release ver-
sion, with respect to the repo, during which the PR was released. Depending on availability and the
amount of effort required for each method, we create the version field by retrieving the informa-
tion directly from the source code, building the repository locally and invoking code to display the
version to standard output, or comparing the created at field with a timeline of release versions
Documentation for creating a mirror repository using GitHub
17

Published as a conference paper at ICLR 2024
from a repository’s webpage. We create executable contexts for every version of a repository, as
discussed in greater detail in § A.3.
A.3
EXECUTION-BASED VALIDATION
After filtering through all the PRs from a repository and converting those that satisfy the aforemen-
tioned criteria into candidate task instances, the next step is to validate the usability of each task
instance via execution. This procedure is broken down into three steps. First, we create executable
contexts for each release version of a repository. Next, we check whether the solution δ and tests T
can be applied, installed, and run successfully on top of codebase C. Finally, we examine each task
instance’s execution log to verify a specific set of behaviors to ensure that the task is usable and fair
for model evaluation.
Executable Contexts. We choose to create executable contexts per release version after experi-
menting with various degrees of granularity with regards to what definition level to define virtual
environments for. Defining task instance-specific contexts is most conducive to ensuring end-to-end
installation success, but comes at the cost of laborious manual handcrafting. On the other hand, a
repository-specific context based on the latest version of a repository is typically too coarse of a
definition that is not compatible with older versions’ requirements. We find that release versions are
a good proxy for capturing the dependency requirements across a subset of task instances, striking a
manageable balance between installation success and manual effort. We manually create each exe-
cutable context by examining the codebase of the latest task instance for each version. Based on the
source code and documentation typically found in the repository’s README and CONTRIBUTING
guides, we find out the Python version, necessary dependencies, and installation command.
Validation Engine. The purpose of the validation engine is to verify candidate task instances.
Specifically, this step checks first, that the solution δ and tests T can be applied to codebase C,
and second, that the codebase can be properly installed and run within the corresponding virtual
environment. To do this, we perform validation repository-by-repository, where for each repository’s
set of task instances, we perform the following procedure:
1. Create executable contexts as conda envs. based on latest task instance per version.
2. Group task instances by version.
3. Iterate across each task instances group, where for each task instance, we perform the
following within the corresponding conda env.
(a) Remove any file changes and checkout the task instance’s base commit. This sets
the repository to codebase C.
(b) Run the installation command to instantiate codebase C.
(c) Apply the test patch T to codebase C.
(d) Run the testing script, determined from test patch T, to generate test result logs logpre.
(e) Apply the solution δ patch to the codebase C with tests T.
(f) Run the testing script from part (d) again to generate test result logs logpost.
The testing command consists of the testing framework used by the repository (e.g. pytest, tox)
with paths specified in T appended. The testing command would run any and all tests that are speci-
fied within the contents of each file path. If any of the steps (a) through (f) fails, the candidate task
instance is discarded from consideration. With moderate variation across repositories, we observe
that this step generally removes half of the candidate task instances.
Examining Validation Logs. Last but not least, we check the logs logpre and logpost created by the
validation engine for specific properties. First, to guard against arbitrary naming choices, we check
logpre for ImportError and AttributeError occurrences, which are potentially indicative
of dependency naming related errors that would trivial and near-impossible to address correctly. To
this end, we remove all task instances with such errors in their logpre from consideration. Next, we
compare the test results to check that the task instance is non-trivial, indicated by at least one or
more tests having a fail status before the solution δ is applied, then a pass status after. To check
this, we first define several repository-specific parsers to convert logpre and logpost into mappings
of test ti ∈T to a status s ∈[fail,pass]. Given these two data structures, we then check that there
18
