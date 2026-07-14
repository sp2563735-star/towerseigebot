"""
Tower Siege — Telegram bot entrypoint.
Run:  TOWER_SIEGE_TOKEN=xxxx python3 bot.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes,
)
from telegram.error import TelegramError

from config import (
    MOVES, ALLIANCE_MOVES, NORMAL_MOVE_IDS, CLASS_MOVES,
    MIN_PLAYERS, MAX_PLAYERS, MAX_MOVES_PER_NIGHT,
    ALLIANCES_UNLOCK_DAY, DAY_CHAT_SECONDS, NIGHT_TIMEOUT_SECONDS,
    NIGHT_BANNER_URL, DAY_BANNER_URL,
    SOLDIER_COUNT, SOLDIER_DAMAGE, SOLDIER_DEFENSE,
    MAX_SOLDIER_DEFENSE, MAX_SOLDIER_PER_TARGET,
    EVENT_BANNERS,
)
from models import Game, Player, Alliance
from resolution import resolve_night_phase1, resolve_night_phase2
import messages as msg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("towersiege")

GAMES: dict[int, Game] = {}
USER_GAME: dict[int, int] = {}
PENDING: dict[int, dict] = {}
SOLDIER_PENDING: dict[int, dict] = {}
SAC_PENDING: dict[int, dict] = {}
HEAL_DATA: dict[int, dict] = {}
HEAL_ACCEPTED: set[int] = set()
HEAL_DECLINED: set[int] = set()

ALLIANCE_MSG: dict[int, int] = {}  # uid -> message_id of alliance menu

# uid -> message_id of their current live "Request Alliance" reminder button,
# so each new day's reminder can expire the previous one instead of stacking.
ALLY_REMINDER_MSG: dict[int, int] = {}


async def _send_banner(context, chat_id, url, caption, parse_mode="Markdown"):
    if url:
        try:
            await context.bot.send_animation(chat_id, animation=url, caption=caption, parse_mode=parse_mode)
            return
        except TelegramError:
            log.warning("Banner GIF failed, falling back to text.")
    await context.bot.send_message(chat_id, caption, parse_mode=parse_mode)


def _hp_bar(hp, width=10):
    filled = max(0, min(width, round((hp / 100) * width)))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _mention(uid, name):
    return f"[{name}](tg://user?id={uid})"


def _name_fn(game):
    def fn(uid):
        pl = game.players.get(uid)
        if not pl:
            return "someone"
        return _mention(uid, pl.name)
    return fn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rows(flat_buttons, per_row=2):
    return [flat_buttons[i:i + per_row] for i in range(0, len(flat_buttons), per_row)]


def _alive_targets(game, exclude_uids=None):
    exclude = set(exclude_uids or [])
    targets = []
    for p in game.alive_players():
        if p.user_id in exclude:
            continue
        blocked = False
        for ex_uid in exclude:
            if game.is_same_alliance(ex_uid, p.user_id):
                blocked = True
                break
        if blocked:
            continue
        targets.append(p)
    return targets


def _count_class_used(state, class_name):
    count = 0
    for act in state.get("moves", []):
        mv = MOVES.get(act["move_id"])
        if mv and mv.get("class") == class_name:
            count += 1
    return count


def _class_remaining(game, p, state, class_name):
    moves = []
    for mid in CLASS_MOVES.get(class_name, []):
        if p.move_uses.get(mid, 0) <= 0:
            continue
        already = any(a["move_id"] == mid for a in state.get("moves", []))
        if already:
            continue
        moves.append(mid)
    return moves


def _soldier_label(p, idx):
    if p.soldier_is_forced_rest(idx):
        return f"🔒 Soldier {idx+1} (forced rest)"
    return f"🪖 Soldier {idx+1} (ready)"


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

async def send_main_menu(context, game, p, edit_message=None):
    uid = p.user_id
    state = PENDING.setdefault(uid, {"moves": [], "chosen_counts": {}, "view": "main"})
    state["chat_id"] = game.chat_id
    slots_left = MAX_MOVES_PER_NIGHT - len(state["moves"])
    submitted_moves = p.submitted
    submitted_soldiers = p.soldiers_submitted
    soldier_state = SOLDIER_PENDING.setdefault(uid, {})

    lines = []
    if submitted_moves:
        lines.append("🔒 *Moves locked in*")
    else:
        lines.append(f"🌙 *Night {game.day_number} \u2014 Choose your moves*")
        lines.append(f"Slots left: {slots_left}/{MAX_MOVES_PER_NIGHT}")
        lines.append(f"Moves chosen: {', '.join(MOVES[a['move_id']]['name'] for a in state['moves']) or 'none'}")

    text = "\n".join(lines)
    buttons = []

    if not submitted_moves:
        class_buttons = []
        for cls_name in ["Knight", "Mage", "Healer"]:
            cls_moves = CLASS_MOVES.get(cls_name, [])
            remaining = 0
            for mid in cls_moves:
                if p.move_uses.get(mid, 0) > 0 and not any(a["move_id"] == mid for a in state.get("moves", [])):
                    remaining += 1
            if remaining == 0 or slots_left <= 0:
                label = f"\u26e8\ufe0f {cls_name} (used)" if remaining == 0 else f"\u26e8\ufe0f {cls_name}"
                class_buttons.append(InlineKeyboardButton(label, callback_data="mv_na:none"))
            else:
                label = f"\u26e8\ufe0f {cls_name} ({remaining})"
                class_buttons.append(InlineKeyboardButton(label, callback_data=f"cls:{cls_name}"))
        buttons.extend(_rows(class_buttons, 3))

        soldier_buttons = []
        for idx in range(SOLDIER_COUNT):
            if submitted_soldiers:
                continue
            if p.soldier_is_forced_rest(idx):
                soldier_buttons.append(InlineKeyboardButton(f"🔒 Soldier {idx+1} (forced rest)", callback_data=f"sld_na:{idx}"))
            elif idx in soldier_state:
                soldier_buttons.append(InlineKeyboardButton(f"🔒 Soldier {idx+1} (assigned)", callback_data=f"sld_na:{idx}"))
            else:
                soldier_buttons.append(InlineKeyboardButton(f"🪖 Soldier {idx+1} (ready)", callback_data=f"sld_pick:{idx}"))
        buttons.extend(_rows(soldier_buttons, 3))
    else:
        text += "\n\n\u2694\ufe0f *Soldiers:*"

    if not submitted_soldiers and not submitted_moves:
        buttons.append([InlineKeyboardButton(f"\u2705 Submit moves ({len(state['moves'])}/{MAX_MOVES_PER_NIGHT})", callback_data="donenight")])

    if not submitted_soldiers and submitted_moves:
        soldier_buttons2 = []
        for idx in range(SOLDIER_COUNT):
            if p.soldier_is_forced_rest(idx):
                soldier_buttons2.append(InlineKeyboardButton(f"🔒 Soldier {idx+1} (forced rest)", callback_data=f"sld_na:{idx}"))
            elif idx in soldier_state:
                soldier_buttons2.append(InlineKeyboardButton(f"🔒 Soldier {idx+1} (assigned)", callback_data=f"sld_na:{idx}"))
            else:
                soldier_buttons2.append(InlineKeyboardButton(f"🪖 Soldier {idx+1} (ready)", callback_data=f"sld_pick:{idx}"))
        buttons.extend(_rows(soldier_buttons2, 3))
        buttons.append([InlineKeyboardButton("\u2705 Lock in soldiers", callback_data="sld_submit")])

    if p.bonus_restores > 0:
        buttons.append([InlineKeyboardButton(f"\u2728 Restore moves ({p.bonus_restores}/2)", callback_data="bonus_menu")])

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    try:
        if edit_message:
            await edit_message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await context.bot.send_message(uid, text, reply_markup=markup, parse_mode="Markdown")
    except TelegramError:
        try:
            await context.bot.send_message(
                game.chat_id,
                f"\u26a0\ufe0f Couldn't DM {p.name} \u2014 they need to start a private chat with me first!"
            )
        except TelegramError:
            pass


async def send_class_menu(context, game, p, class_name, edit_message):
    uid = p.user_id
    state = PENDING.setdefault(uid, {"moves": [], "chosen_counts": {}, "view": "main"})
    slots_left = MAX_MOVES_PER_NIGHT - len(state["moves"])
    move_ids = CLASS_MOVES.get(class_name, [])
    move_buttons = []
    for mid in move_ids:
        mv = MOVES.get(mid)
        if not mv:
            continue
        used_up = p.move_uses.get(mid, 0) <= 0
        already_picked = any(a["move_id"] == mid for a in state.get("moves", []))
        disabled = used_up or already_picked or slots_left <= 0
        label = mv["name"]
        if used_up:
            label += " (used up)"
        elif already_picked:
            label += " \u2714\ufe0f"
        if disabled:
            move_buttons.append(InlineKeyboardButton(label, callback_data=f"mv_na:{mid}"))
        else:
            move_buttons.append(InlineKeyboardButton(label, callback_data=f"mv:{mid}"))
    buttons = _rows(move_buttons, 2)
    buttons.append([InlineKeyboardButton("\u00ab Back", callback_data="backmenu")])
    text = f"*{class_name}* \u2014 pick a move ({len(state['moves'])}/{MAX_MOVES_PER_NIGHT} used)"
    await edit_message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def send_soldier_menu(context, game, p, edit_message):
    uid = p.user_id
    state = SOLDIER_PENDING.setdefault(uid, {})
    ready_buttons = []
    lines = ["🤪 *Command your soldiers*", ""]
    for idx in range(SOLDIER_COUNT):
        if p.soldier_is_forced_rest(idx):
            lines.append(f"🔒 Soldier {idx+1} \u2014 forced rest tonight")
            continue
        dest = state.get(idx)
        if dest is not None:
            if dest == "home":
                lines.append(f"🔒 Soldier {idx+1} \u2014 Defending home")
            elif dest == "idle":
                lines.append(f"🔒 Soldier {idx+1} \u2014 Resting")
            else:
                tgt = game.players.get(dest)
                lines.append(f"🔒 Soldier {idx+1} \u2014 Attacking {tgt.name if tgt else '???'}")
        else:
            lines.append(f"Soldier {idx+1} \u2014 \u26a1 Ready")
            ready_buttons.append(InlineKeyboardButton(f"Soldier {idx+1}", callback_data=f"sld_pick:{idx}"))
    buttons = _rows(ready_buttons, 3)
    buttons.append([InlineKeyboardButton("\u2705 Lock in soldiers", callback_data="sld_submit")])
    text = "\n".join(lines)
    await edit_message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def send_soldier_deploy_menu(edit_message, game, p, idx):
    uid = p.user_id
    state = SOLDIER_PENDING.setdefault(uid, {})
    target_counts = {}
    home_count = 0
    for k, v in state.items():
        if v == "home":
            home_count += 1
        elif v not in ("idle", None):
            target_counts[v] = target_counts.get(v, 0) + 1
    home_disabled = home_count >= MAX_SOLDIER_DEFENSE
    buttons = [
        [InlineKeyboardButton(f"🏠 Defend Home{' (full)' if home_disabled else ''}", callback_data=f"sld_na:cap" if home_disabled else f"sld_home:{idx}"),
         InlineKeyboardButton("💤 Rest", callback_data=f"sld_rest:{idx}")],
    ]
    attack_buttons = []
    for t in _alive_targets(game, {uid}):
        cnt = target_counts.get(t.user_id, 0)
        if cnt >= MAX_SOLDIER_PER_TARGET:
            attack_buttons.append(InlineKeyboardButton(f"🔒 Attack {t.name} (full)", callback_data="sld_na:cap"))
        else:
            attack_buttons.append(InlineKeyboardButton(f"\u2694\ufe0f Attack {t.name}", callback_data=f"sld_atk:{idx}:{t.user_id}"))
    buttons.extend(_rows(attack_buttons, 2))
    buttons.append([InlineKeyboardButton("\u00ab Back", callback_data="sld_back")])
    await edit_message.edit_text(
        f"Soldier {idx+1} \u2014 where to send them?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def send_alliance_menu(context, game, p, edit_message=None):
    uid = p.user_id
    a = game.get_alliance(uid)
    if not a or a.head_id != uid:
        return

    lines = ["🤝 *Alliance Command*", ""]
    lines.append(f"Alliance with {', '.join(game.players[m].name for m in a.members if m != uid)}")
    lines.append("")

    if p.alliance_submitted:
        lines.append("🔒 *Alliance moves locked in*")
    elif p.alliance_moves_pending:
        names = [ALLIANCE_MOVES[m["move_id"]]["name"] for m in p.alliance_moves_pending]
        lines.append(f"Pending: {', '.join(names)}")
    lines.append("")

    move_buttons = []
    pending_ids = {m["move_id"] for m in p.alliance_moves_pending}
    for mid, mv in ALLIANCE_MOVES.items():
        remaining = a.move_uses.get(mid)
        if mid == "bhoomi_domain":
            if mid in pending_ids:
                label = f"\u2705 Boundless Tower Domain (active)"
                move_buttons.append(InlineKeyboardButton(label, callback_data="ally_na"))
            elif a.bhoomi_forced_rest:
                label = f"\U0001f4a4 Boundless Tower Domain (resting)"
                move_buttons.append(InlineKeyboardButton(label, callback_data="ally_na"))
            elif a.bhoomi_streak >= 2:
                label = f"\U0001f4a4 Boundless Tower Domain (cooldown)"
                move_buttons.append(InlineKeyboardButton(label, callback_data="ally_na"))
            else:
                label = f"\U0001f331 Boundless Tower Domain ({a.bhoomi_streak}/2)"
                move_buttons.append(InlineKeyboardButton(label, callback_data=f"ally_move:bhoomi_domain"))
        elif mid in pending_ids:
            label = f"\u2705 {mv['name']} (used)"
            move_buttons.append(InlineKeyboardButton(label, callback_data="ally_na"))
        elif remaining is not None and remaining <= 0:
            label = f"\u274c {mv['name']} (used)"
            move_buttons.append(InlineKeyboardButton(label, callback_data="ally_na"))
        else:
            label = f"\u2694\ufe0f {mv['name']}"
            move_buttons.append(InlineKeyboardButton(label, callback_data=f"ally_move:{mid}"))

    buttons = _rows(move_buttons, 3)

    if not p.alliance_submitted and p.alliance_moves_pending:
        buttons.append([InlineKeyboardButton("🔒 Lock in alliance moves", callback_data="ally_submit")])

    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(buttons)
    try:
        if edit_message:
            await edit_message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            sent = await context.bot.send_message(uid, text, reply_markup=markup, parse_mode="Markdown")
            ALLIANCE_MSG[uid] = sent.message_id
    except TelegramError:
        pass


# ---------------------------------------------------------------------------
# Lobby commands
# ---------------------------------------------------------------------------

async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Start this in your group chat, not DM.")
        return
    existing = GAMES.get(chat_id)
    if existing and existing.phase != "ended":
        await update.message.reply_text("A game is already running in this group. Wait for it to finish.")
        return
    game = Game(chat_id, is_duel=False)
    game.host_id = update.effective_user.id
    game.group_title = update.effective_chat.title or "the group"
    GAMES[chat_id] = game
    if not _add_player(game, update.effective_user):
        await update.message.reply_text("You're already in another game.")
        del GAMES[chat_id]
        return
    bot_username = (await context.bot.get_me()).username
    sent = await update.message.reply_text(
        msg.lobby_text(game), parse_mode="Markdown",
        reply_markup=_lobby_keyboard(game, bot_username, chat_id),
    )
    game.lobby_message_id = sent.message_id
    await update.message.reply_text(
        f"Tap Join above to enter. Host: /startgame once ready (min {MIN_PLAYERS}, max {MAX_PLAYERS}). "
        "Use /info to understand the game.",
    )
    context.job_queue.run_once(
        lobby_timeout_callback, 300, data=game.chat_id, name=f"lobby_timeout_{game.chat_id}",
    )


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a 1v1 duel."""
    chat_id = update.effective_chat.id
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use this in a group.")
        return
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("Reply to someone's message with /invitetotowerbattle to challenge them to a duel.")
        return
    target_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None
    if not target_user:
        for entity in update.message.entities or []:
            if entity.type == "mention" or entity.type == "text_mention":
                try:
                    target_user = await context.bot.get_chat(entity.user.id)
                except:
                    pass
                break
    if not target_user:
        await update.message.reply_text("Reply to someone's message with /invitetotowerbattle to challenge them.")
        return
    if target_user.id == update.effective_user.id:
        await update.message.reply_text("You can't duel yourself!")
        return
    if target_user.is_bot:
        await update.message.reply_text("You can't duel a bot!")
        return

    existing = GAMES.get(chat_id)
    if existing and existing.phase != "ended":
        await update.message.reply_text("A game is already running in this group.")
        return

    init_id = update.effective_user.id
    target_id = target_user.id

    if USER_GAME.get(init_id) and GAMES.get(USER_GAME[init_id]) and GAMES[USER_GAME[init_id]].phase != "ended":
        await update.message.reply_text("You're already in a game. Finish that first.")
        return
    if USER_GAME.get(target_id) and GAMES.get(USER_GAME[target_id]) and GAMES[USER_GAME[target_id]].phase != "ended":
        await update.message.reply_text(f"{target_user.first_name} is already in another game.")
        return

    game = Game(chat_id, is_duel=True)
    game.host_id = update.effective_user.id
    game.group_title = update.effective_chat.title or "the group"
    init_name = update.effective_user.first_name or "Player 1"
    target_name = target_user.first_name or "Player 2"
    GAMES[chat_id] = game

    p = Player(init_id, init_name)
    game.players[init_id] = p
    game.join_order.append(init_id)
    USER_GAME[init_id] = chat_id

    await update.message.reply_text(
        f"\u2694\ufe0f Duel challenge sent to {target_name}! Waiting for them to accept in DMs."
    )

    try:
        await context.bot.send_message(
            init_id,
            f"You challenged *{target_name}* to a tower duel!\n\nWaiting for them to accept...",
            parse_mode="Markdown",
        )
    except TelegramError:
        pass

    buttons = [
        [InlineKeyboardButton("\u2705 Accept Duel", callback_data=f"duel_accept:{chat_id}:{init_id}:{target_id}"),
         InlineKeyboardButton("\u274c Decline", callback_data=f"duel_decline:{chat_id}:{init_id}:{target_id}")],
    ]
    try:
        await context.bot.send_message(
            target_id,
            f"\u2694\ufe0f *{init_name}* has challenged you to a tower duel!\n\n"
            f"Accept or decline below.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
    except TelegramError:
        pass

    context.job_queue.run_once(
        duel_timeout_callback, 120, data=game.chat_id, name=f"duel_timeout_{game.chat_id}",
    )


def _add_player(game: Game, user):
    if user.id in game.players:
        return False
    if len(game.players) >= MAX_PLAYERS:
        return False
    if USER_GAME.get(user.id) and GAMES.get(USER_GAME[user.id]) and GAMES[USER_GAME[user.id]].phase != "ended":
        return False
    p = Player(user.id, user.first_name or user.username or str(user.id))
    game.players[user.id] = p
    game.join_order.append(user.id)
    USER_GAME[user.id] = game.chat_id
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if not context.args:
        await update.message.reply_text("Hey! Tap Join from your group's lobby to get started.")
        return
    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid link.")
        return
    game = GAMES.get(target_chat_id)
    if not game or game.phase != "lobby":
        await update.message.reply_text("That lobby isn't open anymore.")
        return
    if update.effective_user.id in game.players:
        await update.message.reply_text("You're already in the game!")
        return
    if not _add_player(game, update.effective_user):
        await update.message.reply_text("That lobby is full.")
        return
    group_title = getattr(game, "group_title", "the group")
    await update.message.reply_text(f"You joined the game in {group_title}")
    lobby_msg_id = getattr(game, "lobby_message_id", None)
    if lobby_msg_id:
        try:
            bot_username = (await context.bot.get_me()).username
            await context.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=lobby_msg_id,
                text=msg.lobby_text(game),
                parse_mode="Markdown",
                reply_markup=_lobby_keyboard(game, bot_username, game.chat_id),
            )
        except TelegramError:
            pass


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if not game or game.phase != "lobby":
        await update.message.reply_text("No open lobby here. Start one with /newgame.")
        return
    if _add_player(game, update.effective_user):
        bot_username = (await context.bot.get_me()).username
        await update.message.reply_text(msg.lobby_text(game), parse_mode="Markdown", reply_markup=_lobby_keyboard(game, bot_username, chat_id))
    else:
        await update.message.reply_text("You're already in, or the lobby is full.")


