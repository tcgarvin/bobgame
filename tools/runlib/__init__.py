"""Shared readers and summaries for the run-analysis tools.

`analyze_run.py` and `settlement_progress.py` are thin command lines over
this package. The modules, in dependency order:

* `runio`    - run directory resolution, :class:`RunLayout`, JSONL readers
* `report`   - :class:`Moment` and the text rendering of both reports
* `worldscan`- the single pass over ``world/ticks.jsonl.gz``
* `agenttrace` - each agent's trace files, read once
* `cost`     - docs/11_cost_accounting.md
* `social`   - conversations, giving and reflexes
"""
