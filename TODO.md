# TODO

## [wf-notify] Wire workflow_hooks into runner + scheduler

`dash/cron/workflow_runner.py` MUST import + call:
- notify_workflow_started() after queued→running claim
- notify_workflow_done() after dashboard build, before status='done' commit
- notify_workflow_failed() in run-level except branch

`dash/cron/workflow_scheduler.py` MUST call:
- notify_workflow_started(source='cron') after cron-triggered run_now returns run_id

All hooks fail-soft — wrap in try/except just in case.

Import path:
```python
from dash.notifications import (
    notify_workflow_started,
    notify_workflow_done,
    notify_workflow_failed,
)
```