async def cmd_startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if not game or game.phase != "lobby":
        await update.message.reply_text("No lobby to start here.")
        return
    if game.is_duel:
        await update.message.reply_text("Duel games start automatically when both accept.")
        return
    if update.effective_user.id != game.host_id:
        await update.message.reply_text("Only the host can start it.")
        return
    if len(game.players) < MIN_PLAYERS:
        await update.message.reply_text(f"Need at least {MIN_PLAYERS} players to start.")
        return

    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if not getattr(bot_member, "can_delete_messages", False):
            raise TelegramError("missing delete_messages permission")
    except TelegramError as e:
        await update.message.reply_text(
            "I need admin rights with 'Delete Messages' to start. Please grant them and try again.\n({})".format(e)
        )
        return

    game.phase = "night"
    game.day_number = 1
    await update.message.reply_text("The siege begins tonight. \u2694\ufe0f")
    await start_night(game, context)


async def cmd_cancelgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if not game or game.phase == "ended":
        await update.message.reply_text("No active game here to cancel.")
        return
    user_id = update.effective_user.id
    if user_id != game.host_id:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("Only the game host or group admins can cancel this game.")
                return
        except TelegramError:
            await update.message.reply_text("Only the game host or group admins can cancel this game.")
            return

    if context.job_queue:
        for job_name in ("night_timeout", "day_end", "heal_timeout", "lobby_timeout", "duel_timeout"):
            job_name_full = f"{job_name}_{chat_id}"
            existing = context.job_queue.get_jobs_by_name(job_name_full)
            for j in existing:
                j.schedule_removal()

    try:
        lid = game.lobby_message_id
        if lid:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=lid,
                text="🛑 Game cancelled by the host.",
            )
    except TelegramError:
        pass
    _cleanup_game(game)
    del GAMES[chat_id]
    await update.message.reply_text("🛑 Game cancelled. Start a fresh one with /newgame.")


