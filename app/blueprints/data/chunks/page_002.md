by intent, and selectively applies reasoning only when beneficial. Experiments on the MMLU-Pro benchmark across 14 domains show that our router achieves higher accuracy while reducing latency and token usage by nearly half.

Our contributions are as follows:
- We identify the need for semantic routing in open-source inference engines to enable reasoning-aware inference.
- We design, implement, and open-source [2] a high-performance and scalable semantic router that integrates with vLLM and Envoy/ext_proc for fine-grained reasoning control, accelerating Cloud Native ecosystem integration.
- We evaluate the semantic router on the MMLU-Pro benchmark and show that it improves accuracy by 10.2 percentage points while reducing response latency by 47.1% and token consumption by 48.5% compared to direct vLLM inference.

# Background

## Routers in LLM Systems

Recent work has explored the use of routers to improve the efficiency and accuracy of LLM inference by dynamically deciding how queries should be handled. FrugalGPT [5] achieves up to 98% cost reduction by learning which combinations of LLMs to invoke for different queries, leveraging prompt adaptation, approximation, and cascaded model selection across commercial APIs. RouteLLM [15] similarly trains router models to choose between stronger and weaker LLMs during inference, guided by human preference data and augmentation, yielding substantial cost savings while maintaining accuracy across benchmarks such as MT Bench, MMLU, and GSM8K. These approaches highlight the promise of router-based techniques for improving inference performance, but they remain focused on model-level routing.

## The Need for Selective Reasoning

While advanced reasoning strategies such as Chain-of-Thought (CoT) prompting can improve accuracy, recent studies highlight that reasoning is not universally beneficial and often incurs substantial computational overhead. Wilhelm et al. [5] demonstrate that CoT can increase energy costs by up to 150 times while offering little benefit for knowledge-based tasks. Similarly, Aggarwal et al. find that LLMs frequently “overthink” simple queries and “underthink” complex ones [1], leading to inefficiencies. Meta-analyses by Sprague et al. [17] and the original CoT work by Wei et al. [20] further establish that CoT primarily improves performance on math and logic tasks, with limited gains elsewhere and even degraded accuracy in smaller models. To mitigate these inefficiencies, recent frameworks [6, 24, 21] introduce adaptive reasoning strategies that dynamically regulate reasoning depth, reducing token usage while maintaining accuracy.

## Semantic Routing

A semantic router refers to an emerging class of request forwarding systems for LLM inference, in which routing decisions are guided by the semantic meaning of the input rather than by explicit keywords or manually defined rules [13, 3]. The router operates by encoding both user queries and candidate routing utterances into high-dimensional embeddings [23] that capture contextual meaning, and then selecting the target pathway with the highest semantic similarity, typically measured using metrics such as cosine distance. Semantic routing provides a lightweight and efficient mechanism for query-level control, making it a promising foundation for reasoning-aware routing.

# System Design

## System Design

Our system integrates a semantic router with a reasoning mode selector to dynamically balance efficiency and accuracy in LLM inference. As shown in Figure 1a, the process begins by encoding the