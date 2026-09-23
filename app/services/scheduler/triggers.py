"""Shared five-field schedule expressions with Sunday numbered 0 or 7.

Day-of-month and weekday restrictions are conjunctive, matching the execution
scheduler. APScheduler owns calendar and DST behavior; we only map weekday IDs.
"""

import re

from apscheduler.triggers.cron import CronTrigger


def cron_trigger(expression: str, timezone: str = "UTC") -> CronTrigger:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expression must have 5 fields")
    minute, hour, day, month, weekday = fields
    # APScheduler numbers Monday as 0; Unix cron numbers Sunday as 0 or 7.
    # Convert the bounded weekday field to names so neither consumer drifts.
    names = ("sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
    normalized = []
    for item in weekday.split(","):
        if re.search(r"[a-zA-Z]", item):
            normalized.append(item)
            continue
        match = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", item)
        if match is None:
            raise ValueError("Invalid cron weekday")
        base, step = match.group(1), int(match.group(2) or 1)
        if base == "*":
            start, end = 0, 6
        elif "-" in base:
            start, end = map(int, base.split("-"))
        else:
            start = int(base)
            end = 7 if match.group(2) else start
        if not 0 <= start <= end <= 7 or step < 1:
            raise ValueError("Invalid cron weekday range or step")
        normalized.extend(names[index] for index in range(start, end + 1, step))
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=",".join(dict.fromkeys(normalized)),
        timezone=timezone,
    )