# ---------------------------------------------------------------------------
# Info command
# ---------------------------------------------------------------------------

INFO_DETAILS = {
    "pandey_slash": (
        "\u2694\ufe0f *Nirvana Sword Slash* \u2014 Knight\n\n"
        "A massive, devastating sword strike that deals *50 damage* to an enemy's tower.\n"
        "This is one of the strongest single attacks in the game. Use it when you want to "
        "cripple someone badly in one night.\n\n"
        "Uses: 1 per game"
    ),
    "chamar_teleporter": (
        "🌀 *Dino Dimension* \u2014 Knight\n\n"
        "Kidnap a player and drag them out of their tower! The kidnapped player will "
        "*skip their next night* \u2014 they can't do anything.\n\n"
        "To escape before the next night, they must sacrifice 1 unused move using "
        "*Almighty Mulla* (Healer move). If they don't escape, they lose a whole turn.\n\n"
        "Uses: 1 per game"
    ),
    "bengali_shield": (
        "🛡\ufe0f *Holy Shield* \u2014 Mage\n\n"
        "Summon a magical shield around your tower. For the entire night, *all attacks "
        "and soldier strikes* aimed at you are completely blocked.\n\n"
        "This includes normal attacks, alliance attacks, and soldiers. Nothing gets through.\n\n"
        "Uses: 1 per game"
    ),
    "jharkhand_seal": (
        "🔒 *Judgement Seal* \u2014 Mage\n\n"
        "Seal an enemy's normal moves for the night. They can't use any of their "
        "class abilities (Knight, Mage, Healer moves).\n\n"
        "However, it does *not* block their soldiers or alliance moves. "
        "They can still send soldiers at you!\n\n"
        "Uses: 1 per game"
    ),
    "chandal_eyes": (
        "👁\ufe0f *Chandal Eyes* \u2014 Mage\n\n"
        "Hypnotize a player. If someone attacks you tonight, the attack gets "
        "*redirected* to the player you hypnotized instead.\n\n"
        "How to use:\n"
        "1. Choose the player you want to hypnotize (the bait)\n"
        "2. Choose who you want their attack redirected to (the target)\n\n"
        "This also works on Judgement Seal \u2014 the seal gets redirected too!\n\n"
        "Uses: 1 per game"
    ),
    "rajpoot_mirror": (
        "🩸 *Divine Slash Reflection* \u2014 Mage\n\n"
        "Place a magical mirror around your tower. If someone hits you with "
        "*Nirvana Sword Slash*, it gets reflected \u2014 *both* you and the attacker take 25 damage.\n\n"
        "This only works against Nirvana Sword Slash. Other attacks pass through normally.\n\n"
        "Uses: 1 per game"
    ),
    "yadav_milk": (
        "🥛 *Magic Milk* \u2014 Healer\n\n"
        "A versatile potion with two uses:\n\n"
        "1. *Pre-emptively* (risky, high reward): Pick it as a normal move during the night. "
        "It absorbs up to *35 HP* of incoming damage \u2014 but if no one attacks you, it's wasted.\n"
        "2. *Reactive* (safe): After results are announced, if you took damage and "
        "used 2 or fewer moves, you get a 50-second window to restore *25 HP*.\n\n"
        "Uses: 2 per game"
    ),
    "almighty_mulla": (
        "\u26d3\ufe0f *Almighty Mulla* \u2014 Healer\n\n"
        "This move can *only* be used reactively \u2014 you can't pick it during the night.\n\n"
        "If you were kidnapped (Dino Dimension), you can sacrifice *1 other unused move* "
        "along with this to break free and avoid skipping your next night.\n\n"
        "Think of it as your escape plan if someone targets you for kidnapping.\n\n"
        "Uses: 1 per game"
    ),
    "super_soldier_serum": (
        "\U0001f9ea *Super Soldier Serum* \u2014 Healer\n\n"
        "Inject your troops with a powerful serum that enhances their combat abilities "
        "for *one night only*.\n\n"
        "\u2022 Soldier attack damage: 14 \u2192 *24*\n"
        "\u2022 Soldier defense block: 8 \u2192 *12*\n\n"
        "Use this before deploying your soldiers for maximum impact. "
        "The effect wears off at dawn.\n\n"
        "Uses: 1 per game"
    ),
}

CLASS_INFO = {
    "Knight": (
        "🛡\ufe0f *Knight* \u2014 The Attacker\n\n"
        "Knights are all about dealing heavy damage and disrupting enemies.\n"
        "They have 2 moves:\n\n"
        "\u2022 *Nirvana Sword Slash* \u2014 50 damage nuke (1 use)\n"
        "\u2022 *Dino Dimension* \u2014 Kidnap a player (1 use)\n\n"
        "Use the Knight when you want to be aggressive and take down enemy towers."
    ),
    "Mage": (
        "🔮 *Mage* \u2014 The Strategist\n\n"
        "Mages control the battlefield with shields, seals, mirrors, and mind tricks.\n"
        "They have 4 moves:\n\n"
        "\u2022 *Holy Shield* \u2014 Blocks all attacks for a night (1 use)\n"
        "\u2022 *Judgement Seal* \u2014 Locks enemy's normal moves (1 use)\n"
        "\u2022 *Chandal Eyes* \u2014 Redirect an attack to someone else (1 use)\n"
        "\u2022 *Divine Slash Reflection* \u2014 Reflects Nirvana Sword Slash (1 use)\n\n"
        "Use the Mage when you want to outsmart your enemies and protect yourself."
    ),
    "Healer": (
        "💊 *Healer* \u2014 The Survivor\n\n"
        "Healers keep their tower alive with healing, buffs, and escape options.\n"
        "They have 3 moves:\n\n"
        "\u2022 *Magic Milk* \u2014 Pre-emptive absorb 35 HP or reactive heal 25 HP (2 uses)\n"
        "\u2022 *Super Soldier Serum* \u2014 Boost soldiers to 24 attack / 12 defense for one night (1 use)\n"
        "\u2022 *Almighty Mulla* \u2014 Escape kidnapping by sacrificing 1 move (1 use)\n\n"
        "Use the Healer when you need to survive, empower troops, or recover from heavy damage."
    ),
}

