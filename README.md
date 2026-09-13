# Krabby Bot

A Discord bot that logs how long a server call has been going for.  
When a voice channel becomes empty, it posts the duration in a designated text channel and pins the server's record for the longest call duration.
Each log also includes an indented breakdown of how long each user spent in the call, accumulated across multiple joins during the same call.

Example:

```text
[voice call 1] [09/12/2026] 1 hour, 38 minutes, 17 seconds
     person1: 1 hour, 38 minutes, 17 seconds
     person2: 24 minutes, 32 seconds
```

## Slash commands

- `/setlog <channel>` - set where results are posted/pinned
- `/getlog` - show the current log channel
- `/best` - show the current server record
- `/reset` - clears server record and unpins the bot's pinned messages
- `/debug` - shows config and pin permissions
- `/ping` - health check
