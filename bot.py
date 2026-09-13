# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#     _             _   _         _   _                               #
#    / \  _   _ ___| |_(_)_ __   | \ | | __ _ _   _ _   _  ___ _ __   #
#   / _ \| | | / __| __| | '_ \  |  \| |/ _` | | | | | | |/ _ \ '_ \  #
#  / ___ \ |_| \__ \ |_| | | | | | |\  | (_| | |_| | |_| |  __/ | | | #
# /_/   \_\__,_|___/\__|_|_| |_| |_| \_|\__, |\__,_|\__, |\___|_| |_| #
#                                       |___/       |___/             #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

# bot.py
# Discord bot that tracks time spent with 1+ people in each voice channel.
# Posts duration when a channel becomes empty and pins the longest record.

import os
import json
import shutil
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

DATA_FILE = os.getenv("DATA_FILE", "data.json")

# ---- Persistence helpers ----------------------------------------------------

def _ensure_parent_dir(path: str):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _empty_schema():
    return {"guilds": {}, "best_per_guild": {}, "active": {}, "pin_per_guild": {}}


def load_data():
    _ensure_parent_dir(DATA_FILE)
    if not os.path.exists(DATA_FILE):
        return _empty_schema()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # Corrupt file: back it up and start fresh
        try:
            shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
        except Exception:
            pass
        return _empty_schema()
    # Migrate missing keys
    if not isinstance(data, dict):
        data = {}
    data.setdefault("guilds", {})
    data.setdefault("best_per_guild", {})
    data.setdefault("active", {})
    data.setdefault("pin_per_guild", {})
    for k in ("guilds", "best_per_guild", "active", "pin_per_guild"):
        if not isinstance(data.get(k), dict):
            data[k] = {}
    return data


