<pending_action_resume>
A user-confirmed action has finished with status {{ action_status }} and the final tool result is present in the conversation history.

Continue the original task from that evidence using the normal Agent reasoning loop and the available tools.
- Do not treat confirmation or one completed statement as proof that the broader user request is complete.
- If the action failed, diagnose the returned evidence and choose a materially different safe next step; never repeat the same failed write unchanged.
- Prefer safe read-only inspection when more evidence is needed. Any later write must use the normal confirmation mechanism.
- Keep working while safe, relevant progress is possible. Finish only when the original request is satisfied or a real external blocker remains.
- Each resumed reasoning round must choose one explicit action: call the next appropriate domain tool, or call `agent_task_complete` when the entire original objective is complete or genuinely blocked.
- Narrative that merely announces a future action is not a terminal response.
</pending_action_resume>
