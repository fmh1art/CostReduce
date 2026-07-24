复现下面的baselines，工作目录是./baselines。
如果遇到网络问题，可以使用proxy：http://sys-proxy-rd-relay.byted.org:8118
整理的时候，请按照文件夹整理这些baselines
如果遇到不确定的问题，需要询问我
需要在harbor框架下测试 deepswe，swe-bench，dab三个benchmark。不要改动出了当前工作目录（./baselines）以外的任何文件，如果必须要修改，也需要询问我。
对于每个baseline，创建对应的conda环境，这样不会破坏本项目的执行环境。
配置好baseline后，使用_config/doubao_seed2_lite.yaml的llm config每个benchmark跑1个case试试，确保复现没问题。

# Reducing Cost of LLM Agents with Trajectory Reduction

code and other files: tmp/artifact.zip

# ZipAct: Zipping Interaction History into a Compact State  for Efficient LLM Agents

code and other files: https: //github.com/Thomas-mci-21/ZipAct_TMLR

# EET: Experience-Driven Early Termination for Cost-Efficient  Software Engineering Agents

code: https://github.com/IanWalls/EET

# (这个先不复现) SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents

code: https://github.com/Ayanami1314/swe-pruner