# Conclusion

This paper presented a semantic router that dynamically selects between reasoning and non-reasoning strategies to optimize large language model inference. Evaluation on MMLU-Pro shows that the router improves accuracy by more than 10 percentage points while reducing token usage and latency by nearly 50%. The approach is particularly effective in knowledge-intensive domains such as business, economics, and physics, though challenges remain in technical and reasoning-heavy areas. Integrated with vLLM, the router demonstrates that semantic routing is a practical and efficient solution for real-world inference serving.

## References

[1] Pranjal Aggarwal, Seungone Kim, Jack Lanchantin, Sean Welleck, Jason Weston, Ilia Kulikov, and Swarnadeep Saha. Optimalthinkingbench: Evaluating over and underthinking in llms. *arXiv preprint arXiv:2508.13141*, 2025.

[2] Anonymous. vLLM Semantic Router. <https://vllm-semantic-router.netlify.app/>. Accessed: 2025-08-29.

[3] Aurelio.ai. Semantic router. <https://www.aurelio.ai/semantic-router>, 2025. Accessed: 2025-08-29.

[4] Patrick Chao, Edoardo Debenedetti, Alexander Robey, Maksym Andriushchenko, Francesco Croce, Vikash Sehwag, Edgar Dobriban, Nicolas Flammarion, George J Pappas, Florian Tramer, et al. Jailbreakbench: An open robustness benchmark for jailbreaking large language models. *Advances in Neural Information Processing Systems*, 37:55005–55029, 2024.

[5] Lingjiao Chen, Matei Zaharia, and James Zou. Frugalgpt: How to use large language models while reducing cost and improving performance. *arXiv preprint arXiv:2305.05176*, 2023.

[6] Qiguang Chen, Dengyun Peng, Jinhao Liu, HuiKang Su, Jiannan Guan, Libo Qin, and Wanxiang Che. Aware first, think less: Dynamic boundary self-awareness drives extreme reasoning efficiency in large language models. *arXiv preprint arXiv:2508.11582*, 2025.

[7] Envoy Proxy Contributors. External processing filter (ext_proc). <https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter>, 2025. Accessed: 2025-08-29.

[8] Hugging Face. Candle: A minimalist machine learning framework for Rust. <https://github.com/huggingface/candle>, 2023. Accessed: 2025-08-29.

[9] Omar Khattab, Arnav Singhvi, Paridhi Maheshwari, Zhiyuan Zhang, Keshav Santhanam, Sri Vardhamanan, Saiful Haq, Ashutosh Sharma, Thomas T Joshi, Hanna Moazam, et al. Dspy: Compiling declarative language model calls into state-of-the-art pipelines. In *ICLR*, 2024.

[10] Woosuk Kwon, Zhuohan Li, Siyuan Zhuang, Ying Sheng, Lianmin Zheng, Cody Hao Yu, Joseph Gonzalez, Hao Zhang, and Ion Stoica. Efficient memory management for large language model serving with pagedattention. In *Proceedings of the 29th symposium on operating systems principles*, pages 611–626, 2023.

[11] LangChain Documentation. How to route between sub-chains. <https://js.langchain.com/docs/how_to/routing>, 2025. [Online; accessed DD-Month-YYYY].

[12] Ilm-d Contributors (including Red Hat, Google, IBM Research, CoreWeave, NVIDIA, AMD, and others). Ilm-d: A kubernetes-native high-performance distributed llm inference framework. <https://ilm-d.ai/>, 2025. Open-source project: Kubernetes-native distributed LLM inference stack built on vLLM with intelligent scheduling and prompt-aware routing.

[13] Dimitrios Michael Manias, Ali Chouman, and Abdallah Shami. Semantic routing for enhanced performance of llm-assisted intent-based 5g core network management and orchestration. In *GLOBECOM 2024-2024 IEEE Global Communications Conference*, pages 2924–2929. IEEE