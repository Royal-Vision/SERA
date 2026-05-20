```markdown
[14] Microsoft. Presidio research: Data science utilities, evaluation tools and synthetic data generation for presidio. <https://github.com/microsoft/presidio-research>, 2023. Accessed: 2025-08-29.

[15] Isaac Ong, Amjad Almahairi, Vincent Wu, Wei-Lin Chiang, Tianhao Wu, Joseph E Gonzalez, M Waleed Kadous, and Ion Stoica. Routellm: Learning to route llms from preference data. In The Thirteenth International Conference on Learning Representations, 2025.

[16] Production Stack Contributors. Production stack: Scalable inference infrastructure with vllm. <https://docs.vllm.ai/projects/production-stack/en/latest/>, 2025. Open-source project documentation, vLLM Production Stack.

[17] Zayne Sprague, Fangcong Yin, Juan Diego Rodriguez, Dongwei Jiang, Manya Wadhwa, Prasann Singhal, Xinyu Zhao, Xi Ye, Kyle Mahowald, and Greg Durrett. To cot or not to cot? chain-of-thought helps mainly on math and symbolic reasoning. arXiv preprint arXiv:2409.12183, 2024.

[18] Yubo Wang, Xueguang Ma, Ge Zhang, Yuansheng Ni, Abhranil Chandra, Shiguang Guo, Weiming Ren, Aaran Arulraj, Xuan He, Ziyan Jiang, et al. Mmlu-pro: A more robust and challenging multi-task language understanding benchmark. Advances in Neural Information Processing Systems, 37:95266–95290, 2024.

[19] Benjamin Warner, Antoine Chaffin, Benjamin Clavié, Orion Weller, Oskar Hallström, Said Taghadouini, Alexis Gallagher, Raja Biswas, Faisal Ladhak, Tom Aarsen, Nathan Cooper, Griffin Adams, Jeremy Howard, and Iacopo Poli. Smarter, better, faster, longer: A modern bidirectional encoder for fast, memory efficient, and long context finetuning and inference, 2024.

[20] Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten Bosma, Fei Xia, Ed Chi, Quoc V Le, Denny Zhou, et al. Chain-of-thought prompting elicits reasoning in large language models. Advances in neural information processing systems, 35:24824–24837, 2022.

[21] Zihao Wei, Liang Pang, Jiahao Liu, Jingcheng Deng, Shicheng Xu, Zenghao Duan, Jingang Wang, Fei Sun, Xunliang Cai, Huawei Shen, et al. Stop spinning wheels: Mitigating llm overthinking via mining patterns for early reasoning exit. arXiv preprint arXiv:2508.17627, 2025.

[22] Patrick Wilhelm, Thorsten Wittkopp, and Odej Kao. Beyond test-time compute strategies: Advocating energy-per-token in llm inference. In Proceedings of the 5th Workshop on Machine Learning and Systems, pages 208–215, 2025.

[23] Jiarui Zhang, Xiangyu Liu, Yong Hu, Chaoyue Niu, Fan Wu, and Guihai Chen. Query routing for retrieval-augmented language models. arXiv preprint arXiv:2505.23052, 2025.

[24] Yekun Zhu, Guang Chen, and Chengjun Mao. Think in blocks: Adaptive reasoning from direct response to deep reasoning. arXiv preprint arXiv:2508.15507, 2025.

# Appendix A. Additional Per-Category Results

In addition to the per-category accuracy results reported in Figure 3, we include two supplementary breakdowns that highlight the efficiency benefits of semantic routing.

The per-category breakdowns in Figures 4 and 5 confirm that the semantic router consistently improves efficiency across domains. In terms of token usage, the router reduces average consumption by nearly half relative to direct vLLM execution modes, with especially pronounced savings in knowledge-intensive subjects such as history, law, and health, where reasoning is rarely required. Similarly, the latency results show that the router sustains substantially faster response times across most categories, cutting delays by over 40% even in reasoning-sensitive areas like mathematics and physics. These results demonstrate that semantic routing not only improves aggregate efficiency