SOLDIER_INFO = (
    "\U0001fa96 *Soldiers*\n\n"
    "You command *5 soldiers* each night. They are your loyal army \u2014 use them "
    "to defend your tower or attack enemies.\n\n"
    "*What each soldier can do:*\n"
    "\U0001f3e0 *Defend Home* \u2014 Each soldier on defense blocks *8 damage* "
    "(max 3 soldiers on defense = 24 damage blocked)\n"
    "\u2694\ufe0f *Attack Enemy* \u2014 Each attacking soldier deals *14 damage* "
    "(max 2 soldiers per enemy per night = 28 damage)\n"
    "\U0001f4a4 *Rest* \u2014 Soldiers who stay home and rest recover their stamina\n\n"
    "*Rest System (important!):*\n"
    "\u2022 Every time you deploy a soldier, they work for 1 night.\n"
    "\u2022 After *2 consecutive work nights*, the soldier is *forced to rest* "
    "the next night (can't be deployed).\n"
    "\u2022 Soldiers who are idle recover \u2014 once they've rested as many nights "
    "as they worked, they're fully refreshed.\n\n"
    "*Strategy Tip:* Rotate your soldiers! Don't use the same 3 every night "
    "or they'll get exhausted. Keep some resting while others fight."
)

ALLIANCE_INFO = (
    "🤝 *Alliances*\n\n"
    "Alliances unlock on *Day 3*. You can request to form a 2-player pact with "
    "another surviving player. The player with higher HP becomes the *alliance head*.\n\n"
    "*Alliance Moves (head only):*\n\n"
    "\u2694\ufe0f *Seelampur Strike* \u2014 "
    "Deal a massive *100 damage* to any enemy tower. One-shot potential!\n"
    "(1 use per game)\n\n"
    "\u2744\ufe0f *Ice Spear* \u2014 "
    "Freeze *all players not in your alliance*. Their normal moves AND soldiers "
    "are voided for the night. They can't do anything.\n"
    "(1 use per game)\n\n"
    "🌱 *Boundless Tower Domain* \u2014 "
    "Cast a protective ward over your alliance. Any enemy alliance attack "
    "(including freezes) aimed at your members is deflected.\n"
    "Has a 2-1 cycle: use it 2 nights, then forced rest 1 night.\n"
    "(Unlimited uses, but follows the cycle)\n\n"
    "*Winner Conditions:*\n"
    "\u2022 If the lobby has *6+ players* and 2 allied players are the last "
    "ones standing \u2014 they win *together*.\n"
    "\u2022 If the lobby has *5 or fewer* players, the alliance automatically "
    "breaks when only 2 remain \u2014 they fight 1v1 for the throne.\n\n"
    "*Breaking an alliance:* Either member can break it anytime from their DM menu."
)

