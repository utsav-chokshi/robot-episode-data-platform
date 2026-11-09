# Future enhancements

The completed project centers on episode capture, raw-first ingestion, lineage, analytical publication and reproducible evaluation. Improving language-driven robot planning is future work.

## Existing exploratory work

An optional offline SmolVLM-256M adapter, proposal receiver and evaluation path are implemented. In the original fixed experiment, all 90 inference attempts completed, but **0/90 responses met the required plan schema**. The tested provider is unsuitable for task selection under that configuration. No proposal executed a robot action. The original outputs and scores remain unchanged in [benchmark validation](benchmark-validation.md) and the [complete report](evidence/evaluation-report.json).

## Planned improvements

1. Evaluate a stronger local provider within a declared CPU/memory budget, initially on a development set.
2. Investigate constrained JSON generation or a separately versioned structured-output adapter. Preserve raw responses and distinguish native model output from any transformation.
3. Expand scenes and instructions, then freeze a new evaluation set before measuring the new version. Keep the original results alongside it.
4. Assess schema validity, object/zone grounding, correct clarification/rejection and latency separately. A schema-valid response alone is insufficient.
5. Consider any execution integration only after independent validation and explicit action-boundary design. The current platform keeps proposals execution-ineligible.

These are planned enhancements, not completed improvements or promised scores. The data platform and structured baseline remain independently reproducible without model weights.
