```markdown
# Workflow
![Workflow](workflow.png)

## System architecture
![System architecture](system_architecture.png)

Figure 1: Overview of the proposed intent-aware semantic router. (a) Workflow of classification and routing; (b) system architecture.

user prompt into high-dimensional semantic embeddings, which capture the contextual meaning of the input. These embeddings are then processed by an intent classifier that determines whether the prompt corresponds to a simple factual query or a reasoning-intensive task. Based on this classification, the router directs the input to the most suitable inference pathway: lightweight inference with a non-reasoning model for simple tasks, or reasoning inference with a chain-of-thought-enabled model for complex queries. Finally, the outputs are unified into a final response. Unlike prior router approaches such as FrugalGPT and RouteLLM, which primarily operate at the model-selection level to trade off accuracy and cost, our design focuses on semantic intent-based routing and selectively invoking reasoning. This enables adaptive reasoning where costly step-by-step inference is applied only when beneficial, while maintaining low latency and efficiency for straightforward queries.

## Implementation

The implementation of our intent-aware semantic router integrates three key modules—ModernBERT fine-tuning for intent classification, a Rust-based high-performance classification core, and Golang-Rust bindings for Envoy integration—into a unified architecture, as illustrated in Figure 1b.

### ModernBERT Fine-tuning for Intent Classification

We fine-tune ModernBERT [19]—fast, memory-efficient, supports long contexts, and achieves high accuracy by incorporating modern LLM innovations like RoPE and FlashAttention—for multi-task intent classification. The training pipeline ingests three datasets: MMLU-Pro [18] (~12K academic samples across ~14 domains), Microsoft Presidio [14] (~50K token-level PII examples), and jailbreak security datasets [4]. The classification pipeline can use either CPU or GPU for real-time inline inference and simplifies the runtime environment resource requirements.

### Rust Core for High-Performance Classification

The classification engine is implemented in Rust using Hugging Face’s Candle framework [8], which enables efficient, zero-copy tensor workflows, SIMD acceleration, and optimized memory usage. It runs multi-stage parallel inference—category classification, PII detection, and jailbreak detection—leveraging Rust’s ownership model for thread safety. The pipeline batches requests and utilizes Hugging Face Tokenizers for fast tokenization, supports large context window, and chains multiple classification tasks, sustaining highly concurrent requests on commodity hardware without using expensive GPUs.
```