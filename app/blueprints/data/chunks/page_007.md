```markdown
# Per-category accuracy per-mode values

| biology | business | chemistry | computer science | economics | engineering | health | history | law | math | other | philosophy | physics | psychology |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.8 | 0.7 | 0.6 | 0.5 | 0.8 | 0.4 | 0.4 | 0.6 | 0.5 | 0.6 | 0.5 | 0.5 | 0.6 |

## Figure 3: Per-category accuracy across all inference modes on MMLU-Pro.

# Per-category avg total tokens per-mode values

| biology | business | chemistry | computer science | economics | engineering | health | history | law | math | other | philosophy | physics | psychology |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1500 | 1200 | 1400 | 1600 | 1400 | 2000 | 1400 | 1600 | 1200 | 1800 | 1400 | 1000 | 1600 |

## Figure 4: Per-category average total tokens across all inference modes on MMLU-Pro. The semantic router consistently achieves the lowest token usage, reducing overhead in knowledge-centric domains (e.g., history, law, health) while remaining competitive in reasoning-heavy areas such as math and physics.

# Per-category avg response time per-mode values

| biology | business | chemistry | computer science | economics | engineering | health | history | law | math | other | philosophy | physics | psychology |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20 | 10 | 20 | 25 | 15 | 30 | 20 | 25 | 10 | 30 | 10 | 15 | 20 |

## Figure 5: Per-category average response latency across all inference modes on MMLU-Pro. The semantic router reduces latency substantially compared to direct vLLM modes, particularly in domains with shorter factual queries (e.g., history, philosophy). Even in complex reasoning categories, the router sustains lower response times by avoiding unnecessary reasoning overhead.
```