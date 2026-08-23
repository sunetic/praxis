# Agent

An Agent is a reusable task configuration that defines a role, goal, tools, and Skills. Use it for workflows that still require the model to make decisions from live evidence but should not be explained from scratch every time.

## Ways to create an Agent

- Use the guided flow on the Agent page.
- Enter a name, description, prompt, tools, and Skills manually.
- Save a stable Chat workflow as an Agent, then refine its configuration on the Agent page.

Saving a conversation does not copy its transcript verbatim. The result should distill the goal, method, tools, and expertise so a new session can run it again at another time or against another datasource.

## Ways to run an Agent

Run an Agent from its list and select a datasource when needed. The new session uses the Agent configuration but reads current database state; it does not replay an old answer.

Scheduler can also run an Agent. Scheduled runs have no person available to supply missing context, so their opening instruction should clearly define scope, expected output, and failure boundaries.

## Agent or Function?

- Use Agent for live reasoning and dynamic tool selection.
- Use Function when inputs and outputs are fixed and repeatability matters more.
- Start exploratory methods as Agents, then consider Functions after they stabilize.

Built-in Agents may be read-only. Custom Agents can be edited and deleted; check for dependent Schedulers before deleting one.
