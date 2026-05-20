```markdown
# When to Reason: Semantic Router for vLLM

Chen Wang  
IBM Research  
Yorktown Heights, NY, 10598  
Chen.Wang1@ibm.com  

Xunzhuo Liu  
Tencent  
bitliu@tencent.com  

Yuhan Liu  
University of Chicago  
yuhanl@uchicago.edu  

Yue Zhu  
IBM Research  
Yorktown Heights, NY, 10598  
yue.zhu@ibm.com  

Xiangxi Mo  
UC Berkeley  
xmo@berkeley.edu  

Junchen Jiang  
University of Chicago  
junchenj@uchicago.edu  

Huamin Chen  
Red Hat  
Boston, MA, 02210  
hchen@redhat.com  

## Abstract

Large Language Models (LLMs) demonstrate substantial accuracy gains when augmented with reasoning modes such as chain-of-thought and inference-time scaling. However, reasoning also incurs significant costs in inference latency and token usage, with environmental and financial impacts, which are unnecessary for many simple prompts. We present a semantic router that classifies queries based on their reasoning requirements and selectively applies reasoning only when beneficial. Our approach achieves a 10.2 percentage point improvement in accuracy on the MMLU-Pro benchmark while reducing response latency by 47.1% and token consumption by 48.5% compared to direct inference with vLLM. These results demonstrate that semantic routing offers an effective mechanism for striking a balance between accuracy and efficiency in open-source LLM serving systems.

## Introduction

Large Language Models (LLMs) achieve notable accuracy gains when augmented with advanced inference techniques such as chain-of-thought reasoning or inference-time scaling. Yet, these benefits come at substantial computational and energy costs, particularly when reasoning is applied indiscriminately. Prior studies [22] show that while reasoning improves performance on complex tasks, it is unnecessary for many straightforward queries. This tension makes selective reasoning a central challenge for practical LLM systems.

Recent frameworks such as LangChain/LangGraph [11] and DSPy [9] enable modular routing policies, but they require manual configuration and are tied to higher-level stacks. In contrast, open-source inference engines like vLLM [10]—the de facto standard for high-throughput LLM serving—deliver efficient inference but lack native semantic routing. Related systems (e.g., llm-d [12], Production Stack [16]) provide lightweight routing but do not support fine-grained control over reasoning. Consequently, developers using vLLM's APIs avoid vendor lock-in but remain without integrated mechanisms for adaptive reasoning.

To address this gap, we propose a semantic router for open-source inference engines. Our system integrates with vLLM and cloud-native routing frameworks (Envoy, ext_proc), classifies queries
```