GAME_INTRO = (
    "🏀 *Welcome to Tower Siege!*\n\n"
    "You are a king with a tower. Your tower has *100 HP* spread across *3 floors*:\n"
    "\u2022 Floor 1: 30 HP (breaks at 70 HP)\n"
    "\u2022 Floor 2: 30 HP (breaks at 40 HP)\n"
    "\u2022 Floor 3: 40 HP (breaks at 0 HP)\n\n"
    "Each night, you pick up to *3 moves* from your classes (Knight, Mage, Healer) "
    "and deploy your *5 soldiers*. Survive, destroy enemy towers, and be the last "
    "king standing!\n\n"
    "Tap a topic below to learn more:"
)


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /info in my DM to learn the game!")
        return
    buttons = [
        [InlineKeyboardButton("🪖 Soldiers", callback_data="info_soldiers"),
         InlineKeyboardButton("\u2694\ufe0f 3 Pillars", callback_data="info_pillars"),
         InlineKeyboardButton("🤝 Alliance", callback_data="info_alliance")],
    ]
    await update.message.reply_text(
        GAME_INTRO,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


async def cb_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "info_soldiers":
        buttons = [[InlineKeyboardButton("\u00ab Back", callback_data="info_back")]]
        await query.edit_message_text(
            SOLDIER_INFO,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    elif data == "info_pillars":
        buttons = [
            [InlineKeyboardButton("🛡\ufe0f Knight", callback_data="info_class:Knight"),
             InlineKeyboardButton("🔮 Mage", callback_data="info_class:Mage"),
             InlineKeyboardButton("💊 Healer", callback_data="info_class:Healer")],
            [InlineKeyboardButton("\u00ab Back", callback_data="info_back")],
        ]
        await query.edit_message_text(
            "*\u2694\ufe0f The 3 Pillars*\n\n"
            "You belong to one of three classes. Each class has unique moves.\n"
            "Tap a class to see its moves explained in detail:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    elif data == "info_alliance":
        buttons = [[InlineKeyboardButton("\u00ab Back", callback_data="info_back")]]
        await query.edit_message_text(
            ALLIANCE_INFO,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    elif data.startswith("info_class:"):
        class_name = data.split(":", 1)[1]
        cls_moves = CLASS_MOVES.get(class_name, [])
        move_buttons = []
        for mid in cls_moves:
            mv = MOVES.get(mid)
            if mv:
                move_buttons.append(InlineKeyboardButton(mv["name"], callback_data=f"info_move:{mid}"))
        buttons = _rows(move_buttons, 2)
        buttons.append([InlineKeyboardButton("\u00ab Back", callback_data="info_pillars")])
        text = CLASS_INFO.get(class_name, f"*{class_name}*")
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    elif data.startswith("info_move:"):
        move_id = data.split(":", 1)[1]
        details = INFO_DETAILS.get(move_id, "No info available.")
        buttons = [[InlineKeyboardButton("\u00ab Back", callback_data=f"info_class:{MOVES[move_id]['class']}")]]
        await query.edit_message_text(
            details,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

    elif data == "info_back":
        buttons = [
            [InlineKeyboardButton("🪖 Soldiers", callback_data="info_soldiers"),
             InlineKeyboardButton("\u2694\ufe0f 3 Pillars", callback_data="info_pillars"),
             InlineKeyboardButton("🤝 Alliance", callback_data="info_alliance")],
        ]
        await query.edit_message_text(
            GAME_INTRO,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Lobby / duel timeout callbacks
# ---------------------------------------------------------------------------

async def lobby_timeout_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    game = GAMES.get(chat_id)
    if not game or game.phase != "lobby" or game.is_duel:
        return
    try:
        lid = game.lobby_message_id
        if lid:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=lid,
                text="⏰ Lobby closed \u2014 no one started the game in 5 minutes.",
            )
    except TelegramError:
        pass
    try:
        await context.bot.send_message(chat_id, "⏰ No one started the game. Lobby closed after 5 minutes.")
    except TelegramError:
        pass
    _cleanup_game(game)
    del GAMES[chat_id]


async def duel_timeout_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    game = GAMES.get(chat_id)
    if not game or not game.is_duel or game.phase != "lobby":
        return
    for uid in list(game.players.keys()):
        try:
            await context.bot.send_message(uid, "⏰ The duel challenge expired.")
        except TelegramError:
            pass
    try:
        await context.bot.send_message(chat_id, "⏰ The duel challenge timed out.")
    except TelegramError:
        pass
    _cleanup_game(game)
    del GAMES[chat_id]


# ---------------------------------------------------------------------------
# Alliance request lifecycle helpers
# ---------------------------------------------------------------------------
# game.pending_alliance_requests is shaped as:
#   dict[target_id] -> list of {"requester_id", "request_msg_id", "sent_msg_id"}
# so multiple simultaneous incoming requests to the same player are tracked
# individually and can each be expired/edited on accept, decline, or night fall.

async def _expire_request_messages(context, game, target_id, entries, reason_text):
    for entry in entries:
        try:
            await context.bot.edit_message_text(
                chat_id=target_id, message_id=entry["request_msg_id"], text=reason_text,
            )
        except TelegramError:
            pass
        requester_id = entry["requester_id"]
        sent_msg_id = entry.get("sent_msg_id")
        if sent_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=requester_id, message_id=sent_msg_id, text=reason_text,
                )
            except TelegramError:
                pass


async def _clear_all_pending_requests(context, game, reason_text):
    for target_id, entries in list(game.pending_alliance_requests.items()):
        await _expire_request_messages(context, game, target_id, entries, reason_text)
    game.pending_alliance_requests.clear()


def _player_eligible_for_reminder(game, p):
    return (
        p.alive
        and not p.has_ever_been_allied
        and p.alliance_id is None
        and not game.is_duel
        and len(game.alive_players()) > 2
        and not any(
            entries and any(e["requester_id"] == p.user_id for e in entries)
            for entries in game.pending_alliance_requests.values()
        )
    )


async def _refresh_alliance_reminders(game: Game, context: ContextTypes.DEFAULT_TYPE):
    """Send/refresh the 'Request Alliance' reminder for every still-eligible
    player, expiring each one's previous day's reminder first so there's
    never more than one live button per person."""
    if game.is_duel or game.day_number < ALLIANCES_UNLOCK_DAY:
        return
    button = InlineKeyboardMarkup([[InlineKeyboardButton("🤝 Request Alliance", callback_data="ally_open")]])
    for p in game.alive_players():
        if not _player_eligible_for_reminder(game, p):
            continue
        old_msg_id = ALLY_REMINDER_MSG.pop(p.user_id, None)
        if old_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=p.user_id, message_id=old_msg_id,
                    text="\u23f0 This reminder has expired \u2014 a new day has begun.",
                )
            except TelegramError:
                pass
        try:
            sent = await context.bot.send_message(
                p.user_id,
                f"🤝 Day {game.day_number} \u2014 tap below to request an alliance.",
                reply_markup=button,
            )
            ALLY_REMINDER_MSG[p.user_id] = sent.message_id
        except TelegramError:
            pass


# ---------------------------------------------------------------------------
# Night phase
# ---------------------------------------------------------------------------

async def start_night(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if game.pending_alliance_requests:
        await _clear_all_pending_requests(context, game, "\u23f0 This alliance request expired \u2014 night has fallen.")

    for p in game.alive_players():
        p.reset_round_flags()
        PENDING.pop(p.user_id, None)
        SOLDIER_PENDING.pop(p.user_id, None)
        ALLIANCE_MSG.pop(p.user_id, None)

        if game.day_number >= 4 and (game.day_number - 1) % 3 == 0:
            p.bonus_restores = 2

        if p.pending_kidnap_by is not None:
            try:
                await context.bot.send_message(
                    p.user_id,
                    "🌀 You were kidnapped and never escaped \u2014 you're skipped tonight."
                )
            except TelegramError:
                pass
            p.pending_kidnap_by = None
            p.submitted = True
            p.soldiers_submitted = True
            p.night_actions = []
        else:
            p.submitted = False
            p.soldiers_submitted = False
            await send_main_menu(context, game, p)

        if game.get_alliance(p.user_id) and game.is_alliance_head(p.user_id) and not game.is_duel:
            await send_alliance_menu(context, game, p)

    banner = msg.NIGHT_BANNER_DUEL if game.is_duel else msg.NIGHT_BANNER
    bot_username = (await context.bot.get_me()).username

    if not game.is_duel:
        await _send_banner(context, game.chat_id, NIGHT_BANNER_URL, banner)

        alive_list = "\n".join(f"{i+1}. {p.name}" for i, p in enumerate(game.alive_players()))
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("Go to bot", url=f"https://t.me/{bot_username}")]])
        await context.bot.send_message(
            game.chat_id,
            f"*Players alive:*\n{alive_list}\n\n{NIGHT_TIMEOUT_SECONDS // 60} min. left to submit moves.",
            parse_mode="Markdown",
            reply_markup=buttons,
        )
    else:
        await _send_banner(context, game.chat_id, NIGHT_BANNER_URL, msg.NIGHT_BANNER_DUEL)
        await context.bot.send_message(
            game.chat_id,
            f"\u2694\ufe0f The duel continues... Both players check DMs. {NIGHT_TIMEOUT_SECONDS // 60} min. to submit.",
        )

    game.night_job = context.job_queue.run_once(
        night_timeout_callback, NIGHT_TIMEOUT_SECONDS,
        data=game.chat_id, name=f"night_timeout_{game.chat_id}",
    )


async def night_timeout_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    game = GAMES.get(chat_id)
    if not game or game.phase != "night":
        return
    for p in game.alive_players():
        if not p.submitted:
            state = PENDING.pop(p.user_id, {})
            existing_alliance = [a for a in p.night_actions if a["move_id"] in ALLIANCE_MOVES]
            p.night_actions = state.get("moves", []) + existing_alliance
            p.submitted = True
            for act in state.get("moves", []):
                if act["move_id"] in MOVES:
                    p.spend_move(act["move_id"])
        if not p.alliance_submitted and p.alliance_moves_pending:
            a = game.get_alliance(p.user_id)
            for act in p.alliance_moves_pending:
                mid = act["move_id"]
                p.night_actions.append(act)
                if a and mid in ALLIANCE_MOVES and a.move_uses.get(mid) is not None:
                    a.move_uses[mid] = max(0, a.move_uses.get(mid, 0) - 1)
            p.alliance_moves_pending = []
            p.alliance_submitted = True
        if not p.soldiers_submitted:
            state = SOLDIER_PENDING.pop(p.user_id, {})
            p.soldier_deployment = {int(k): v for k, v in state.items() if v not in (None, "idle")}
            p.soldiers_submitted = True
    await do_resolve(game, context)


async def _maybe_resolve(game: Game, context: ContextTypes.DEFAULT_TYPE):
    if all(pl.submitted and pl.soldiers_submitted for pl in game.alive_players()):
        if game.night_job:
            game.night_job.schedule_removal()
            game.night_job = None
        await do_resolve(game, context)


# ---------------------------------------------------------------------------
# Move selection callbacks
# ---------------------------------------------------------------------------

async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    if not game or game.phase != "night":
        await query.answer("No active night phase.")
        return
    p = game.players.get(uid)
    if not p or not p.alive or p.submitted:
        await query.answer("Not available.")
        return

    state = PENDING.setdefault(uid, {"moves": [], "chosen_counts": {}, "view": "main"})
    data = query.data
    await query.answer()

    if data == "donenight":
        existing_alliance = [a for a in p.night_actions if a["move_id"] in ALLIANCE_MOVES]
        p.night_actions = state.get("moves", []) + existing_alliance
        p.submitted = True
        for act in state.get("moves", []):
            if act["move_id"] in MOVES:
                p.spend_move(act["move_id"])
        PENDING.pop(uid, None)
        await query.edit_message_text("🔒 Moves locked in for tonight.")
        await _maybe_resolve(game, context)
        return

    if data.startswith("mv:") and not data.startswith("mv_na:"):
        move_id = data[3:]
        mv = MOVES.get(move_id)
        if not mv:
            return
        if len(state["moves"]) >= MAX_MOVES_PER_NIGHT:
            await query.answer("You've used all your move slots.")
            return
        if any(a["move_id"] == move_id for a in state["moves"]):
            await query.answer("Already picked this move.")
            return
        if p.move_uses.get(move_id, 0) <= 0:
            await query.answer("No uses left for this move.")
            return
        if mv.get("needs_target"):
            state["view"] = "target"
            state["pending_move"] = move_id
            targets = _alive_targets(game, {uid})
            target_buttons = [InlineKeyboardButton(t.name, callback_data=f"tgt:{t.user_id}") for t in targets]
            buttons = _rows(target_buttons, 2)
            buttons.append([InlineKeyboardButton("\u00ab Cancel", callback_data="backmenu")])
            await query.edit_message_text(
                f"{mv['name']}: choose a target.", reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            state["moves"].append({"move_id": move_id})
            state["chosen_counts"][move_id] = state["chosen_counts"].get(move_id, 0) + 1
            await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("tgt:"):
        target_id = int(data[4:])
        move_id = state.get("pending_move")
        mv = MOVES.get(move_id)
        if mv and mv.get("needs_redirect"):
            state["view"] = "redirect"
            state["pending_target"] = target_id
            targets = _alive_targets(game, {uid, target_id})
            target_buttons = [InlineKeyboardButton(t.name, callback_data=f"rdr:{t.user_id}") for t in targets]
            buttons = _rows(target_buttons, 2)
            buttons.append([InlineKeyboardButton("\u00ab Cancel", callback_data="backmenu")])
            await query.edit_message_text(
                f"{mv['name']}: if they attack you tonight, redirect it to whom?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            state["moves"].append({"move_id": move_id, "target_id": target_id})
            state["chosen_counts"][move_id] = state["chosen_counts"].get(move_id, 0) + 1
            state.pop("pending_move", None)
            state["view"] = "main"
            await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("rdr:"):
        redirect_id = int(data[4:])
        move_id = state.pop("pending_move", None)
        target_id = state.pop("pending_target", None)
        state["moves"].append({"move_id": move_id, "target_id": target_id, "redirect_id": redirect_id})
        state["chosen_counts"][move_id] = state["chosen_counts"].get(move_id, 0) + 1
        state["view"] = "main"
        await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("cls:"):
        class_name = data[4:]
        state["view"] = f"class_{class_name}"
        await send_class_menu(context, game, p, class_name, query.message)
        return

    if data == "backmenu":
        state.pop("pending_move", None)
        state.pop("pending_target", None)
        if state.get("view", "").startswith("class_"):
            state["view"] = "main"
            await send_main_menu(context, game, p, edit_message=query.message)
        else:
            state["view"] = "main"
            await send_main_menu(context, game, p, edit_message=query.message)
        return


async def cb_soldiers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    if not game or game.phase != "night":
        await query.answer("No active night phase.")
        return
    p = game.players.get(uid)
    if not p or not p.alive or p.soldiers_submitted:
        await query.answer("Not available.")
        return

    state = SOLDIER_PENDING.setdefault(uid, {})
    data = query.data
    await query.answer()

    if data == "sld_submit":
        p.soldier_deployment = {}
        for idx, dest in list(state.items()):
            idx = int(idx)
            if dest in ("home", "idle", None) or dest is None:
                if dest == "home":
                    p.soldier_deployment[idx] = "home"
            else:
                p.soldier_deployment[idx] = dest
        p.soldiers_submitted = True
        SOLDIER_PENDING.pop(uid, None)
        await query.edit_message_text("🔒 Soldiers locked in for tonight.")
        await _maybe_resolve(game, context)
        return

    if data.startswith("sld_pick:"):
        idx = int(data.split(":", 1)[1])
        if p.soldier_is_forced_rest(idx):
            await query.answer("This soldier is forced to rest tonight.")
            return
        await send_soldier_deploy_menu(query.message, game, p, idx)
        return

    if data == "sld_back":
        await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("sld_home:"):
        idx = int(data.split(":", 1)[1])
        state[idx] = "home"
        await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("sld_rest:"):
        idx = int(data.split(":", 1)[1])
        state[idx] = "idle"
        await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("sld_atk:"):
        parts = data.split(":")
        idx = int(parts[1])
        target_id = int(parts[2])
        state[idx] = target_id
        await send_main_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("sld_na:"):
        suffix = data.split(":", 1)[1]
        if suffix == "cap":
            await query.answer("This target already has the max soldiers assigned.")
        else:
            await query.answer("This soldier isn't available tonight.")
        return

    if data == "bonus_menu":
        if p.bonus_restores <= 0:
            await query.answer("No restores left.")
            return
        avail = [mid for mid in NORMAL_MOVE_IDS if p.move_uses.get(mid, 0) < MOVES[mid]["max_uses"]]
        if not avail:
            await query.answer("All moves already at max.")
            return
        move_buttons = [InlineKeyboardButton(f"\u2728 {MOVES[m]['name']}", callback_data=f"bonus_pick:{m}") for m in avail]
        rows = _rows(move_buttons, 2)
        rows.append([InlineKeyboardButton("\u00ab Back", callback_data="backmenu")])
        await query.edit_message_text(
            f"Choose a move to restore ({p.bonus_restores}/2 remaining):",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("bonus_pick:"):
        move_id = data.split(":", 1)[1]
        if p.bonus_restores <= 0:
            await query.answer("No restores left.")
            return
        mv = MOVES.get(move_id)
        if not mv or p.move_uses.get(move_id, 0) >= mv["max_uses"]:
            await query.answer("Already at max.")
            return
        p.move_uses[move_id] = min(p.move_uses.get(move_id, 0) + 1, mv["max_uses"])
        p.bonus_restores -= 1
        await query.answer(f"Restored {mv['name']}!")
        if p.bonus_restores > 0:
            avail = [mid for mid in NORMAL_MOVE_IDS if p.move_uses.get(mid, 0) < MOVES[mid]["max_uses"]]
            if avail:
                move_buttons = [InlineKeyboardButton(f"\u2728 {MOVES[m]['name']}", callback_data=f"bonus_pick:{m}") for m in avail]
                rows = _rows(move_buttons, 2)
                rows.append([InlineKeyboardButton("\u00ab Back", callback_data="backmenu")])
                await query.edit_message_text(
                    f"Choose another move ({p.bonus_restores}/2 remaining):",
                    reply_markup=InlineKeyboardMarkup(rows),
                )
                return
        await send_main_menu(context, game, p, edit_message=query.message)
        return


# ---------------------------------------------------------------------------
# Alliance menu callbacks
# ---------------------------------------------------------------------------

async def cb_alliance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    data = query.data

    if data == "ally_break":
        await query.answer()
        if not game or game.phase != "day":
            return
        p = game.players.get(uid)
        if not p or not p.alive:
            return
        await _break_alliance(context, game, uid)
        await query.edit_message_text("Alliance broken. You're free to act against them now.")
        await context.bot.send_message(game.chat_id, f"💔 {p.name} has shattered a pact.")
        HEAL_DATA.pop(uid, None)
        ALLIANCE_MSG.pop(uid, None)
        return

    if not game or game.phase != "night":
        await query.answer("No active night phase.")
        return
    p = game.players.get(uid)
    if not p or not p.alive:
        return

    await query.answer()

    if data.startswith("ally_move:"):
        move_id = data.split(":", 1)[1]
        a = game.get_alliance(uid)
        if not a or a.head_id != uid:
            return
        mv = ALLIANCE_MOVES.get(move_id)
        if not mv:
            return
        if p.alliance_submitted:
            await query.answer("Already submitted.")
            return
        if move_id == "bhoomi_domain":
            if a.bhoomi_forced_rest:
                await query.answer("Boundless Tower Domain is resting tonight.")
                return
            if any(m["move_id"] == move_id for m in p.alliance_moves_pending):
                await query.answer("Already active.")
                return
            p.alliance_moves_pending.append({"move_id": move_id})
            await send_alliance_menu(context, game, p, edit_message=query.message)
            return
        if any(m["move_id"] == move_id for m in p.alliance_moves_pending):
            await query.answer("Already staged.")
            return
        if mv.get("needs_target"):
            if a.move_uses.get(move_id, 0) <= 0:
                await query.answer("No uses left.")
                return
            PENDING.setdefault(uid, {})["pending_ally_move"] = move_id
            targets = _alive_targets(game, {uid})
            target_buttons = [InlineKeyboardButton(t.name, callback_data=f"ally_tgt:{t.user_id}") for t in targets]
            buttons = _rows(target_buttons, 2)
            buttons.append([InlineKeyboardButton("\u00ab Back", callback_data="ally_back")])
            await query.edit_message_text(
                f"{mv['name']}: choose a target.", reply_markup=InlineKeyboardMarkup(buttons)
            )
            return
        p.alliance_moves_pending.append({"move_id": move_id})
        await send_alliance_menu(context, game, p, edit_message=query.message)
        return

    if data.startswith("ally_tgt:"):
        target_id = int(data.split(":", 1)[1])
        pending = PENDING.get(uid, {})
        move_id = pending.pop("pending_ally_move", None)
        if not move_id:
            return
        p = game.players.get(uid)
        if not p or p.alliance_submitted:
            return
        p.alliance_moves_pending.append({"move_id": move_id, "target_id": target_id})
        await send_alliance_menu(context, game, p, edit_message=query.message)
        return

    if data == "ally_submit":
        if not p.alliance_moves_pending or p.alliance_submitted:
            return
        a = game.get_alliance(uid)
        for act in p.alliance_moves_pending:
            mid = act["move_id"]
            p.night_actions.append(act)
            if a and mid in ALLIANCE_MOVES and a.move_uses.get(mid) is not None:
                a.move_uses[mid] = max(0, a.move_uses.get(mid, 0) - 1)
        p.alliance_moves_pending = []
        p.alliance_submitted = True
        await query.edit_message_text("🔒 Alliance moves locked in.")
        await send_alliance_menu(context, game, p, edit_message=query.message)
        return

    if data == "ally_back":
        await send_alliance_menu(context, game, p, edit_message=query.message)
        return

    if data == "ally_na":
        await query.answer("Not available.")
        return


async def _break_alliance(context, game, uid):
    p = game.players.get(uid)
    if not p or p.alliance_id is None:
        return
    a = game.alliances.get(p.alliance_id)
    other_id = a.other_member(uid) if a else None
    if a:
        del game.alliances[a.id]
    p.alliance_id = None
    if other_id:
        other = game.players.get(other_id)
        if other:
            other.alliance_id = None
        try:
            await context.bot.send_message(other_id, f"💔 {p.name} has broken your alliance.")
        except TelegramError:
            pass


# ---------------------------------------------------------------------------
# Resolution + day phase
# ---------------------------------------------------------------------------

async def do_resolve(game: Game, context: ContextTypes.DEFAULT_TYPE):
    name_fn = _name_fn(game)
    phase1 = resolve_night_phase1(game, name_fn)

    for uid, lines in phase1["dm_data"].items():
        try:
            await context.bot.send_message(uid, "\n\n".join(lines), parse_mode="Markdown")
        except TelegramError:
            pass

    eligible_heal = []
    for p in game.alive_players():
        night_move_count = len([a for a in p.night_actions if a["move_id"] in MOVES])
        if night_move_count >= MAX_MOVES_PER_NIGHT:
            continue
        if p.move_uses.get("yadav_milk", 0) <= 0:
            continue
        raw = phase1["damage_map"].get(p.user_id, 0)
        block = phase1["defend_block"].get(p.user_id, 0)
        net = raw - block
        if net <= 0:
            continue
        eligible_heal.append(p.user_id)

    if eligible_heal:
        chat_id = game.chat_id
        HEAL_DATA[chat_id] = {"phase1": phase1, "eligible": set(eligible_heal)}
        HEAL_ACCEPTED.clear()
        HEAL_DECLINED.clear()

        for uid in eligible_heal:
            p = game.players.get(uid)
            if not p:
                continue
            raw = phase1["damage_map"].get(uid, 0)
            block = phase1["defend_block"].get(uid, 0)
            net = raw - block
            slot_used = len([a for a in p.night_actions if a["move_id"] in MOVES])
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2705 Heal (+25 HP)", callback_data="heal_yes"),
                 InlineKeyboardButton("\u274c Skip", callback_data="heal_no")],
            ])
            try:
                await context.bot.send_message(
                    uid,
                    f"💧 Your tower took *{net}* net damage. You have 50s to use Magic Milk and restore 25 HP. "
                    f"(Slot left: {MAX_MOVES_PER_NIGHT - slot_used}/{MAX_MOVES_PER_NIGHT})",
                    reply_markup=buttons,
                    parse_mode="Markdown",
                )
            except TelegramError:
                pass

        game.heal_job = context.job_queue.run_once(
            heal_timeout_callback, 50, data=game.chat_id, name=f"heal_timeout_{game.chat_id}",
        )
    else:
        await _finish_resolution(game, context, phase1, set())


async def heal_timeout_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    game = GAMES.get(chat_id)
    if not game:
        return
    phase1 = HEAL_DATA.pop(chat_id, {}).get("phase1")
    if not phase1:
        return
    await _finish_resolution(game, context, phase1, set(HEAL_ACCEPTED))


async def _finish_resolution(game, context, phase1, heal_accepted):
    name_fn = _name_fn(game)

    report, eliminated, winner = resolve_night_phase2(game, name_fn, phase1, heal_accepted)

    for uid in eliminated:
        elim_p = game.players.get(uid)
        if elim_p:
            try:
                await context.bot.send_message(
                    uid,
                    f"\U0001f480 Your tower has collapsed on Day {game.day_number}. You are out of the game."
                )
            except TelegramError:
                pass
            if elim_p.alliance_id is not None:
                a = game.alliances.get(elim_p.alliance_id)
                if a:
                    survivor_id = a.other_member(uid)
                    if survivor_id:
                        survivor = game.players.get(survivor_id)
                        if survivor:
                            survivor.alliance_id = None
                        try:
                            await context.bot.send_message(
                                survivor_id,
                                f"💔 Your ally {elim_p.name} has fallen. Your pact is shattered."
                            )
                        except TelegramError:
                            pass
                    del game.alliances[a.id]
                elim_p.alliance_id = None

    for p in game.players.values():
        if not p.alive:
            continue
        try:
            bar = _hp_bar(p.hp)
            await context.bot.send_message(
                p.user_id,
                f"\u2764\ufe0f *Your tower HP:* {p.hp}/100\n{bar}",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass

    game.phase = "day"
    game.day_number += 1

    banner_caption = f"{msg.DAY_BANNER}\n*Day {game.day_number - 1}*"
    await _send_banner(context, game.chat_id, DAY_BANNER_URL, banner_caption)

    if report:
        for line in report:
            if isinstance(line, dict) and "__banner__" in line:
                url = EVENT_BANNERS.get(line["__banner__"])
                if url:
                    await _send_banner(context, game.chat_id, url, "")
            else:
                try:
                    await context.bot.send_message(game.chat_id, line, parse_mode="Markdown")
                except TelegramError:
                    pass
    else:
        await context.bot.send_message(game.chat_id, "🎭 What a surprise! Everyone made it through the night.")

    closing = msg.player_list_text(game) + f"\n\n💬 You have {DAY_CHAT_SECONDS} seconds to talk before the next night falls."
    await context.bot.send_message(game.chat_id, closing, parse_mode="Markdown")

    for p in game.alive_players():
        if p.pending_kidnap_by is not None:
            kidnapper = game.players.get(p.pending_kidnap_by)
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("\u26d3\ufe0f Break Free", callback_data="breakfree_open")]])
            try:
                await context.bot.send_message(
                    p.user_id,
                    f"🌀 You've been kidnapped by {kidnapper.name if kidnapper else 'someone'}! "
                    "Sacrifice 1 other move to escape, or do nothing and skip next night.",
                    reply_markup=btn,
                )
            except TelegramError:
                pass

    if winner:
        game.phase = "ended"
        winner_names = ", ".join(w.name for w in winner)
        await _send_banner(context, game.chat_id, EVENT_BANNERS.get("winner"), "")
        lines = [
            "\U0001F3C6\U0001F3C6\U0001F3C6\U0001F3C6",
            f"*{winner_names} wins Tower Siege!*",
            f"Days fought: {game.day_number - 1}",
            "",
            "*Final standings:*",
        ]
        for uid in game.join_order:
            p = game.players.get(uid)
            if not p:
                continue
            if p.alive:
                lines.append(f"\U0001F3C6 {p.name} \u2014 victorious!")
            else:
                lines.append(f"\u2620\ufe0f {p.name} \u2014 lost the war")
        lines.extend(["", "Thanks for playing the game \u2014 Hopeless fellow", "\U0001F3C6\U0001F3C6\U0001F3C6\U0001F3C6"])
        await context.bot.send_message(game.chat_id, "\n".join(lines), parse_mode="Markdown")
        _cleanup_game(game)
        return

    if game.day_number >= ALLIANCES_UNLOCK_DAY and not game.is_duel:
        if game.day_number == ALLIANCES_UNLOCK_DAY:
            await context.bot.send_message(
                game.chat_id,
                f"🤝 *Day {ALLIANCES_UNLOCK_DAY} \u2014 Alliances are now unlocked!*\n\n"
                "Surviving players can request ONE alliance in their DMs.",
                parse_mode="Markdown",
            )
        await _refresh_alliance_reminders(game, context)

    game.day_job = context.job_queue.run_once(
        day_end_callback, DAY_CHAT_SECONDS, data=game.chat_id, name=f"day_end_{game.chat_id}",
    )