def save_data():
    _ensure_parent_dir(DATA_FILE)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, DATA_FILE)  # atomic on normal filesystems
    except OSError as e:
        # On some Docker/WSL bind-mounts of single files, replace fails with EBUSY.
        # Fallback: direct overwrite (non-atomic) so the bot continues reliably.
        print(f"[WARN] Atomic replace failed ({e}). Falling back to direct overwrite.")
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(DATA, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.remove(tmp)
            except Exception:
                pass
        except Exception as e2:
            print(f"[ERROR] Failed to write data file: {e2}")


DATA = load_data()

# ---- Bot setup --------------------------------------------------------------

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

tree = bot.tree

# ---- Utilities --------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)


def fmt_duration(total_seconds: int) -> str:
    """Return human duration with days, omitting zero units.
    If all units are zero, returns "0 seconds".
    """
    if total_seconds < 0:
        total_seconds = 0
    days = total_seconds // 86400
    rem = total_seconds % 86400
    hours = rem // 3600
    minutes = (rem % 3600) // 60
    seconds = rem % 60

    parts = []
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if seconds or not parts:
        parts.append(f"{seconds} second" + ("s" if seconds != 1 else ""))

    return ", ".join(parts)


def humans_in_channel(vchan: discord.VoiceChannel) -> int:
    return sum(1 for m in vchan.members if not getattr(m, "bot", False))


def human_members_in_channel(vchan: discord.VoiceChannel) -> list[discord.Member]:
    return [m for m in vchan.members if not getattr(m, "bot", False)]


def member_name(member: discord.Member) -> str:
    return member.display_name or member.name


def make_session(vchan: discord.VoiceChannel, started_at: Optional[datetime] = None) -> dict:
    started_at = started_at or now_utc()
    started_iso = started_at.isoformat()
    users = {}
    for member in human_members_in_channel(vchan):
        users[str(member.id)] = {
            "name": member_name(member),
            "total_seconds": 0,
            "joined_at": started_iso,
        }
    return {"started_at": started_iso, "users": users}


def coerce_session(vchan: discord.VoiceChannel, raw_session) -> dict:
    if isinstance(raw_session, dict):
        raw_session.setdefault("started_at", now_utc().isoformat())
        users = raw_session.setdefault("users", {})
        if not isinstance(users, dict):
            raw_session["users"] = {}
        return raw_session

    if isinstance(raw_session, str):
        try:
            started_at = datetime.fromisoformat(raw_session)
        except Exception:
            started_at = now_utc()
        return make_session(vchan, started_at)

    return make_session(vchan)


def track_member_join(session: dict, member: discord.Member, joined_at: datetime):
    user_id = str(member.id)
    users = session.setdefault("users", {})
    user = users.setdefault(
        user_id,
        {"name": member_name(member), "total_seconds": 0, "joined_at": None},
    )
    user["name"] = member_name(member)
    if not user.get("joined_at"):
        user["joined_at"] = joined_at.isoformat()


def track_member_leave(session: dict, member: discord.Member, left_at: datetime):
    users = session.setdefault("users", {})
    user = users.get(str(member.id))
    if not isinstance(user, dict):
        return

    joined_iso = user.get("joined_at")
    if not joined_iso:
        return

    try:
        joined_at = datetime.fromisoformat(joined_iso)
    except Exception:
        joined_at = left_at

    elapsed = int((left_at - joined_at).total_seconds())
    user["total_seconds"] = int(user.get("total_seconds", 0)) + max(0, elapsed)
    user["joined_at"] = None
    user["name"] = member_name(member)


def finalize_user_totals(session: dict, ended_at: datetime) -> list[dict]:
    totals = []
    for user in session.get("users", {}).values():
        if not isinstance(user, dict):
            continue

        total_seconds = int(user.get("total_seconds", 0))
        joined_iso = user.get("joined_at")
        if joined_iso:
            try:
                joined_at = datetime.fromisoformat(joined_iso)
            except Exception:
                joined_at = ended_at
            total_seconds += max(0, int((ended_at - joined_at).total_seconds()))

        if total_seconds > 0:
            totals.append({
                "name": user.get("name") or "Unknown user",
                "seconds": total_seconds,
            })

    return sorted(totals, key=lambda row: (-row["seconds"], row["name"].lower()))


async def ensure_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    conf = DATA.setdefault("guilds", {}).get(str(guild.id), {})
    ch_id = conf.get("log_channel_id")
    if not ch_id:
        return None
    channel = guild.get_channel(ch_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        channel = await guild.fetch_channel(ch_id)
        return channel if isinstance(channel, discord.TextChannel) else None
    except Exception:
        return None


def can_pin_in(channel: discord.TextChannel) -> bool:
    guild = channel.guild
    me = guild.me  # type: ignore
    if me is None:
        return False
    perms = channel.permissions_for(me)
    return perms.manage_messages and perms.read_messages and perms.read_message_history and perms.send_messages


def build_log_content(vchan: discord.VoiceChannel, date_str: str, seconds: int, user_totals: list[dict]) -> str:
    lines = [f"[{vchan.name}] [{date_str}] {fmt_duration(seconds)}"]
    lines.extend(f"     {row['name']}: {fmt_duration(row['seconds'])}" for row in user_totals)
    return "\n".join(lines)


async def post_and_maybe_pin(
    guild: discord.Guild,
    vchan: discord.VoiceChannel,
    seconds: int,
    user_totals: list[dict],
):
    log_channel = await ensure_log_channel(guild)
    if not log_channel:
        print(f"[WARN] No log channel set for guild {guild.id}")
        return

    date_str = now_utc().strftime("%m/%d/%Y")
    content = build_log_content(vchan, date_str, seconds, user_totals)
    msg = await log_channel.send(content)

    gid = str(guild.id)
    best_per_guild = DATA.setdefault("best_per_guild", {})
    rec = best_per_guild.get(gid)
    prev_seconds = rec.get("seconds", 0) if isinstance(rec, dict) else 0
    is_strictly_best = seconds > prev_seconds

    if is_strictly_best:
        pin_per_guild = DATA.setdefault("pin_per_guild", {})
        previous_pin_id = pin_per_guild.get(gid)
        if previous_pin_id:
            try:
                prev = await log_channel.fetch_message(previous_pin_id)
                if prev.pinned:
                    await prev.unpin(reason="New longest voice session in guild")
            except Exception as e:
                print(f"[INFO] Could not unpin previous: {e}")
        if can_pin_in(log_channel):
            try:
                await msg.pin(reason="Longest voice session in this server")
                pin_per_guild[gid] = msg.id
            except discord.Forbidden:
                print("[WARN] Missing permission to pin in log channel.")
            except Exception as e:
                print(f"[WARN] Failed to pin: {e}")
        else:
            print("[WARN] Bot lacks Manage Messages/Read History/Send in log channel; cannot pin.")
        best_per_guild[gid] = {
            "seconds": seconds,
            "message_id": msg.id,
            "channel_id": vchan.id,
            "date": date_str,
        }
        save_data()


async def evaluate_channel(vchan: discord.VoiceChannel):
    ch_id = str(vchan.id)
    occupied = humans_in_channel(vchan) >= 1
    active = DATA.setdefault("active", {})
    raw_session = active.get(ch_id)

    if occupied and raw_session is None:
        active[ch_id] = make_session(vchan)
        save_data()
    elif raw_session is not None:
        session = coerce_session(vchan, raw_session)
        active[ch_id] = session
        if occupied:
            save_data()
            return

        ended_at = now_utc()
        try:
            start = datetime.fromisoformat(session.get("started_at", ended_at.isoformat()))
        except Exception:
            start = ended_at
        elapsed = int((ended_at - start).total_seconds())
        user_totals = finalize_user_totals(session, ended_at)
        active.pop(ch_id, None)
        save_data()
        await post_and_maybe_pin(vchan.guild, vchan, elapsed, user_totals)


# ---- Events -----------------------------------------------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    for guild in bot.guilds:
        for vchan in guild.voice_channels:
            try:
                if humans_in_channel(vchan) >= 1 and str(vchan.id) not in DATA.setdefault("active", {}):
                    DATA["active"][str(vchan.id)] = make_session(vchan)
                elif humans_in_channel(vchan) == 0 and str(vchan.id) in DATA.setdefault("active", {}):
                    DATA["active"].pop(str(vchan.id), None)
                elif str(vchan.id) in DATA.setdefault("active", {}):
                    DATA["active"][str(vchan.id)] = coerce_session(vchan, DATA["active"][str(vchan.id)])
            except Exception:
                pass
    save_data()
    try:
        await tree.sync()
        print("Slash commands synced globally.")
    except Exception as e:
        print("Slash sync failed:", e)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if getattr(member, "bot", False):
        return

    before_channel = before.channel if before and isinstance(before.channel, discord.VoiceChannel) else None
    after_channel = after.channel if after and isinstance(after.channel, discord.VoiceChannel) else None

    if before_channel == after_channel:
        return

    changed_at = now_utc()
    active = DATA.setdefault("active", {})

    if before_channel:
        raw_session = active.get(str(before_channel.id))
        if raw_session is not None:
            session = coerce_session(before_channel, raw_session)
            track_member_leave(session, member, changed_at)
            active[str(before_channel.id)] = session
            save_data()

    if after_channel:
        raw_session = active.get(str(after_channel.id))
        if raw_session is not None:
            session = coerce_session(after_channel, raw_session)
            track_member_join(session, member, changed_at)
            active[str(after_channel.id)] = session
            save_data()

    for vchan in (before_channel, after_channel):
        if not vchan:
            continue
        try:
            await evaluate_channel(vchan)
        except Exception as e:
            print("Error evaluating channel:", vchan.id, e)


# ---- Slash Commands ---------------------------------------------------------

@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(channel="Text channel where results will be posted")
@tree.command(name="setlog", description="Set the text channel where results are posted and pins are made")
async def setlog(interaction: discord.Interaction, channel: discord.TextChannel):
    gid = str(interaction.guild_id)
    DATA.setdefault("guilds", {}).setdefault(gid, {})["log_channel_id"] = channel.id
    save_data()
    await interaction.response.send_message(f"Log channel set to {channel.mention}.", ephemeral=True)


@tree.command(name="getlog", description="Show the current log channel")
async def getlog(interaction: discord.Interaction):
    try:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        ch = await ensure_log_channel(interaction.guild)
        if ch:
            await interaction.response.send_message(f"Current log channel: {ch.mention}", ephemeral=True)
        else:
            await interaction.response.send_message("No log channel set. Use /setlog.", ephemeral=True)
    except Exception as e:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)


@tree.command(name="best", description="Show the longest voice session recorded in this server")
async def best(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        gid = str(interaction.guild_id)
        rec = DATA.setdefault("best_per_guild", {}).get(gid)
        if not isinstance(rec, dict):
            await interaction.followup.send("No record yet for this server.")
            return
        seconds = rec.get("seconds", 0)
        vchan = interaction.guild.get_channel(rec.get("channel_id")) if interaction.guild else None
        ch_name = vchan.name if isinstance(vchan, discord.VoiceChannel) else "(deleted channel)"
        date_str = rec.get("date", now_utc().strftime("%m/%d/%Y"))
        await interaction.followup.send(
            f"Current Best Time — [{ch_name}] [{date_str}] {fmt_duration(seconds)}"
        )
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")


@app_commands.default_permissions(manage_guild=True)
@tree.command(name="reset", description="Reset this server's best time and unpin the bot's pinned messages in the log channel")
async def reset(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        if not interaction.guild:
            await interaction.followup.send("Run this in a server.")
            return
        ch = await ensure_log_channel(interaction.guild)
        if not ch:
            await interaction.followup.send("No log channel set. Use /setlog.")
            return
        me = interaction.guild.me
        try:
            pinned = await ch.pins()
        except discord.Forbidden:
            await interaction.followup.send("Missing permission to view pins in the log channel.")
            return
        unpinned = 0
        for m in pinned:
            if me and m.author.id == me.id:
                try:
                    await m.unpin(reason="/reset invoked")
                    unpinned += 1
                except Exception:
                    pass
        gid = str(interaction.guild_id)
        DATA.setdefault("best_per_guild", {}).pop(gid, None)
        DATA.setdefault("pin_per_guild", {}).pop(gid, None)
        save_data()
        await interaction.followup.send(f"Reset complete. Unpinned {unpinned} message(s).")
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")


@tree.command(name="debug", description="Show bot config and permissions")
async def debug(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        if not interaction.guild:
            await interaction.followup.send("Run this in a server.")
            return
        ch = await ensure_log_channel(interaction.guild)
        if not ch:
            await interaction.followup.send("No log channel set. Use /setlog.")
            return
        perms_ok = can_pin_in(ch)
        gid = str(interaction.guild_id)
        rec = DATA.setdefault("best_per_guild", {}).get(gid)
        best_s = rec.get("seconds") if isinstance(rec, dict) else None
        await interaction.followup.send(
            f"Log channel: {ch.mention}\nCan pin there: {perms_ok}\nBest seconds: {best_s}\nData file: {DATA_FILE}"
        )
    except Exception as e:
        await interaction.followup.send(f"Error: {e}")


@tree.command(name="ping", description="Health check")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)


# ---- Entry point ------------------------------------------------------------

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("Set DISCORD_BOT_TOKEN in your environment.")
        raise SystemExit(1)
    bot.run(token)
