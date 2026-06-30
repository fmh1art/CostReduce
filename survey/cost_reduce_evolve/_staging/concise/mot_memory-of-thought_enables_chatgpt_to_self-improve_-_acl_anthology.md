Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing, pages 6354–6374
December 6-10, 2023 ©2023 Association for Computational Linguistics
MoT: Memory-of-Thought Enables ChatGPT to Self-Improve
Xiaonan Li, Xipeng Qiu
School of Computer Science, Fudan University
Shanghai Key Laboratory of Intelligent Information Processing, Fudan University
{lixn20, xpqiu}@fudan.edu.cn
Abstract
Large Language Models (LLMs) have shown
impressive abilities in various tasks.
How-
ever, fundamentally improving them depends
on high-quality datasets or computationally ex-
pensive fine-tuning. On the contrary, humans
can easily improve themselves by self-thinking
and memory, without external resources. In
this paper, we propose a framework, MoT, to
let the LLM self-improve through Memory-of-
Thought, without annotated datasets and pa-
rameter updates. Specifically, MoT is divided
into two stages: 1. before the test stage, the
LLM pre-thinks on the unlabeled dataset and
saves the high-confidence thoughts as external
memory; 2. During the test stage, given a test
question, the LLM recalls relevant memory to
help itself reason and answer it. Experimen-
tal results show that MoT can help ChatGPT
significantly improve its abilities in arithmetic
reasoning, commonsense reasoning, factual rea-
soning, and natural language inference. Further
analyses show that each component contributes
critically to the improvements and MoT can
lead to consistent improvements across various
CoT methods and LLMs.
1
Introduction
Large Language Models (LLMs) have demon-
strated surprising abilities on a wide range of Natu-
ral Language Processing (NLP) tasks (Chen et al.,
2023; Zhang et al., 2022a; Chowdhery et al., 2022;
Tay et al., 2022; OpenAI, 2023; Hoffmann et al.,
2022; Touvron et al., 2023; Mialon et al., 2023;
Zhao et al., 2023; Qiu et al., 2020). Notably, new
abilities emerge in LLMs as they are scaled to
hundreds of billions of parameters, like in-context
few-shot learning (Chen et al., 2023; Dong et al.,
2022), simple digit operation and factual knowl-
edge query (Wei et al., 2022b). Especially, the gen-
eral reasoning ability of the LLM has impressed
the NLP community and relevant techniques have
achieved a series of new state-of-the-art (Wei et al.,
2022c; Kojima et al., 2022; Lampinen et al., 2022;
LLM
labeled 
Dataset
Fine-tune
LLM
(fine-tuned)
xtest
inference
(a) LLM Fine-tuning
LLM
Unlabeled 
Dataset
Memory
Pre-think
Recall
xtest
inference
(b) Pre-thinking and Recalling
Figure 1: The comparison between fine-tuning and MoT:
while fine-tuning LLM with labeled datasets is costly
and needs powerful computational resources, MoT can
make the LLM self-improve via pre-thinking and recall-
ing, without parameter updates and annotated datasets.
Wang et al., 2022b; Huang and Chang, 2022).
Specifically, Wei et al. (2022c) and Kojima et al.
(2022) propose few-shot CoT and zero-shot CoT,
which elicit LLM’s reasoning by few-shot demon-
strations and simple yet effective “Let’s think step
by step” prompting, respectively. Based on them,
Wang et al. (2022b); Press et al. (2022); Zhou et al.
(2022); Wang et al. (2023); Weng et al. (2022)
further propose self-consistency, self-ask, least-to-
most, plan-and-solve, etc., to achieve more compli-
cated reasoning in various specialized scenarios.
Despite the impressive abilities of the LLM pre-
trained on the large corpus, fundamentally improv-
ing the LLM’s performance beyond few-shot /
zero-shot baselines highly depends on either high-
quality annotated datasets or costly fine-tuning of
LLMs. In general, these methods can be divided
into three categories: 1. Annotated Datasets +
Fine-tuning: Wei et al. (2022a) and Sanh et al.
(2022) propose FLAN and T0 respectively to en-
hance the LLM’s zero-shot ability by tens of cu-
rated NLP benchmark datasets. Based on FLAN,
Chung et al. (2022) scale up its training in terms
of model size and the number of tasks, and demon-
strate that the added CoT examples with rationales
improve the LLM’s reasoning abilities. Instruct-
GPT (Ouyang et al., 2022) improves the GPT-
3’s instruction-following ability by fine-tuning on
many diverse crowd-sourced instruction-answer
6354

pairs. 2. Retrieving Annotated Data: Liu et al.
(2022), Su et al. (2022a) and Agrawal et al. (2022)
use SentenceBERT (Reimers and Gurevych, 2019)
or BM25 (Robertson and Zaragoza, 2009) to re-
trieve relevant examples from the annotated dataset,
to improve LLM’s in-context learning. Rubin 