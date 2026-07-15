"""
Tower Siege — move definitions and constants.
"""

MIN_PLAYERS_DUEL = 2
MIN_PLAYERS = 3
MAX_PLAYERS = 12
MAX_HP = 100
FLOOR_THRESHOLDS = [70, 40, 0]
DAY_CHAT_SECONDS = 60
NIGHT_TIMEOUT_SECONDS = 90
ALLIANCES_UNLOCK_DAY = 3
MAX_MOVES_PER_NIGHT = 3

# --- Soldier rest system ---
# 5 soldiers. Each soldier tracks work/rest streaks.
# - Deploying = work day (streak++)
# - Not deploying = rest day if soldier has worked before
# - After 2 consecutive work days, forced rest next night
# - After rest streak >= work streak, both reset, soldier fully refreshed
# - Max 3 soldiers on defense, max 2 attacking a single enemy per night
SOLDIER_COUNT = 5
SOLDIER_DAMAGE = 14
SOLDIER_DEFENSE = 8
SOLDIER_DAMAGE_BOOSTED = 24
SOLDIER_DEFENSE_BOOSTED = 12
PREEMPTIVE_MILK_ABSORB = 35
MAX_SOLDIER_DEFENSE = 3
MAX_SOLDIER_PER_TARGET = 2

NIGHT_BANNER_URL = "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExOXd5dzcyOGpyMDNpbXh3Y3BieWsyZzJuM3NiOXR5Y2NzZG01eDl2NSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/XFiImydWEVhNC/giphy.gif"
DAY_BANNER_URL = "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExZ2pqc3F5bnd1MXF6N2xsM21jMWJubzZ5ZDQzc2lkbXNpZjl0ZGlxNiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/3QTDMmH15UvGE/giphy.gif"

MOVES = {
    "pandey_slash": {
        "name": "Nirvana Sword Slash", "emoji": "🗡️", "type": "attack", "damage": 50, "max_uses": 1,
        "narrate": True, "needs_target": True, "class": "Knight",
        "flavor": "{attacker} the Knight carved through {target}'s tower with a devastating {move}.",
    },
    "chandal_eyes": {
        "name": "Chandal Eyes", "emoji": "👁️", "type": "redirect", "max_uses": 1,
        "narrate": True, "needs_target": True, "needs_redirect": True, "class": "Mage",
        "flavor": "{target} was hypnotized by {attacker} the Mage — their strike landed on {redirect}'s tower instead.",
    },
    "bengali_shield": {
        "name": "Holy Shield", "emoji": "🛡️", "type": "shield", "max_uses": 1,
        "narrate": True, "needs_target": False, "class": "Mage",
        "flavor": "A wall of light rose around {attacker}'s tower. Nothing got through tonight.",
    },
    "yadav_milk": {
        "name": "Magic Milk", "emoji": "🥛", "type": "heal", "heal": 25, "max_uses": 2,
        "narrate": False, "needs_target": False, "class": "Healer",
    },
    "jharkhand_seal": {
        "name": "Judgement Seal", "emoji": "🔒", "type": "seal", "max_uses": 1,
        "narrate": True, "needs_target": True, "class": "Mage",
        "flavor": "{target} awoke unable to move a muscle, sealed by {attacker} in the dead of night.",
    },
    "rajpoot_mirror": {
        "name": "Divine Slash Reflection", "emoji": "🪞", "type": "counter_pandey", "max_uses": 1,
        "narrate": True, "needs_target": False, "class": "Mage",
        "flavor": "{target} the Knight struck {attacker}'s tower with Nirvana Sword Slash, but the Mage's Divine Slash Reflection hurled it back, wounding them both.",
    },
    "chamar_teleporter": {
        "name": "Dino Dimension", "emoji": "🦕", "type": "kidnap", "max_uses": 1,
        "narrate": True, "needs_target": True, "class": "Knight",
        "flavor": "{target} has vanished from their tower. {attacker} the Knight is behind it.",
    },
    "almighty_mulla": {
        "name": "Almighty Mulla", "emoji": "🙏", "type": "escape", "max_uses": 1,
        "narrate": True, "needs_target": False, "reactive_only": True, "class": "Healer",
        "flavor": "{target} broke every chain and returned to their tower before dawn, using a Healer's Almighty Mulla, though it cost them dearly.",
    },
    "super_soldier_serum": {
        "name": "Super Soldier Serum", "emoji": "💉", "type": "soldier_boost", "max_uses": 1,
        "narrate": True, "needs_target": False, "class": "Healer",
        "flavor": "{attacker} injected a Super Soldier Serum, empowering their troops for the night.",
    },
}

ALLIANCE_MOVES = {
    "seelampur_strike": {
        "name": "Seelampur Strike", "emoji": "💥", "type": "alliance_attack", "damage": 100, "max_uses": 1,
        "narrate": True, "needs_target": True,
        "flavor": "The alliance struck as one. {target}'s tower shook to its foundation under a single, devastating blow.",
    },
    "zoya_spear": {
        "name": "Ice Spear", "emoji": "❄️", "type": "alliance_freeze", "max_uses": 1,
        "narrate": True, "needs_target": False,
        "flavor": "A bitter frost swept the land. Every unallied king found themselves frozen in place tonight.",
    },
    "bhoomi_domain": {
        "name": "Boundless Tower Domain", "emoji": "🌿", "type": "alliance_ward", "max_uses": None,
        "narrate": True, "needs_target": False,
        "flavor": "An unseen ward settled over {attacker}'s alliance. No rival pact could touch them tonight.",
    },
}

EVENT_BANNERS = {
    "seelampur_strike_hit": "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExZmdqYmJqMzZqZzhqeDE2ZmZ4Y2g1M2gxbTF4MzF2Ymd0dTNwbGs5MiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/YgM0iEEUr05NLaR0XO/giphy.gif",
    "seelampur_strike_blocked": "https://i.pinimg.com/originals/64/3c/59/643c597dd50c5a8825a67836fc016719.gif",
    "zoya_spear": "https://i.pinimg.com/originals/d7/fd/6b/d7fd6b15109721cadac2e282abe80928.gif",
    "chandal_eyes_redirect": "https://i.pinimg.com/originals/40/36/e0/4036e0c02a3b933933e930d7df98412f.gif",
    "pandey_slash_mirror": "https://i.pinimg.com/originals/7e/91/5f/7e915f0c04c6e07c62e790a719f0cea1.gif",
    "pandey_slash_shield": "https://i.pinimg.com/originals/16/8a/63/168a63c0c88da79cec0556bd110c972f.gif",
    "chamar_teleporter": "https://i.pinimg.com/originals/93/fb/b1/93fbb11674fb5972c69a6bff4ff9ca36.gif",
    "pandey_slash_hit": "https://media1.tenor.com/m/0IHcSipTVcsAAAAd/solo-leveling-liu-zhigang.gif",
    "winner": "https://i.pinimg.com/originals/64/3c/59/643c597dd50c5a8825a67836fc016719.gif",
}

NORMAL_MOVE_IDS = [m for m, d in MOVES.items() if not d.get("reactive_only")]

CLASS_MOVES = {
    "Knight": ["pandey_slash", "chamar_teleporter"],
    "Mage": ["bengali_shield", "jharkhand_seal", "chandal_eyes", "rajpoot_mirror"],
    "Healer": ["yadav_milk", "super_soldier_serum"],
}
