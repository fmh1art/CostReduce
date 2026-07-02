# 1. annotate step type改为用LLM标注，就像前面找dependent steps一样，用LLM query的方式标注
# 2. V3的主要区别在于：v2进化得到的scripts不会在下游code agent上验证。例如，我用了deep-swe的16个case进行evolve后，得到evolve_scripts后，v3会参考scripts/run_deep_swe.sh的脚本，将evolve_scripts安装到code agent，并在这16个case上再做测试，根据得到的trajectory分析目前的evolve_scripts存在什么问题，再做修改。流程如下：

- 迭代V3。生成tools后，进行验证，跑一轮实际的trajectory，标注，得到DAG。目前有：原始trajectory$T_0$，原始最小trajectory $T^{*}_0$，当前evolve scripts $S_0$，新的trajectory $T_1$，新的最小trajectory $T^{*}_1$
  - 添加一个LLM evaluator，输入trajectory，LLM judge是否完成了这个task。如果 Evaluate($T_1$)=Success，进入evolve
    - 计算 $T_0$vs $T_1$的指标，例如实际的API cost，step数，最大的observation token，observation token avg step。保存中间结果。
    - 根据$T_1$和$T^{*}_1$，重新走V2的更新流程，更新scripts
    - 当 $|Cost(T_1)-Cost(T^{*}_1)|\leq t$或$|Cost(T_0)-Cost(T_1)|\leq t$或evolve步数超过5轮时
  - Evaluate($T_0$)=Success 且 Evaluate($T_1$)=Fail，判断错误原因，列出scripts修改计划，将错误原因和修改计划给到下游code agent，去修改scripts


# 已迭代修改

我发现目前的框架很慢，可以按照下面要求对其优化：

- 不迭代进行验证。按照当前V3框架，先对 T_0 进行标注。
- If Evaluate($T_0$)=Fail，判断错误原因，列出scripts和instruction.md的修改计划，记录错误原因和修改计划
- 基于contrastive samples和上述错误原因和修改计划，根据现在的evolve框架进行进化。
- 将进化得到的scripts安装到code agent，在之前evolve的cases上跑一遍，得到 T_1。
- If Evaluate($T_1$)=Fail，判断错误原因，列出scripts和instruction.md的修改计划，记录错误原因和修改计划
- 计算 $T_0$vs $T_1$的指标，例如实际的API cost，step数，最大的observation token，observation token avg step。基于上述信息和之前记录的错误原因和修改计划prompt 大模型问当前是否达到了降成本的预期，如果没有，让他修改instruction.md（通用的指令）和scrips

# 修改

我觉得现在这种方案还不如v2的框架。请将当前v3_cycle方案保留，实现一个v3_chunk的方案，和v2_chunk的区别就在于annotate step type改为用LLM标注