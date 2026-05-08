# Jarvis Real Task Execution SOP

> Generated via Test 4 multi-agent workflow.
> Mission ID: real_test_04_sop_fix_9d473f52

## Final SOP
# Standard Operating Procedure (SOP) for Running Jarvis on Real Tasks

## Introduction
This SOP provides a structured process to run Jarvis on real tasks, ensuring all necessary setups and checks are performed. Familiarity with the code and operational risks is necessary for successful task execution.

## 1. Preconditions
Before executing tasks, ensure the following are set up correctly:
- Run `start_api.bat` and `start_worker.bat` to initiate the necessary services.
- Confirm that your environment is configured by reviewing the `.env` setup in the README. This is essential for proper functionality and connectivity throughout the operation.

## 2. Pre-run Checks
Use the `LocalShellExecutor` without sandbox settings to verify local environment readiness. Set the `LLM_MODE` variable according to the task's requirements, as it influences how the language model interprets commands.

## 3. Risk Classification
Understand the risks associated with different execution modes:
- **shell_local**: It executes commands directly without any filtering, making it critical to verify command safety.
- **codex_cloud**: Requires valid credentials to access cloud resources. Ensure credentials are not exposed in code repositories.

## 4. /chat Procedure
Invoke the `/chat` endpoint using the `ChatRequest` model. Key parameters include:
- `wait_for_completion`: Determines whether to wait for the response before proceeding.
- `max_wait_seconds`: Specifies the timeout duration for the operation.
This procedure allows interaction with the language model for task handling.

## 5. /intake Procedure
The `/intake` endpoint organizes tasks through a hierarchy of Goals, Missions, and Tasks defined in `models.py`. Each level helps structure the task for efficient processing and management.

## 6. Executor Notes
- Be aware of the default shell timeout, typically set to 600 seconds, and configure the `DEFAULT_SHELL` setting according to your environment.
- Ensure the `codex` CLI dependency is properly installed for cloud interactions.

## 7. Post-run Validation
After task execution, validate outcomes by checking the `returncode` in results:
- Possible TaskStatus enumerations: `queued`, `running`, `succeeded`, `failed`.
Understanding these terms is crucial for interpreting task results and troubleshooting issues.

## 8. Failure Handling
Monitor and handle task failures effectively:
- Use the `unhandled_exception` flag within the worker to identify unprocessed exceptions.
- For tasks stuck in the `running` state, investigate logs and consider restarting as necessary.

## 9. Artifacts
Track generated reports and outputs located in `jarvis_stage3_artifacts/reports/`. These artifacts are integral for reviewing task performance and outcomes.

## Conclusion
Adhere to this SOP for effectively utilizing Jarvis on real tasks, ensuring safe execution, risk management, and thorough post-run validation to maintain operational standards.

---

## Review Summary
1. **Clarify Permissions**: The opening instruction regarding permission for file writing is vague. Specify how this affects the operation and suggest a clear action step for the user.
2. **Section Objectives**: Clearly state the objective of each section to clarify what the user should learn or accomplish.
3. **Preconditions Detail**: Provide a brief explanation of why each precondition is necessary, enhancing comprehension.
4. **Risk Classification Guidance**: Provide examples of risks associated with `shell_local` and `codex_cloud` to illustrate the implications more clearly.
5. **Procedure Steps**: Include a brief description of what the `/chat` and `/intake` procedures accomplish to provide context before diving into code specifics.
6. **Validation Commentary**: Expand on post-run validation to emphasize why it is vital and how to interpret the results effectively.
7. **Failure Handling Clarity**: Highlight methods for resolving common issues rather than just mentioning the flags and scenarios.
8. **Artifact Overview**: Briefly describe the types of artifacts generated and their value for the user.

---

## Artifact Paths
- Claude draft: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_tests\test4_sop_package\test4_claude_draft.md
- OpenAI review: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_tests\test4_sop_package\test4_openai_review.md
- Final SOP: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_tests\test4_sop_package\test4_final_sop.md
- Package report: C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\real_tests\test4_sop_package\test4_package_report.txt