async def day_end_callback(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    game = GAMES.get(chat_id)
    if not game or game.phase != "day":
        return
    game.phase = "night"
    await start_night(game, context)


def _cleanup_game(game):
    for uid in list(game.players.keys()):
        USER_GAME.pop(uid, None)
        PENDING.pop(uid, None)
        SOLDIER_PENDING.pop(uid, None)
        SAC_PENDING.pop(uid, None)
        ALLIANCE_MSG.pop(uid, None)
        ALLY_REMINDER_MSG.pop(uid, None)
    HEAL_DATA.pop(game.chat_id, None)
    HEAL_ACCEPTED.clear()
    HEAL_DECLINED.clear()


# ---------------------------------------------------------------------------
# Heal callbacks
# ---------------------------------------------------------------------------

async def cb_heal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    if not game:
        return
    chat_id = game.chat_id
    heal_info = HEAL_DATA.get(chat_id)
    if not heal_info or uid not in heal_info.get("eligible", set()):
        await query.answer("Heal window expired or not available.")
        return

    data = query.data
    await query.answer()

    if data == "heal_yes":
        HEAL_ACCEPTED.add(uid)
        HEAL_DECLINED.discard(uid)
        p = game.players.get(uid)
        if p:
            p.move_uses["yadav_milk"] = max(0, p.move_uses["yadav_milk"] - 1)
        await query.edit_message_text("\u2705 You used Magic Milk to heal for 25 HP!")
    elif data == "heal_no":
        HEAL_DECLINED.add(uid)
        HEAL_ACCEPTED.discard(uid)
        await query.edit_message_text("\u274c You chose not to heal.")

    eligible = heal_info.get("eligible", set())
    responded = HEAL_ACCEPTED | HEAL_DECLINED
    if responded >= eligible:
        if game.heal_job:
            game.heal_job.schedule_removal()
            game.heal_job = None
        phase1 = HEAL_DATA.pop(chat_id, {}).get("phase1")
        if phase1:
            await _finish_resolution(game, context, phase1, set(HEAL_ACCEPTED))


# ---------------------------------------------------------------------------
# Alliance request callbacks
# ---------------------------------------------------------------------------

async def _open_request_picker(game, uid):
    """Shared eligibility check for /ally and the reminder button. Returns
    (error_text_or_None, target_list)."""
    if not game or game.phase != "day":
        return "You're not in an active game.", []
    if game.is_duel:
        return "Duels have no alliances.", []
    if game.day_number < ALLIANCES_UNLOCK_DAY:
        return f"Alliances unlock on Day {ALLIANCES_UNLOCK_DAY}.", []
    p = game.players.get(uid)
    if not p or not p.alive:
        return "Not available.", []
    if p.has_ever_been_allied:
        return "You've already used your alliance request. You can only accept invitations now.", []
    if len(game.alive_players()) <= 2:
        return "Not enough players to form an alliance.", []
    targets = [t for t in game.alive_players() if t.user_id != uid]
    if not targets:
        return "No eligible players to ally with.", []
    return None, targets


async def cmd_ally(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("DM me this command privately.")
        return
    uid = update.effective_user.id
    game = GAMES.get(USER_GAME.get(uid))
    err, targets = await _open_request_picker(game, uid)
    if err:
        await update.message.reply_text(err)
        return
    target_buttons = [InlineKeyboardButton(t.name, callback_data=f"ally_pick:{t.user_id}") for t in targets]
    await update.message.reply_text(
        "Request an alliance with whom?",
        reply_markup=InlineKeyboardMarkup(_rows(target_buttons, 2)),
    )


async def cb_ally_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    await query.answer()
    err, targets = await _open_request_picker(game, uid)
    if err:
        await query.edit_message_text(err)
        return
    ALLY_REMINDER_MSG.pop(uid, None)
    target_buttons = [InlineKeyboardButton(t.name, callback_data=f"ally_pick:{t.user_id}") for t in targets]
    await query.edit_message_text("Request alliance with whom?", reply_markup=InlineKeyboardMarkup(_rows(target_buttons, 2)))


async def cb_ally_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    await query.answer()
    if not game or game.phase != "day":
        return
    p = game.players.get(uid)
    target_id = int(query.data.split(":", 1)[1])
    target = game.players.get(target_id)
    if not p or not target:
        await query.edit_message_text("Invalid request.")
        return
    if p.has_ever_been_allied:
        await query.edit_message_text("You've already used your alliance request.")
        return
    if not target.alive:
        await query.edit_message_text("That player is no longer alive.")
        return

    sent = await query.edit_message_text(f"Alliance request sent to {target.name}.")
    buttons = [
        [InlineKeyboardButton("\u2705 Accept", callback_data=f"ally_accept:{uid}"),
         InlineKeyboardButton("\u274c Decline", callback_data=f"ally_decline:{uid}")],
    ]
    try:
        request_msg = await context.bot.send_message(
            target_id,
            f"🤝 {p.name} has requested an alliance with you.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except TelegramError:
        return

    entries = game.pending_alliance_requests.setdefault(target_id, [])
    entries.append({
        "requester_id": uid,
        "request_msg_id": request_msg.message_id,
        "sent_msg_id": sent.message_id if sent else None,
    })


async def cb_ally_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    await query.answer()
    if not game or game.phase != "day":
        return
    accept = query.data.startswith("ally_accept:")
    requester_id = int(query.data.split(":", 1)[1])
    requester = game.players.get(requester_id)
    target = game.players.get(uid)
    if not requester or not target:
        await query.edit_message_text("Invalid request.")
        return

    entries = game.pending_alliance_requests.get(uid, [])
    this_entry = next((e for e in entries if e["requester_id"] == requester_id), None)
    if not this_entry:
        await query.edit_message_text("This request is no longer valid.")
        return

    other_entries = [e for e in entries if e is not this_entry]
    game.pending_alliance_requests[uid] = []

    if not target.alive:
        await query.edit_message_text("You are no longer alive.")
        return
    if accept and not requester.alive:
        await query.edit_message_text("That player is no longer alive.")
        return

    if accept:
        if other_entries:
            await _expire_request_messages(
                context, game, uid, other_entries,
                "\u274c No longer available \u2014 they formed a pact with someone else.",
            )

        for member_id in (requester_id, uid):
            old_a = game.get_alliance(member_id)
            if old_a:
                other = old_a.other_member(member_id)
                if other:
                    game.players[other].alliance_id = None
                del game.alliances[old_a.id]
                if other:
                    try:
                        await context.bot.send_message(other, f"💔 Your alliance broke when {game.players[member_id].name} formed a new pact.")
                    except TelegramError:
                        pass

        head_id = requester_id if requester.hp >= target.hp else uid
        alliance = Alliance([requester_id, uid], head_id)
        game.alliances[alliance.id] = alliance
        requester.alliance_id = alliance.id
        requester.has_ever_been_allied = True
        target.alliance_id = alliance.id
        target.has_ever_been_allied = True
        break_btn = InlineKeyboardMarkup([[InlineKeyboardButton("💔 Break Alliance", callback_data="ally_break")]])
        await query.edit_message_text(
            f"Alliance formed with {requester.name}! {game.players[head_id].name} leads.",
            reply_markup=break_btn,
        )
        try:
            await context.bot.send_message(requester_id, f"🤝 {target.name} accepted your alliance!", reply_markup=break_btn)
        except TelegramError:
            pass
        await context.bot.send_message(
            game.chat_id,
            f"🤝 A pact has been sealed between {requester.name} and {target.name}.",
        )
    else:
        await query.edit_message_text(f"You declined {requester.name}'s alliance request.")
        try:
            await context.bot.send_message(requester_id, f"\u274c {target.name} declined your alliance request.")
        except TelegramError:
            pass
        if other_entries:
            game.pending_alliance_requests[uid] = other_entries


async def cmd_breakalliance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("DM me this command.")
        return
    uid = update.effective_user.id
    game = GAMES.get(USER_GAME.get(uid))
    if not game:
        return
    p = game.players.get(uid)
    if not p or p.alliance_id is None:
        await update.message.reply_text("You're not in an alliance.")
        return
    await _break_alliance(context, game, uid)
    await update.message.reply_text("Alliance broken.")
    await context.bot.send_message(game.chat_id, f"💔 {p.name} has shattered a pact.")


# ---------------------------------------------------------------------------
# Duel callbacks
# ---------------------------------------------------------------------------

async def cb_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    data = query.data
    await query.answer()

    if data.startswith("duel_accept:"):
        parts = data.split(":")
        chat_id = int(parts[1])
        init_id = int(parts[2])
        target_id = int(parts[3])
        if uid != target_id:
            return
        game = GAMES.get(chat_id)
        if not game or not game.is_duel or game.phase != "lobby":
            await query.edit_message_text("This duel is no longer available.")
            return

        if not game.players.get(target_id):
            p = Player(target_id, query.from_user.first_name or "Player 2")
            game.players[target_id] = p
            game.join_order.append(target_id)
            USER_GAME[target_id] = chat_id

        for j in context.job_queue.get_jobs_by_name(f"duel_timeout_{chat_id}"):
            j.schedule_removal()

        await query.edit_message_text("You accepted the duel! The battle begins...")

        game.phase = "night"
        game.day_number = 1
        try:
            await context.bot.send_message(
                chat_id,
                f"\u2694\ufe0f *TOWER SIEGE DUEL* \u2694\ufe0f\n\n"
                f"{game.players[init_id].name} vs {game.players[target_id].name}\n"
                f"The battle begins tonight!",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass
        try:
            await context.bot.send_message(
                init_id,
                f"\u2694\ufe0f *{game.players[target_id].name}* accepted your duel challenge! The battle begins tonight!",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass
        await start_night(game, context)
        return

    elif data.startswith("duel_decline:"):
        parts = data.split(":")
        chat_id = int(parts[1])
        init_id = int(parts[2])
        target_id = int(parts[3])
        game = GAMES.get(chat_id)
        if game:
            del GAMES[chat_id]
        await query.edit_message_text("Duel declined. Challenge cancelled.")
        try:
            await context.bot.send_message(
                init_id,
                f"\u274c Your duel challenge was declined by the opponent.",
            )
        except TelegramError:
            pass
        return


# ---------------------------------------------------------------------------
# Kidnap escape
# ---------------------------------------------------------------------------

async def cb_breakfree_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    await query.answer()
    if not game:
        return
    p = game.players.get(uid)
    if not p or p.pending_kidnap_by is None:
        await query.edit_message_text("You haven't been kidnapped.")
        return
    if p.move_uses.get("almighty_mulla", 0) <= 0:
        await query.edit_message_text("No Almighty Mulla left \u2014 no escape.")
        return
    sacrificeable = [mid for mid in NORMAL_MOVE_IDS if mid != "almighty_mulla" and p.move_uses.get(mid, 0) > 0]
    if len(sacrificeable) < 1:
        await query.edit_message_text("You need 1 move to sacrifice \u2014 not enough left.")
        return
    SAC_PENDING[uid] = {"chosen": []}
    await _send_sacrifice_menu(query.message, p, uid, edit=True)


async def cmd_breakfree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("DM me this command.")
        return
    uid = update.effective_user.id
    game = GAMES.get(USER_GAME.get(uid))
    if not game:
        return
    p = game.players.get(uid)
    if not p or p.pending_kidnap_by is None:
        await update.message.reply_text("You haven't been kidnapped.")
        return
    if p.move_uses.get("almighty_mulla", 0) <= 0:
        await update.message.reply_text("No Almighty Mulla left.")
        return
    sacrificeable = [mid for mid in NORMAL_MOVE_IDS if mid != "almighty_mulla" and p.move_uses.get(mid, 0) > 0]
    if len(sacrificeable) < 1:
        await update.message.reply_text("You need 1 move to sacrifice.")
        return
    SAC_PENDING[uid] = {"chosen": []}
    await _send_sacrifice_menu(update.message, p, uid)


async def _send_sacrifice_menu(message_or_query, p, uid, edit=False):
    state = SAC_PENDING[uid]
    sacrificeable = [mid for mid in NORMAL_MOVE_IDS if mid != "almighty_mulla" and p.move_uses.get(mid, 0) > 0 and mid not in state["chosen"]]
    move_buttons = [InlineKeyboardButton(MOVES[m]["name"], callback_data=f"sac:{m}") for m in sacrificeable]
    buttons = _rows(move_buttons, 2)
    if len(state["chosen"]) == 1:
        buttons = [[InlineKeyboardButton("\u2705 Confirm escape", callback_data="sac_confirm")]]
    text = f"Sacrifice 1 move to break free. Chosen: {', '.join(MOVES[m]['name'] for m in state['chosen']) or 'none'}"
    if edit:
        await message_or_query.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_sacrifice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    game = GAMES.get(USER_GAME.get(uid))
    await query.answer()
    if not game:
        return
    p = game.players.get(uid)
    state = SAC_PENDING.get(uid)
    if not p or not state:
        return

    if query.data == "sac_confirm":
        for mid in state["chosen"]:
            p.move_uses[mid] = max(0, p.move_uses[mid] - 1)
        p.move_uses["almighty_mulla"] = max(0, p.move_uses["almighty_mulla"] - 1)
        kidnapper_id = p.pending_kidnap_by
        p.pending_kidnap_by = None
        SAC_PENDING.pop(uid, None)
        await query.edit_message_text("\u26d3\ufe0f You broke every chain and returned to your tower before dawn.")
        if kidnapper_id:
            await context.bot.send_message(
                kidnapper_id,
                f"\u26d3\ufe0f {p.name} escaped from your tower using a Healer's Almighty Mulla!",
            )
        await context.bot.send_message(
            game.chat_id,
            f"\u26d3\ufe0f {p.name} broke every chain and returned to their tower before dawn, using a Healer's Almighty Mulla.",
        )
        return

    move_id = query.data.split(":", 1)[1]
    if move_id not in state["chosen"]:
        state["chosen"].append(move_id)
    await _send_sacrifice_menu(query.message, p, uid, edit=True)


# ---------------------------------------------------------------------------
# Delete messages during night
# ---------------------------------------------------------------------------

async def delete_during_night(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    game = GAMES.get(chat.id)
    if not game or game.phase != "night":
        return
    try:
        await update.effective_message.delete()
    except TelegramError:
        pass


# ---------------------------------------------------------------------------
# Lobby keyboard
# ---------------------------------------------------------------------------

def _lobby_keyboard(game: Game, bot_username: str, chat_id: int):
    if len(game.players) >= MAX_PLAYERS:
        return None
    url = f"https://t.me/{bot_username}?start={chat_id}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏀 Join", url=url)]])


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

async def _post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("newgame", "Start a new lobby in this group"),
        BotCommand("invitetotowerbattle", "Challenge someone to a 1v1 tower duel"),
        BotCommand("join", "Join the open lobby (or tap the button)"),
        BotCommand("startgame", "Host: begin the siege"),
        BotCommand("cancelgame", "Host: cancel the current game"),
        BotCommand("info", "See what every move does"),
    ])


