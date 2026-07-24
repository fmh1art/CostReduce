# Fixed Harbor adapters (2026-07-24)

This directory contains versioned AgentDiet and ZipAct Harbor adapters used by
replacement experiments.  The legacy adapters remain untouched so jobs that
were already running keep one implementation for their entire lifetime.

## AgentDiet

- Keeps the artifact's delayed schedule, XML trajectory representation,
  context window, threshold, prompt, and reduction acceptance rule.
- Runs the internal compressor as a visible-text call with hidden reasoning
  disabled.
- Rejects empty and length-truncated compressor outputs instead of replacing a
  trajectory step with empty text.
- Preserves compressor calls and erased actor usage records for complete API
  cost accounting.

## ZipAct

- Keeps the upstream initializer, memory-less Actor, state Updater, and
  Goal-World-Constraint state organization.
- Restores the upstream 50-step episode cap.
- Retains the immutable task outside updater JSON, bounds state size, and uses
  a larger visible JSON budget suitable for Harbor's long coding tasks.
- Preserves all initializer/updater calls for complete API cost accounting.

Both adapters inherit mini-swe-agent's normal system/instance prompt and bash
protocol.  New jobs mount these directories read-only via
`baselines/run_harbor_smoke.py`; no non-baseline framework code is changed.
