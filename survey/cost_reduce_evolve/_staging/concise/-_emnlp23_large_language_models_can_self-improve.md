Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing, pages 1051–1068
December 6-10, 2023 ©2023 Association for Computational Linguistics
Large Language Models Can Self-Improve
Jiaxin Huang1∗
Shixiang Shane Gu2
Le Hou2†
Yuexin Wu2
Xuezhi Wang2
Hongkun Yu2
Jiawei Han1
1University of Illinois at Urbana-Champaign
2Google
1{jiaxinh3, hanj}@illinois.edu
2{shanegu, lehou, crickwu,
xuezhiw, hongkuny}@google.com
Abstract
Large Language Models (LLMs) have achieved
excellent performances in various tasks. How-
ever, fine-tuning an LLM requires extensive su-
pervision. Human, on the other hand, may im-
prove their reasoning abilities by self-thinking
without external inputs.
In this work, we
demonstrate that an LLM is also capable of
self-improving with only unlabeled datasets.
We use a pre-trained LLM to generate “high-
confidence” rationale-augmented answers for
unlabeled questions using Chain-of-Though
(CoT) prompting and self-consistency, and fine-
tune the LLM using those self-generated so-
lutions as target outputs. We show that with-
out any ground truth label, our approach sig-
nificantly improves the general reasoning abil-
ity of PaLM 540B model (74.4%→82.1% on
GSM8K, 90.0%→94.4% on OpenBookQA,
and 63.4%→67.9% on ANLI-A3) and can
also be adapted to extreme low-resource cases
where even training questions and CoT prompts
are limited. We conduct ablation studies and
show that fine-tuning on diverse reasoning
paths is critical for self-improvement.
1
Introduction
Scaling has enabled Large Language Models
(LLMs) to achieve state-of-the-art performance on
a range of Natural Language Processing (NLP)
tasks (Wang et al., 2018, 2019; Rajpurkar et al.,
2016). More importantly, new capabilities have
emerged from LLMs as they are scaled to hun-
dreds of billions of parameters (Wei et al., 2022b):
in-context few-shot learning (Brown et al., 2020)
makes it possible for an LLM to perform well
on a task it never trained on with only a handful
of examples; Chain-of-Thought (CoT) prompting
(Wei et al., 2022c; Kojima et al., 2022) demon-
strates strong reasoning ability of LLMs across
diverse tasks with or without few-shot examples;
∗Work was done during Google internship.
†Corresponding author.
self-consistency (Wang et al., 2022c) further im-
proves the performance via self-evaluating multiple
reasoning paths.
Despite these incredible capabilities of models
trained on large text corpus (Brown et al., 2020;
Chowdhery et al., 2022), fundamentally improving
the model performances beyond few-shot baselines
still requires finetuning on an extensive amount
of high-quality supervised datasets. FLAN (Wei
et al., 2021; Chung et al., 2022) and T0 (Sanh et al.,
2022) curated tens of benchmark NLP datasets to
boost zero-shot task performances on unseen tasks;
InstructGPT (Ouyang et al., 2022) crowd-sourced
many human answers for diverse sets of text in-
structions to better align their model to human
instructions; Minerva (Lewkowycz et al., 2022)
parsed the full ArXiv database carefully for rele-
vant articles to excel on challenging competitive
math and science datasets. The need for large anno-
tated data for supervised LLM training still remains
a burden for low-resource applications or specific
domains where only limited annotations are avail-
able.
In this paper, we study how an LLM capa-
ble of in-context few-shot learning and chain-of-
thought reasoning, is able to self-improve its rea-
soning ability without supervised data. We show
that using only input sequences (without ground
truth output sequences) from multiple NLP task
datasets, a pre-trained LLM is able to improve per-
formances for both in-domain and out-of-domain
tasks.
Our method is shown in Figure 1: we
first sample multiple predictions using few-shot
Chain-of-Thought (CoT) (Wei et al., 2022c) as
prompts, filter “high-confidence” predictions us-
ing majority voting (Wang et al., 2022c), and fi-
nally finetune the LLM on these high-confidence
predictions. The resulting model shows improved
reasoning in both greedy and multi-path evalu-
ations.
We call the model fine-tuned in this
way as Language Model Self-Improved (LMSI).
1051

Note that LMSI depends on in-context few-shot
learning and chain-of-thought reasoning abilities
which small language models do not necessar-
ily have. We empirically verify LMSI using a
pre-trained 540B PaLM model (Chowdhery et al.,
2022), where our method not only significantly im-
proves training task performances (7