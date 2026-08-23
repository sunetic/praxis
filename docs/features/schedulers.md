# Scheduler

Scheduler runs configured Functions or Agents on a cron expression or fixed interval for inspection, collection, periodic analysis, and notification.

## Two target types

| Target | Best for | Behavior |
| --- | --- | --- |
| Function | Fixed inputs and outputs that need stronger repeatability | Runs a published version and accepts JSON parameters |
| Agent | Tasks that read live state and make dynamic decisions | Uses a fixed opening instruction; results vary with the model and live state |

## Create and run

Select a target, name the task, and enter its timing rule and input. The UI can help turn a natural-language schedule into timing configuration and parameters. After saving, run it immediately, pause or resume it, edit it, and inspect run status and results.

Maximum retries and backoff control failure recovery. Retries are appropriate for temporary network failures or provider rate limits; they should not hide persistent permission, SQL, or task-design errors.

## Scheduled Agent guidance

A scheduled Agent cannot ask a person for missing context. Its instruction should include datasource scope, time window, evidence requirements, output format, and prohibited actions. Manually run every critical task at least once and confirm that missing data, model rate limits, and tool failures produce understandable outcomes.

Built-in Schedulers may have restricted editing; custom Schedulers support the full lifecycle.
