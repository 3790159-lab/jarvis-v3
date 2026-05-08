## Review Notes

1. Improve the flow by explicitly stating that the pre-run checks are only executed if all preconditions have been met.
2. Use clear language when describing the intent classification confidence threshold and pending queue depth limit, e.g., instead of "pending queue depth limit", use "number of tasks in the waiting queue".
3. Clarify the purpose section by providing more context on why this SOP is necessary.
4. Consider adding a section for continuous monitoring and updates to ensure compliance with changing project requirements.

## Final Revised SOP

### Purpose
The goal of this Standard Operating Procedure (SOP) is to guide full lifecycle handling of Telegram requests for Jarvis operators, ensuring efficient execution confirmation while maintaining risk management and quality control standards.

### Preconditions

To initiate the standard procedure:
 - Ensure the bot process is operational
 - Verify the worker loop is running
 - Confirm both SQLite DBs are available
 - Enforce `.env` policy adherence
 - Enable LLM backend access
 - Maintain API access

### Pre-run Checks
Before proceeding:

- Ensure intent classification accuracy meets 80% threshold
- Limit waiting queue depth to 20 tasks
- Enforce safe execution by enforcing `EXECUTION_MODE=safe`

### Approval & Risk

Decision making is governed by the following policy rules extracted from `risk_policies.py`:

| Risk Level | Action |
|-----------|--------|
| low | Approve |
| guarded | Approve/Reject through API call |
| high_risk | Reject and initiate auto-rejection blocklist from safety.py |
| forbidden | Reject immediately |

### Execution Procedure

1. Manage Task state machine (`queued â running â completed`)
2. Dispatch worker using identified task
3. Enforce 60-second timeout behavior
4. Monitor and split messages into chunks exceeding 500 characters

### Post-run Validation

 - Check the shell return code
 - Review latency records in trace for flagged threshold (> 30 seconds)
 - Confirm Telegram delivery request success
 - Perform multi-step mission closure upon completion

### Failure Handling

- Trigger error pattern table upon failure, detailing causes and actions to take
- Apply `/retry` limit on failed tasks (max three repetitions before escalating)
- Enforce stale approval SLA of 15 minutes for unapproved requests
- Outline bot-down protocol in the context of system recovery