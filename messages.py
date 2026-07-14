"""Tower Siege — banners and report formatting."""

from config import MIN_PLAYERS, DAY_CHAT_SECONDS

NIGHT_BANNER = (
    "🌑🌑🌑🌑🌑🌑🌑🌑🌑🌑\n"
    "*NIGHT HAS FALLEN*\n"
    "The gates are sealed. No one may speak until dawn.\n"
    "Check your DMs \u2014 choose your moves.\n"
    "🌑🌑🌑🌑🌑🌑🌑🌑🌑🌑"
)

DAY_BANNER = (
    "🌞🌞🌞🌞🌞🌞🌞🌞🌞🌞\n"
    "*DAWN BREAKS*\n"
    "🌞🌞🌞🌞🌞🌞🌞🌞🌞🌞"
)

NIGHT_BANNER_DUEL = (
    "🌑🌑🌑🌑🌑🌑🌑🌑🌑🌑\n"
    "*NIGHT FALLS ON THE DUEL*\n"
    "Two towers. One will stand.\n"
    "Check your DMs.\n"
    "🌑🌑🌑🌑🌑🌑🌑🌑🌑🌑"
)


def _mention(uid, name):
    return f"[{name}](tg://user?id={uid})"


def player_list_text(game):
    lines = ["*Kingdoms remaining:*"]
    for uid in game.join_order:
        p = game.players.get(uid)
        if not p:
            continue
        status = "🟢" if p.alive else "\u2620\ufe0f"
        floors_left = 3 - sum(p.floor_broken)
        name = _mention(uid, p.name)
        if p.alive:
            lines.append(f"{status} {name} \u2014 {floors_left}/3 floors standing")
        else:
            lines.append(f"{status} {name} \u2014 eliminated")
    return "\n".join(lines)


def day_report_text(game, day_number, narrations):
    lines = [DAY_BANNER, f"*Day {day_number}*", ""]
    if narrations:
        for n in narrations:
            lines.append(n)
            lines.append("")
    else:
        lines.append("A strangely quiet night. No blows were struck.")
        lines.append("")
    lines.append(player_list_text(game))
    lines.append("")
    lines.append(f"💬 You have {DAY_CHAT_SECONDS} seconds to talk before the next night falls.")
    return "\n".join(lines)


def lobby_text(game):
    lines = ["*\u2694\ufe0f TOWER SIEGE \u2014 Lobby Open \u2694\ufe0f*", "", "Players joined:"]
    for uid in game.join_order:
        lines.append(f"\u2022 {_mention(uid, game.players[uid].name)}")
    lines.append("")
    if game.is_duel:
        lines.append("Waiting for the second player to accept the duel...")
    else:
        lines.append(f"Need at least {MIN_PLAYERS} players. Host can /startgame once ready.")
    return "\n".join(lines)


def duel_lobby_text(initiator_name, target_name):
    return (
        f"\u2694\ufe0f *TOWER SIEGE \u2014 Duel Challenge!* \u2694\ufe0f\n\n"
        f"{_mention(0, initiator_name)} has challenged {_mention(0, target_name)} to a tower battle!\n\n"
        "Both players must accept in DMs for the duel to begin."
    )


def winner_text(winner_name):
    return (
        "🏆🏆🏆🏆🏆🏆🏆🏆🏆🏆\n"
        f"*{winner_name}'s tower stands alone.*\n"
        f"*{winner_name} wins Tower Siege!*\n"
        "🏆🏆🏆🏆🏆🏆🏆🏆🏆🏆"
    )


def alliance_winner_text(names):
    return (
        "🏆🏆🏆🏆🏆🏆🏆🏆🏆🏆\n"
        f"*{names}'s alliance stood unbroken.*\n"
        f"*{names} win Tower Siege together!*\n"
        "🏆🏆🏆🏆🏆🏆🏆🏆🏆🏆"
    )
