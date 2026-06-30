# 1. annotate step type改为用LLM标注，就像前面找dependent steps一样，用LLM query的方式标注
# 2. V3的主要区别在于：v2进化得到的scripts不会在下游code agent上验证。例如，我用了deep-swe的16个case进行evolve后，得到evolve_scripts后，v3会参考scripts/run_deep_swe.sh的脚本，将evolve_scripts安装到code agent，并在这16个case上再做测试，根据得到的trajectory分析目前的evolve_scripts存在什么问题，再做修改。流程如下：

- 迭代V3。生成tools后，进行验证，跑一轮实际的trajectory，标注，得到DAG。目前有：原始trajectory$T_0$，原始最小trajectory $T^{*}_0$，当前evolve scripts $S_0$，新的trajectory $T_1$，新的最小trajectory $T^{*}_1$
  - 添加一个LLM evaluator，输入trajectory，LLM judge是否完成了这个task。如果 Evaluate($T_1$)=Success，进入evolve
    - 计算 $T_0$vs $T_1$的指标，例如实际的API cost，step数，最大的observation token，observation token avg step。保存中间结果。
    - 根据$T_1$和$T^{*}_1$，重新走V2的更新流程，更新scripts
    - 当 $|Cost(T_1)-Cost(T^{*}_1)|\leq t$或$|Cost(T_0)-Cost(T_1)|\leq t$或evolve步数超过5轮时
  - Evaluate($T_0$)=Success 且 Evaluate($T_1$)=Fail，判断错误原因，列出scripts修改计划，将错误原因和修改计划给到下游code agent，去修改scripts


# 修改1

我已经在所有benchmark上跑了上述实验，得到文件夹如下：

.evolve_scripts_v3_deep-swe_0630-001820
.evolve_scripts_v3_swe-atlas-qa_0630-001820
.evolve_scripts_v3_swe-atlas-rf_0630-001820
.evolve_scripts_v3_swe-atlas-tw_0630-001820

results/deep-swe
results/swe-atlas-qa
results/swe-atlas-rf
results/swe-atlas-tw
results/v3_cycle

但是，我发现目前的框架很慢，可以按照下面要求对其优化：

- 不迭代进行验证。按照当前V3框架，先对 T_0 进行标注。然后根据现在的evolve框架进行进化。
- 将进化得到的scripts安装到code agent，在之前evolve的cases上跑一遍，得到 T_1。计算 $T_0$vs $T_1$的指标，例如实际的API cost，step数，最大的observation token，observation token avg step。prompt 大模型问当前是否达到了降成本的预期，如果没有，让他修改instruction.md和scrips


- 当使用evolve scripts $S_0$ 安装到code agent，去得到新的trajectory的时候（使用了进化得到的scripts），我们不需要对他进行标注。因为后续使用这个标注的部分仅仅包括：（1）根据$T_1$和$T^{*}_1$，重新走V2的更新流程，更新scripts。但其实计算 $T^{*}_1$ 并对比 $T_1$ 其实不是非常有必要。我们只需要对比 trajectory$T_0$ 和 trajectory$T_1$ 就足够了。但是，直接将 T_0 和 T_1序列化到prompt中太长了，可以让LLM将两个trajectory进行对齐。

