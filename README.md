# Krabby Bot

A Discord bot that logs how long a server call has been going for.  
When a voice channel becomes empty, it posts the duration in a designated text channel and pins the server’s record for the longest call duration.

## Slash commands

- `/setlog <channel>` – set where results are posted/pinned
- `/getlog` – show the current log channel
- `/best` – show the current server record
- `/reset` – clears server record and unpins the bot’s pinned messages
- `/debug` – shows config and pin permissions
- `/ping` – health check