def main():
    token = os.environ.get("TOWER_SIEGE_TOKEN")
    if not token:
        raise SystemExit("Set TOWER_SIEGE_TOKEN environment variable to your bot token from @BotFather.")

    app = Application.builder().token(token).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newgame", cmd_newgame))
    app.add_handler(CommandHandler("invitetotowerbattle", cmd_invite))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("startgame", cmd_startgame))
    app.add_handler(CommandHandler("cancelgame", cmd_cancelgame))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("ally", cmd_ally))
    app.add_handler(CommandHandler("breakalliance", cmd_breakalliance))
    app.add_handler(CommandHandler("breakfree", cmd_breakfree))

    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=r"^(mv:|tgt:|rdr:|cls:|donenight|backmenu|mv_na:)"))
    app.add_handler(CallbackQueryHandler(cb_soldiers, pattern=r"^(sld_|bonus_)"))
    app.add_handler(CallbackQueryHandler(cb_alliance, pattern=r"^ally_(move|tgt|submit|break|back|na)"))
    app.add_handler(CallbackQueryHandler(cb_heal, pattern=r"^(heal_yes|heal_no)$"))
    app.add_handler(CallbackQueryHandler(cb_ally_open, pattern=r"^ally_open$"))
    app.add_handler(CallbackQueryHandler(cb_ally_pick, pattern=r"^ally_pick:"))
    app.add_handler(CallbackQueryHandler(cb_ally_response, pattern=r"^ally_(accept|decline):"))
    app.add_handler(CallbackQueryHandler(cb_info, pattern=r"^info_"))
    app.add_handler(CallbackQueryHandler(cb_duel, pattern=r"^duel_"))
    app.add_handler(CallbackQueryHandler(cb_breakfree_open, pattern=r"^breakfree_open$"))
    app.add_handler(CallbackQueryHandler(cb_sacrifice, pattern=r"^(sac:|sac_confirm)"))

    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, delete_during_night))

    log.info("Tower Siege bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
