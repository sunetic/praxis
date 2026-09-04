The resumed Agent run must make an explicit control decision.

- If safe work remains, call the appropriate domain tool now.
- If the entire original objective is satisfied, call `agent_task_complete` with outcome `completed` and a self-contained final response.
- If progress truly requires new user input or external authority, call `agent_task_complete` with outcome `blocked`, explain the evidence-based blocker, and state the smallest required user action.
- Do not end with a plan, promise, or description of a future tool call.
