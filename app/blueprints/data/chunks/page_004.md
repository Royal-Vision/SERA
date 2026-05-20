# 3.2.3 Golang + Rust (via CGO) for Cloud-Native Envoy Integration

We wrap the Rust-based classification core in a Golang layer using CGO bindings to support Envoy's External Processing (ext_proc) filter interface [7]. Envoy intercepts HTTP requests and forwards them via gRPC to the external processor, which applies real-time classification and routing decisions before responses reach backend services. The CGO layer is statically linked, minimizing runtime overhead while enabling seamless integration with Kubernetes, service meshes, and API gateway patterns. Such design pattern facilitates Cloud Native ecosystem adoption.

## 4 Evaluation

We evaluate our semantic router on an NVIDIA L4 GPU using the Qwen/Qwen3-30B-A3B model served by vLLM v0.10.1 with tensor parallelism degree 4. The evaluation is conducted on the MMLU-Pro benchmark across 14 domains, measuring accuracy, token usage, and latency. For direct vLLM comparison, we run the same model under six execution modes—neutral reasoning (NR) and explicit chain-of-thought (XC), each with reasoning enabled or disabled configurations.

Figure 2 breaks down accuracy by the 14 MMLU-Pro domains for all execution modes (NR/XC with reason_on, reason_off, and base), along with our semantic router. Across the majority of categories, the router leads in reasoning-heavy domains and remains competitive in knowledge-centric areas, indicating that selective reasoning does not sacrifice accuracy on fact-focused tasks while delivering benefits where structured reasoning is essential.

[Figure]: Per-category accuracy per-mode values

| biology | business | chemistry | computer science | economics | engineering | health | history | law | math | other | philosophy | physics | psychology |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.8 | 0.6 | 0.4 | 0.2 | 0.8 | 0.6 | 0.4 | 0.2 | 0.8 | 0.6 | 0.4 | 0.2 | 0.8 |

Table 1 summarizes the aggregate performance metrics comparing our semantic router against direct vLLM inference. Overall, the semantic router improves accuracy by 10.24 points while cutting latency by 47.1% and token usage by 48.5%.

Table 1: Overall performance of semantic router versus direct vLLM inference on MMLU-Pro.

| Method | Avg. Accuracy | Avg. Latency (s) | Avg. Tokens |
| --- | --- | --- | --- |
| Semantic Router | 58.57% | 13.09 | 887.5 |
| Direct vLLM | 48.33% | 24.76 | 1,722.1 |
| Improvement | +10.24pp | -47.1% | -48.5% |

Our evaluation shows that the semantic router delivers substantial efficiency gains while improving overall accuracy, achieving a statistically significant 10.24 percentage point increase (p < 0.01) with 48.5% fewer tokens and 47.1% lower latency. The router is particularly effective in knowledge-intensive domains such as business and economics, where accuracy improvements exceed 20 percentage points, while performance in technical areas like engineering and computer science remains more challenging. Mixed results in reasoning-heavy domains (e.g., mathematics and biology) highlight opportunities for refining routing strategies. Overall, the router demonstrates robust improvements