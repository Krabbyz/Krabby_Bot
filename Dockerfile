# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#     _             _   _         _   _                               #
#    / \  _   _ ___| |_(_)_ __   | \ | | __ _ _   _ _   _  ___ _ __   #
#   / _ \| | | / __| __| | '_ \  |  \| |/ _` | | | | | | |/ _ \ '_ \  #
#  / ___ \ |_| \__ \ |_| | | | | | |\  | (_| | |_| | |_| |  __/ | | | #
# /_/   \_\__,_|___/\__|_|_| |_| |_| \_|\__, |\__,_|\__, |\___|_| |_| #
#                                       |___/       |___/             #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

# Dockerfile for the Discord Krabby Bot

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "discord.py>=2.4,<2.5"

RUN useradd -m -u 1000 app && chown -R app /app
USER app

COPY --chown=app:app bot.py /app/bot.py

CMD ["python", "bot.py"]
