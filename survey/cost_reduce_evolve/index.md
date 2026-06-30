# Survey: Cost Reduction for Agents via Evolving Tools/Skills

Curated paper set for the project: **reduce Agent cost (e.g. Code Agent) by evolving tools/skills, while maintaining performance.**

- Papers with local PDF: **106** (core=40, method=38, goal=28)
- Rejected (off-topic / pure training infra): 57
- Relevant but no local PDF (abstract only): 18
- Total judged: 163

## Tier meaning

- **core** — evolves tools / skills / prompts / workflows / memory *for agents* (the project's METHOD). ★ = closest match to the project.

- **method** — broader self-evolving-agent / experience-memory / skill-library / tool-use background.

- **goal** — reduces agent / LLM cost (routing, cascade, prompt/context compression, token budget, efficient serving).

- PDFs are in `pdfs/`; extracted text in `_staging/md/`.


## CORE

| Year | Title | Note | PDF | Source |
|---|---|---|---|---|
| 2026 | EvoRoute: Experience-Driven Self-Routing LLM Agent Systems ★ | EvoRoute: experience-driven self-routing for agent cost/latency (Agent System Trilemma). *** closest match to project *** | 2026_EvoRoute_Experience-Driven_Self-Routing_LLM_Agent_Systems.pdf | 2601.02695 |
| 2026 | ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory | ReasoningBank: distills reusable reasoning strategies from experience; memory-aware test-time scaling. | 2026_ReasoningBank_Scaling_Agent_Self-Evolving_with_Reasoning_Mem.pdf | 2509.25140 |
| 2023 | Voyager: An Open-Ended Embodied Agent with Large Language Models | Voyager: open-ended embodied agent with ever-growing skill library + auto curriculum (foundational). | 2023_Voyager_An_Open-Ended_Embodied_Agent_with_Large_Language_Mod.pdf | 2305.16291 |
| 2026 | AccelOpt: A Self-Improving LLM Agentic System for AI Accelerator Kernel Optimization | AccelOpt: self-improving agent with optimization memory; explicitly cost-effective via open models. | 2026_AccelOpt_A_Self-Improving_LLM_Agentic_System_for_AI_Accelera.pdf | 2511.15915 |
| 2025 | Training-Free Group Relative Policy Optimization | Training-Free GRPO: experience as token prior — cost-effective agent enhancement, no param updates. | 2025_Training-Free_Group_Relative_Policy_Optimization.pdf | 2510.08191 |
| 2026 | Evolving Medical Imaging Agents via Experience-driven Self-skill Discovery | MACRO: evolving agents via experience-driven self-skill & tool discovery (method transferable). | 2026_Evolving_Medical_Imaging_Agents_via_Experience-driven_Self-s.pdf | 2603.05860 |
| 2025 | Toward Effective Tool-Integrated Reasoning via Self-Evolved Preference Learning | Tool-Light: self-evolved tool-integrated reasoning; tackles excessive tool calls/overthinking. | 2025_Toward_Effective_Tool-Integrated_Reasoning_via_Self-Evolved.pdf | 2509.23285 |
| 2022 | Large Language Models Are Human-Level Prompt Engineers ★ | APE: Large Language Models Are Human-Level Prompt Engineers (prompt optimization landmark). | 2022_Large_Language_Models_Are_Human-Level_Prompt_Engineers.pdf | 2211.01910 |
| 2025 | AgentEvolver: Towards Efficient Self-Evolving Agent System | AgentEvolver: efficient self-evolving agent system; targets costly/inefficient agent dev. | 2025_AgentEvolver_Towards_Efficient_Self-Evolving_Agent_System.pdf | 2511.10395 |
| 2025 | AFlow: Automating Agentic Workflow Generation | AFlow: automated agentic workflow generation/optimization; trades cost for performance. | 2025_AFlow_Automating_Agentic_Workflow_Generation.pdf | 2410.10762 |
| 2025 | MemEvolve: Meta-Evolution of Agent Memory Systems | MemEvolve: meta-evolution of agent memory systems (jointly evolves memory arch). | 2025_MemEvolve_Meta-Evolution_of_Agent_Memory_Systems.pdf | 2512.18746 |
| 2026 | Meta Context Engineering via Agentic Skill Evolution | MCE: meta context engineering via agentic skill evolution (co-evolving skills). | 2026_Meta_Context_Engineering_via_Agentic_Skill_Evolution.pdf | 2601.21557 |
| 2025 | SkillWeaver: Web Agents can Self-Improve by Discovering and Honing Skills | SkillWeaver: web agents self-improve by discovering & honing skills (as APIs). | 2025_SkillWeaver_Web_Agents_can_Self-Improve_by_Discovering_and_H.pdf | 2504.07079 |
| 2025 | Inducing Programmatic Skills for Agentic Tasks | ASI Inducing Programmatic Skills: program-based skills; reduces 10-15% steps. | 2025_Inducing_Programmatic_Skills_for_Agentic_Tasks.pdf | 2504.06821 |
| 2025 | ReVeal: Self-Evolving Code Agents via Reliable Self-Verification | ReVeal: self-evolving code agents via reliable self-verification + tool eval. | 2025_ReVeal_Self-Evolving_Code_Agents_via_Reliable_Self-Verificat.pdf | 2506.11442 |
| 2026 | Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models | ACE: contexts as evolving playbooks (accumulate/refine/organize strategies). | 2026_Agentic_Context_Engineering_Evolving_Contexts_for_Self-Impro.pdf | 2510.04618 |
| 2025 | Automated Design of Agentic Systems | ADAS: automated design of agentic systems (meta agent codes better agents). | 2025_Automated_Design_of_Agentic_Systems.pdf | 2408.08435 |
| 2026 | Beyond Static Tools: Test-Time Tool Evolution for Scientific Reasoning | Beyond Static Tools (TTE): test-time tool synthesis/verification/evolution. | 2026_Beyond_Static_Tools_Test-Time_Tool_Evolution_for_Scientific.pdf | 2601.07641 |
| 2026 | Group-Evolving Agents: Open-Ended Self-Improvement via Experience Sharing | Group-Evolving Agents: open-ended self-improvement via experience sharing. | 2026_Group-Evolving_Agents_Open-Ended_Self-Improvement_via_Experi.pdf | 2602.04837 |
| 2023 | ExpeL: LLM Agents Are Experiential Learners | ExpeL: LLM agents are experiential learners (extracts reusable insights). | 2023_ExpeL_LLM_Agents_Are_Experiential_Learners.pdf | 2308.10144 |
| 2026 | GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning | GEPA: reflective prompt evolution; outperforms RL on prompt optimization. | 2026_GEPA_Reflective_Prompt_Evolution_Can_Outperform_Reinforcemen.pdf | 2507.19457 |
| 2026 | AutoSkill: Experience-Driven Lifelong Learning via Skill Self-Evolution | AutoSkill: experience-driven lifelong learning via skill self-evolution. | 2026_AutoSkill_Experience-Driven_Lifelong_Learning_via_Skill_Self.pdf | 2603.01145 |
| 2026 | MemSkill: Learning and Evolving Memory Skills for Self-Evolving Agents | MemSkill: learning & evolving memory skills (learnable memory routines). | 2026_MemSkill_Learning_and_Evolving_Memory_Skills_for_Self-Evolvi.pdf | 2602.02474 |
| 2026 | Reinforcement Learning for Self-Improving Agent with Skill Library | SAGE: RL self-improving agent with skill library (Skill-Augmented GRPO). | 2026_Reinforcement_Learning_for_Self-Improving_Agent_with_Skill_L.pdf | 2512.17102 |
|  | Gödel Agent: A Self-Referential Agent Framework for Recursively Self-Improvement - ACL Anthology | Gödel Agent: self-referential framework for recursive self-improvement. | _Gödel_Agent_A_Self-Referential_Agent_Framework_for_Recursive.pdf | https://aclanthology.org/2025.acl-long.1 |
| 2026 | Self-Consolidation for Self-Evolving Agents | Self-Consolidation: consolidates experience, cuts retrieval time/noise. | 2026_Self-Consolidation_for_Self-Evolving_Agents.pdf | 2602.01966 |
| 2025 | Dynamic Cheatsheet: Test-Time Learning with Adaptive Memory | Dynamic Cheatsheet: test-time learning with evolving adaptive memory. | 2025_Dynamic_Cheatsheet_Test-Time_Learning_with_Adaptive_Memory.pdf | 2504.07952 |
|  | LLM Agents Making Agent Tools - ACL Anthology | TOOLMAKER: agents autonomously turn papers-with-code into LLM tools. | _LLM_Agents_Making_Agent_Tools_-_ACL_Anthology.pdf | https://aclanthology.org/2025.acl-long.1 |
| 2026 | SkillNet: Create, Evaluate, and Connect AI Skills | SkillNet: create, evaluate, and connect AI skills (skill ecosystem). | 2026_SkillNet_Create_Evaluate_and_Connect_AI_Skills.pdf | 2603.04448 |
| 2025 | Live-SWE-agent: Can Software Engineering Agents Self-Evolve on the Fly? | Live-SWE-agent: software-engineering agents self-evolve on the fly. | 2025_Live-SWE-agent_Can_Software_Engineering_Agents_Self-Evolve_o.pdf | 2511.13646 |
| 2026 | Remember Me, Refine Me: A Dynamic Procedural Memory Framework for Experience-Driven Agent Evolution | ReMe: dynamic procedural memory; reduces redundant trial-and-error. | 2026_Remember_Me_Refine_Me_A_Dynamic_Procedural_Memory_Framework.pdf | 2512.10696 |
| 2026 | EvolveR: Self-Evolving LLM Agents through an Experience-Driven Lifecycle | EvolveR: self-evolving agents through experience-driven lifecycle. | 2026_EvolveR_Self-Evolving_LLM_Agents_through_an_Experience-Drive.pdf | 2510.16079 |
| 2026 | SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning | SkillRL: recursive skill-augmented RL with hierarchical SKILLBANK. | 2026_SkillRL_Evolving_Agents_via_Recursive_Skill-Augmented_Reinfo.pdf | 2602.08234 |
| 2023 | DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines | DSPy: compile declarative LM calls into self-improving pipelines. | 2023_DSPy_Compiling_Declarative_Language_Model_Calls_into_Self-Im.pdf | 2310.03714 |
| 2026 | Multi-Agent Design: Optimizing Agents with Better Prompts and Topologies | Multi-Agent Design: optimizing agent prompts + topologies (Mass). | 2026_Multi-Agent_Design_Optimizing_Agents_with_Better_Prompts_and.pdf | 2502.02533 |
| 2024 | Large Language Models as Tool Makers | LATM: LLMs as Tool Makers (closed-loop tool creation/caching). | 2024_Large_Language_Models_as_Tool_Makers.pdf | 2305.17126 |
| 2023 | Large Language Models as Optimizers | OPRO: LLMs as optimizers; prompt optimization (foundational). | 2023_Large_Language_Models_as_Optimizers.pdf | 2309.03409 |
| 2026 | Dr. Zero: Self-Evolving Search Agents without Training Data | Dr. Zero: self-evolving search agents without training data. | 2026_Dr._Zero_Self-Evolving_Search_Agents_without_Training_Data.pdf | 2601.07055 |
| 2024 | AutoFlow: Automated Workflow Generation for Large Language Model Agents | AutoFlow: automated workflow generation for LLM agents. | 2024_AutoFlow_Automated_Workflow_Generation_for_Large_Language_Mo.pdf | 2407.12821 |
| 2024 | CRAFT: Customizing LLMs by Creating and Retrieving from Specialized Toolsets | CRAFT: create + retrieve specialized toolsets for LLMs. | 2024_CRAFT_Customizing_LLMs_by_Creating_and_Retrieving_from_Speci.pdf | 2309.17428 |

## METHOD

| Year | Title | Note | PDF | Source |
|---|---|---|---|---|
|  | Enabling Self-Improving Agents to Learn at Test Time With Human-In-The-Loop Guidance - ACL Anthology | ARIA: self-improving agents learn at test time w/ human-in-loop; evolving knowledge repo. | _Enabling_Self-Improving_Agents_to_Learn_at_Test_Time_With_Hu.pdf | https://aclanthology.org/2025.emnlp-indu |
| 2026 | Your Agent May Misevolve: Emergent Risks in Self-evolving LLM Agents | Your Agent May Misevolve: risks across model/memory/tool/workflow evolution pathways. | 2026_Your_Agent_May_Misevolve_Emergent_Risks_in_Self-evolving_LLM.pdf | 2509.26354 |
| 2026 | Agent-World: Scaling Real-World Environment Synthesis for Evolving General Agent Intelligence | Agent-World: self-evolving training arena; MCP/tool environments; lifelong learning. | 2026_Agent-World_Scaling_Real-World_Environment_Synthesis_for_Evo.pdf | 2604.18292 |
| 2026 | Feedback-Driven Tool-Use Improvements in Large Language Models via Automated Build Environments | Feedback-driven tool-use improvements via automated build envs (RL for tool use). | 2026_Feedback-Driven_Tool-Use_Improvements_in_Large_Language_Mode.pdf | 2508.08791 |
| 2025 | WebRL: Training LLM Web Agents via Self-Evolving Online Curriculum Reinforcement Learning | WebRL: self-evolving online curriculum RL for web agents; notes expensive APIs. | 2025_WebRL_Training_LLM_Web_Agents_via_Self-Evolving_Online_Curri.pdf | 2411.02337 |
| 2026 | EvoConfig: Self-Evolving Multi-Agent Systems for Efficient Autonomous Environment Configuration | EvoConfig: self-evolving multi-agent for efficient env config (software eng). | 2026_EvoConfig_Self-Evolving_Multi-Agent_Systems_for_Efficient_Au.pdf | 2601.16489 |
| 2026 | EvoTest: Evolutionary Test-Time Learning for Self-Improving Agentic Systems | EvoTest: evolutionary test-time learning for self-improving agentic systems. | 2026_EvoTest_Evolutionary_Test-Time_Learning_for_Self-Improving_A.pdf | 2510.13220 |
| 2025 | Xolver: Multi-Agent Reasoning with Holistic Experience Learning Just Like an Olympiad Team | Xolver: training-free multi-agent reasoning w/ holistic experience learning. | 2025_Xolver_Multi-Agent_Reasoning_with_Holistic_Experience_Learni.pdf | 2506.14234 |
| 2026 | Don't Just Fine-tune the Agent, Tune the Environment | Don't Just Fine-tune the Agent, Tune the Environment (env tuning paradigm). | 2026_Dont_Just_Fine-tune_the_Agent_Tune_the_Environment.pdf | 2510.10197 |
| 2025 | Agent0: Unleashing Self-Evolving Agents from Zero Data via Tool-Integrated Reasoning | Agent0: self-evolving agents from zero data via tool-integrated reasoning. | 2025_Agent0_Unleashing_Self-Evolving_Agents_from_Zero_Data_via_To.pdf | 2511.16043 |
| 2026 | ARISE: Agent Reasoning with Intrinsic Skill Evolution in Hierarchical Reinforcement Learning | ARISE: hierarchical RL with tiered skill library (Skills Manager/Worker). | 2026_ARISE_Agent_Reasoning_with_Intrinsic_Skill_Evolution_in_Hier.pdf | 2603.16060 |
| 2026 | CORAL: Towards Autonomous Multi-Agent Evolution for Open-Ended Discovery | CORAL: autonomous multi-agent evolution for open-ended discovery (code). | 2026_CORAL_Towards_Autonomous_Multi-Agent_Evolution_for_Open-Ende.pdf | 2604.01658 |
| 2025 | ArcMemo: Abstract Reasoning Composition with Lifelong LLM Memory | ArcMemo: lifelong concept-level memory distilled from reasoning traces. | 2025_ArcMemo_Abstract_Reasoning_Composition_with_Lifelong_LLM_Mem.pdf | 2509.04439 |
| 2023 | Reflexion: Language Agents with Verbal Reinforcement Learning | Reflexion: language agents with verbal reinforcement (self-reflection). | 2023_Reflexion_Language_Agents_with_Verbal_Reinforcement_Learning.pdf | 2303.11366 |
| 2025 | Socratic-Zero : Bootstrapping Reasoning via Data-Free Agent Co-evolution | Socratic-Zero: data-free agent co-evolution (Teacher/Solver/Generator). | 2025_Socratic-Zero_Bootstrapping_Reasoning_via_Data-Free_Agent_Co.pdf | 2509.24726 |
| 2023 | Teaching Large Language Models to Self-Debug | Self-Debug: teaching LLMs to self-debug code (fewer repair iterations). | 2023_Teaching_Large_Language_Models_to_Self-Debug.pdf | 2304.05128 |
| 2023 | Toolformer: Language Models Can Teach Themselves to Use Tools | Toolformer: LMs teach themselves to use tools (foundational tool use). | 2023_Toolformer_Language_Models_Can_Teach_Themselves_to_Use_Tools.pdf | 2302.04761 |
| 2026 | A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve on the Path to Artificial Super Intelligence | Survey: Self-Evolving Agents (TMLR) — what/when/how/where to evolve. | 2026_A_Survey_of_Self-Evolving_Agents_What_When_How_and_Where_to.pdf | 2507.21046 |
| 2024 | CoPS: Empowering LLM Agents with Provable Cross-Task Experience Sharing | CoPS: cross-task experience sharing for sequential reasoning agents. | 2024_CoPS_Empowering_LLM_Agents_with_Provable_Cross-Task_Experien.pdf | 2410.16670 |
| 2023 | ToRA: A Tool-Integrated Reasoning Agent for Mathematical Problem Solving | ToRA: tool-integrated reasoning agent (tools + language efficiency). | 2023_ToRA_A_Tool-Integrated_Reasoning_Agent_for_Mathematical_Prob.pdf | 2309.17452 |
| 2025 | Agent KB: Leveraging Cross-Domain Experience for Agentic Problem Solving | Agent KB: universal memory for cross-framework experience sharing. | 2025_Agent_KB_Leveraging_Cross-Domain_Experience_for_Agentic_Prob.pdf | 2507.06229 |
|  | MoT: Memory-of-Thought Enables ChatGPT to Self-Improve - ACL Anthology | MoT: Memory-of-Thought enables self-improvement (external memory). | _MoT_Memory-of-Thought_Enables_ChatGPT_to_Self-Improve_-_ACL.pdf | https://aclanthology.org/2023.emnlp-main |
| 2023 | Counterfactually Auditable Lifecycle Certification for Autonomous Agents | ToolLLM: foundational tool-use framework/benchmark (16000+ APIs). | 2023_Counterfactually_Auditable_Lifecycle_Certification_for_Auton.pdf | 2307.16789 |
| 2025 | Towards Agentic Self-Learning LLMs in Search Environment | Agentic self-learning LLMs in search; co-evolving GRM (RL-based). | 2025_Towards_Agentic_Self-Learning_LLMs_in_Search_Environment.pdf | 2510.14253 |
| 2025 | Deep Research Agents: A Systematic Examination And Roadmap | Survey: Deep Research Agents — systematic examination & roadmap. | 2025_Deep_Research_Agents_A_Systematic_Examination_And_Roadmap.pdf | 2506.18096 |
| 2026 | RetroAgent: From Solving to Evolving via Retrospective Dual Intrinsic Feedback | RetroAgent: evolving via retrospective dual intrinsic feedback. | 2026_RetroAgent_From_Solving_to_Evolving_via_Retrospective_Dual_I.pdf | 2603.08561 |
| 2026 | MemRL: Self-Evolving Agents via Runtime Reinforcement Learning on Episodic Memory | MemRL: self-evolving agents via runtime RL on episodic memory. | 2026_MemRL_Self-Evolving_Agents_via_Runtime_Reinforcement_Learnin.pdf | 2601.03192 |
| 2025 | Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory | Mem0: production-ready scalable long-term memory for agents. | 2025_Mem0_Building_Production-Ready_AI_Agents_with_Scalable_Long-.pdf | 2504.19413 |
| 2025 | SEDM: Scalable Self-Evolving Distributed Memory for Agents | SEDM: scalable self-evolving distributed memory for agents. | 2025_SEDM_Scalable_Self-Evolving_Distributed_Memory_for_Agents.pdf | 2509.09498 |
| 2025 | Scaling Agent Learning via Experience Synthesis | Scaling Agent Learning via Experience Synthesis (DreamGym). | 2025_Scaling_Agent_Learning_via_Experience_Synthesis.pdf | 2511.03773 |
| 2025 | MemGen: Weaving Generative Latent Memory for Self-Evolving Agents | MemGen: generative latent memory for self-evolving agents. | 2025_MemGen_Weaving_Generative_Latent_Memory_for_Self-Evolving_Ag.pdf | 2509.24704 |
| 2025 | Multi-Agent Evolve: LLM Self-Improve through Co-evolution | Multi-Agent Evolve: LLM self-improve through co-evolution. | 2025_Multi-Agent_Evolve_LLM_Self-Improve_through_Co-evolution.pdf | 2510.23595 |
| 2025 | A Comprehensive Survey of Self-Evolving AI Agents: A New Paradigm Bridging Foundation Models and Lifelong Agentic Systems | Survey: Comprehensive Survey of Self-Evolving AI Agents. | 2025_A_Comprehensive_Survey_of_Self-Evolving_AI_Agents_A_New_Para.pdf | 2508.07407 |
| 2025 | CAM: A Constructivist View of Agentic Memory for LLM-Based Reading Comprehension | CAM: constructivist agentic memory for long-doc reading. | 2025_CAM_A_Constructivist_View_of_Agentic_Memory_for_LLM-Based_Re.pdf | 2510.05520 |
| 2024 | CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing | CRITIC: self-correct with tool-interactive critiquing. | 2024_CRITIC_Large_Language_Models_Can_Self-Correct_with_Tool-Inte.pdf | 2305.11738 |
| 2023 | Self-Refine: Iterative Refinement with Self-Feedback | Self-Refine: iterative refinement with self-feedback. | 2023_Self-Refine_Iterative_Refinement_with_Self-Feedback.pdf | 2303.17651 |
| 2024 | A Survey on Self-Evolution of Large Language Models | Survey: Self-Evolution of Large Language Models. | 2024_A_Survey_on_Self-Evolution_of_Large_Language_Models.pdf | 2404.14387 |
| 2025 | Mem-{\alpha}: Learning Memory Construction via Reinforcement Learning | Mem-α: learning memory construction via RL. | 2025_Mem-alpha_Learning_Memory_Construction_via_Reinforcement_Lea.pdf | 2509.25911 |

## GOAL

| Year | Title | Note | PDF | Source |
|---|---|---|---|---|
| 2023 | FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance ★ | FrugalGPT: LLM cascade to reduce cost up to 98% while matching performance. *** goal landmark *** | 2023_FrugalGPT_How_to_Use_Large_Language_Models_While_Reducing_Co.pdf | 2305.05176 |
| 2025 | Multi-agent Architecture Search via Agentic Supernet | MaAS: multi-agent architecture search via supernet; query-dependent resource & token allocation. | 2025_Multi-agent_Architecture_Search_via_Agentic_Supernet.pdf | 2502.04180 |
| 2024 | RouteLLM: Learning to Route LLMs with Preference Data ★ | RouteLLM: learning to route LLMs; >2x cost reduction, no quality loss. *** goal landmark *** | 2024_RouteLLM_Learning_to_Route_LLMs_with_Preference_Data.pdf | 2406.18665 |
| 2024 | Large Language Monkeys: Scaling Inference Compute with Repeated Sampling | Large Language Monkeys: scaling inference compute via repeated sampling (cost-coverage). | 2024_Large_Language_Monkeys_Scaling_Inference_Compute_with_Repeat.pdf | 2407.21787 |
| 2024 | SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering | SWE-agent: agent-computer interfaces for SE (foundational code agent; interface design). | 2024_SWE-agent_Agent-Computer_Interfaces_Enable_Automated_Softwar.pdf | 2405.15793 |
| 2025 | Evolving Deeper LLM Thinking | Mind Evolution: evolutionary search for test-time compute; controls for inference cost. | 2025_Evolving_Deeper_LLM_Thinking.pdf | 2501.09891 |
| 2024 | Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing | Hybrid LLM: cost-efficient quality-aware query routing (40% fewer large-model calls). | 2024_Hybrid_LLM_Cost-Efficient_and_Quality-Aware_Query_Routing.pdf | 2404.14618 |
| 2023 | AutoMix: Automatically Mixing Language Models | AutoMix: route queries to larger LMs to optimize cost (self-verification router). | 2023_AutoMix_Automatically_Mixing_Language_Models.pdf | 2310.12963 |
| 2024 | Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters | Scaling test-time compute optimally (foundational cost-vs-performance tradeoff). | 2024_Scaling_LLM_Test-Time_Compute_Optimally_can_be_More_Effectiv.pdf | 2408.03314 |
| 2024 | AIOS: LLM Agent Operating System | AIOS: LLM agent OS — scheduling/resource/context/memory management for agents. | 2024_AIOS_LLM_Agent_Operating_System.pdf | 2403.16971 |
| 2025 | Universal Model Routing for Efficient LLM Inference | Universal Model Routing (UniRoute): dynamic routing to smallest feasible LLM. | 2025_Universal_Model_Routing_for_Efficient_LLM_Inference.pdf | 2502.08773 |
| 2023 | LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression ★ | LongLLMLingua: prompt compression in long-context scenarios (cost landmark). | 2023_LongLLMLingua_Accelerating_and_Enhancing_LLMs_in_Long_Contex.pdf | 2310.06839 |
| 2025 | MAS-GPT: Training LLMs to Build LLM-based Multi-Agent Systems | MAS-GPT: train LLM to build MAS in a single inference (cuts inference cost). | 2025_MAS-GPT_Training_LLMs_to_Build_LLM-based_Multi-Agent_Systems.pdf | 2503.03686 |
| 2024 | Towards Optimizing the Costs of LLM Usage | Towards Optimizing the Costs of LLM Usage (quality/latency-aware selection). | 2024_Towards_Optimizing_the_Costs_of_LLM_Usage.pdf | 2402.01742 |
| 2025 | Get Experience from Practice: LLM Agents with Record & Replay | AgentRR: record & replay for agents (reliability/privacy/cost/performance). | 2025_Get_Experience_from_Practice_LLM_Agents_with_Record_Replay.pdf | 2505.17716 |
| 2025 | Towards Efficient Multi-LLM Inference: Characterization and Analysis of LLM Routing and Hierarchical Techniques | Survey: efficient multi-LLM inference — routing & hierarchical techniques. | 2025_Towards_Efficient_Multi-LLM_Inference_Characterization_and_A.pdf | 2506.06579 |
| 2024 | Agentless: Demystifying LLM-based Software Engineering Agents | Agentless: simpler-than-agents approach to SE (cost/complexity argument). | 2024_Agentless_Demystifying_LLM-based_Software_Engineering_Agents.pdf | 2407.01489 |
|  | LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models ★ | LLMLingua: prompt compression for accelerated inference (cost landmark). | 2023_LLMLingua_Compressing_Prompts_for_Accelerated_Inference_of_L.pdf | 2310.05736 |
| 2026 | Latent Collaboration in Multi-Agent Systems | LatentMAS: latent collaboration in MAS — fewer tokens, faster inference. | 2026_Latent_Collaboration_in_Multi-Agent_Systems.pdf | 2511.20639 |
| 2024 | A Human-Inspired Reading Agent with Gist Memory of Very Long Contexts | ReadAgent: gist memory compresses context ~20x for long-context agents. | 2024_A_Human-Inspired_Reading_Agent_with_Gist_Memory_of_Very_Long.pdf | 2402.09727 |
| 2024 | RouterBench: A Benchmark for Multi-LLM Routing System | RouterBench: benchmark for multi-LLM routing systems (cost-vs-quality). | 2024_RouterBench_A_Benchmark_for_Multi-LLM_Routing_System.pdf | 2403.12031 |
| 2026 | Memory as Action: Autonomous Context Curation for Long-Horizon Agentic Tasks | MemAct: memory-as-action context curation for long-horizon efficiency. | 2026_Memory_as_Action_Autonomous_Context_Curation_for_Long-Horizo.pdf | 2510.12635 |
| 2023 | Skeleton-of-Thought: Prompting LLMs for Efficient Parallel Generation | Skeleton-of-Thought: parallel generation to cut end-to-end latency. | 2023_Skeleton-of-Thought_Prompting_LLMs_for_Efficient_Parallel_Ge.pdf | 2307.15337 |
| 2023 | Cost-Effective Hyperparameter Optimization for Large Language Model Generation Inference | EcoOptiGen: cost-effective HPO for LLM inference (budget-aware). | 2023_Cost-Effective_Hyperparameter_Optimization_for_Large_Languag.pdf | 2303.04673 |
| 2024 | Personal LLM Agents: Insights and Survey about the Capability, Efficiency and Security | Survey: Personal LLM Agents — capability, efficiency, security. | 2024_Personal_LLM_Agents_Insights_and_Survey_about_the_Capability.pdf | 2401.05459 |
| 2024 | A Survey of Resource-efficient LLM and Multimodal Foundation Models | Survey: Resource-efficient LLM & Multimodal Foundation Models. | 2024_A_Survey_of_Resource-efficient_LLM_and_Multimodal_Foundation.pdf | 2401.08092 |
| 2026 | LightMem: Lightweight and Efficient Memory-Augmented Generation | LightMem: lightweight & efficient memory-augmented generation. | 2026_LightMem_Lightweight_and_Efficient_Memory-Augmented_Generati.pdf | 2510.18866 |
| 2024 | A Survey on Efficient Inference for Large Language Models | Survey: Efficient Inference for Large Language Models. | 2024_A_Survey_on_Efficient_Inference_for_Large_Language_Models.pdf | 2404.14294 |

## Relevant — no local PDF (abstract only)

These were judged relevant from title+abstract but have no easily-resolvable PDF (OpenAlex DOI without arXiv). Fetch on demand via the DOI/URL.

| Year | Title | Tier | Note | URL |
|---|---|---|---|---|
|  | - (ACL'25 findings) Optima: Optimizing effectiveness and efficiency for llm-based multi-agent system | core | Optimizes effectiveness AND efficiency of LLM multi-agent systems (efficiency-driven). | https://aclanthology.org/2025.findings-acl.601.pdf |
| 2025 | MasRouter: Learning to Route LLMs for Multi-Agent Systems | goal | Learns to route LLMs for multi-agent systems (cost). | https://doi.org/10.18653/v1/2025.acl-long.757 |
| 2023 | Batch Prompting: Efficient Inference with Large Language Model APIs | goal | Efficient inference with LLM APIs via batching (cost). | https://doi.org/10.18653/v1/2023.emnlp-industry.74 |
| 2025 | Token-Budget-Aware LLM Reasoning | goal | Reasons under a token budget for cost control. | https://doi.org/10.18653/v1/2025.findings-acl.1274 |
| 2024 | Reasoning in Token Economies: Budget-Aware Evaluation of LLM Reasoning Strategies | goal | Budget-aware evaluation of LLM reasoning (cost). | https://doi.org/10.18653/v1/2024.emnlp-main.1112 |
| 2023 | Compressing Context to Enhance Inference Efficiency of Large Language Models | goal | Selective context compression for inference efficiency. | https://doi.org/10.18653/v1/2023.emnlp-main.391 |
| 2025 | A data-augmented model routing framework for efficient LLM deployment in edge–cloud environments | goal | Data-augmented model routing for efficient LLM deployment. | https://doi.org/10.1007/s11227-025-08034-8 |
| 2024 | TensorOpera Router: A Multi-Model Router for Efficient LLM Inference | goal | Multi-model router for efficient LLM inference. | https://doi.org/10.18653/v1/2024.emnlp-industry.34 |
| 2025 | Select-then-Route : Taxonomy guided Routing for LLMs | goal | Taxonomy-guided routing for LLMs (cost). | https://doi.org/10.18653/v1/2025.emnlp-industry.28 |
| 2023 | Frugal Prompting for Dialog Models | goal | Frugal prompting for dialog models (cost). | https://doi.org/10.18653/v1/2023.findings-emnlp.290 |
| 2026 | Accelerating PayPal's Commerce Agent with Speculative Decoding: An Empirical Study on EAGLE3 with Fine-Tuned Nemotron Models | goal | Speculative decoding applied to a production commerce agent (cost). |  |
| 2025 | SAGE: Self-evolving Agents with Reflective and Memory-augmented Abilities | method | Self-evolving agents with reflective + memory-augmented abilities. | https://doi.org/10.1016/j.neucom.2025.130470 |
|  | - (NeurIPS'25) Sirius: Self-improving multi-agent systems via bootstrapped reasoning | method | Self-improving multi-agent systems via bootstrapping. | https://neurips.cc/virtual/2025/loc/san-diego/poster/118834 |
| 2025 | Agentic skill discovery | core | Agentic skill discovery (title-only; verify venue). | https://doi.org/10.1016/j.robot.2025.105248 |
| 2025 | LLM-Based Agents for Tool Learning: A Survey | method | Survey of LLM agents for tool learning. | https://doi.org/10.1007/s41019-025-00296-9 |
| 2025 | A Survey on the Memory Mechanism of Large Language Model-based Agents | method | Survey of agent memory mechanisms. | https://doi.org/10.1145/3748302 |
| 2024 | Small LLMs Are Weak Tool Learners: A Multi-LLM Agent | method | Multi-LLM agent where small models collaborate on tool use. | https://doi.org/10.18653/v1/2024.emnlp-main.929 |
| 2024 | CodeAgent: Enhancing Code Generation with Tool-Integrated Agent Systems for Real-World Repo-level Coding Challenges | method | Tool-integrated code-generation agent. | https://doi.org/10.18653/v1/2024.acl-long.737 |

---
*Generated from 163 judged papers; see `_candidates.json` for full records incl. rejects.*
