# Scheduling and calendar
> Open when: Meetings, reminders, timelines, or calendar changes
> New lesson: `daftar kb add scheduling-and-calendar "one-line lesson" --by <name>`

## Owner guidance

## Lessons
- 2026-11-09 [daftar] Cron runs in UTC: compute local-time schedules with a timezone-aware helper, never a fixed offset that breaks at daylight saving changes.
- 2026-11-09 [daftar] Never hardcode a UTC offset in code; use the IANA zone name (e.g. `ZoneInfo("Europe/Paris")`).
- 2026-11-09 [daftar] A one-shot scheduled send needs absolute paths (cron PATH is minimal), a retry window, a sentinel against duplicates, and a success or failure report.
- 2026-11-09 [daftar] An approval phrased as "send it tomorrow at 9" is a scheduled approval: schedule it and do not ask again.
- 2026-11-09 [daftar] A mention in a task or calendar comment does not guarantee the recipient is notified; verify the notification exists.
