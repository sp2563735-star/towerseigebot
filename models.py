"""Tower Siege — game state data model."""

from config import MOVES, ALLIANCE_MOVES, MAX_HP, SOLDIER_COUNT, SOLDIER_DAMAGE, SOLDIER_DEFENSE, MAX_SOLDIER_DEFENSE, MAX_SOLDIER_PER_TARGET


class Player:
    def __init__(self, user_id: int, name: str):
        self.user_id = user_id
        self.name = name
        self.hp = MAX_HP
        self.alive = True

        self.move_uses = {mid: m["max_uses"] for mid, m in MOVES.items()}
        self.floor_broken = [False, False, False]

        self.alliance_id = None
        self.has_ever_been_allied = False

        self.pending_kidnap_by = None

        self.night_actions = []
        self.submitted = False
        self.alliance_moves_pending = []
        self.alliance_submitted = False

        # per-round resolved flags
        self.shield_active = False
        self.mirror_active = False
        self.sealed = False   # Silencer Seal — blocks normal moves ONLY
        self.frozen = False   # Ice Spear — blocks normal moves AND soldiers
        self.soldier_boosted = False  # Super Soldier Serum active this round

        # --- soldier rest system ---
        # work_streak[i] = consecutive nights soldier i has been deployed
        # rest_streak[i] = consecutive nights soldier i has been idle after working
        # Soldier is available when work_streak[i] == 0
        # After deployment: work_streak[i]++, rest_streak[i] = 0
        # After idle (if work_streak[i] > 0): rest_streak[i]++
        # If rest_streak[i] >= work_streak[i] → both reset to 0
        # If work_streak[i] >= 2 → forced rest (can't deploy, must idle)
        self.soldier_work_streak = [0] * SOLDIER_COUNT
        self.soldier_rest_streak = [0] * SOLDIER_COUNT
        self.soldier_deployment = {}
        self.soldiers_submitted = False
        self.bonus_restores = 0

    def reset_round_flags(self):
        self.shield_active = False
        self.mirror_active = False
        self.sealed = False
        self.frozen = False
        self.soldier_boosted = False
        self.night_actions = []
        self.submitted = False
        self.alliance_moves_pending = []
        self.alliance_submitted = False
        self.soldier_deployment = {}
        self.soldiers_submitted = False
        self.bonus_restores = 0

    def available_soldier_indices(self):
        """Returns list of soldier indices that can be deployed tonight."""
        available = []
        for i in range(SOLDIER_COUNT):
            if self.soldier_work_streak[i] == 0:
                available.append(i)
        return available

    def deploy_soldier(self, idx):
        """Called after a soldier is deployed (attack or defense)."""
        self.soldier_work_streak[idx] += 1
        self.soldier_rest_streak[idx] = 0

    def rest_soldier(self, idx):
        """Called when a soldier is not deployed (idle = rest)."""
        if self.soldier_work_streak[idx] > 0:
            self.soldier_rest_streak[idx] += 1
            if self.soldier_rest_streak[idx] >= self.soldier_work_streak[idx]:
                self.soldier_work_streak[idx] = 0
                self.soldier_rest_streak[idx] = 0

    def soldier_is_forced_rest(self, idx):
        """Soldier cannot be deployed if they already worked 2 consecutive nights."""
        return self.soldier_work_streak[idx] >= 2

    def moves_remaining_list(self):
        from config import NORMAL_MOVE_IDS
        return [mid for mid in NORMAL_MOVE_IDS if self.move_uses.get(mid, 0) > 0]

    def spend_move(self, move_id):
        if self.move_uses.get(move_id, 0) > 0:
            self.move_uses[move_id] -= 1
            return True
        return False


class Alliance:
    _next_id = 1

    def __init__(self, member_ids, head_id):
        self.id = Alliance._next_id
        Alliance._next_id += 1
        self.members = list(member_ids)
        self.head_id = head_id
        self.active = True
        self.move_uses = {mid: m["max_uses"] for mid, m in ALLIANCE_MOVES.items()}
        self.warded = False

        # Bhoomi Kavach 2-1 cycle
        self.bhoomi_streak = 0        # 0, 1, or 2 consecutive casts
        self.bhoomi_forced_rest = False  # true = can't cast tonight

    def other_member(self, uid):
        for m in self.members:
            if m != uid:
                return m
        return None

    def cast_bhoomi(self):
        if self.bhoomi_forced_rest:
            return False
        self.bhoomi_streak += 1
        if self.bhoomi_streak >= 2:
            self.bhoomi_forced_rest = True
        return True

    def skip_bhoomi(self):
        if self.bhoomi_forced_rest:
            self.bhoomi_forced_rest = False
            self.bhoomi_streak = 0
        else:
            self.bhoomi_streak = 0


class Game:
    def __init__(self, chat_id: int, is_duel=False):
        self.chat_id = chat_id
        self.is_duel = is_duel
        self.phase = "lobby"
        self.day_number = 0
        self.players = {}
        self.join_order = []
        self.alliances = {}
        self.pending_alliance_requests = {}
        self.host_id = None
        self.group_title = ""
        self.lobby_message_id = None
        self.night_job = None
        self.day_job = None
        self.heal_job = None

    def alive_players(self):
        return [p for p in self.players.values() if p.alive]

    def alive_count(self):
        return len(self.alive_players())

    def get_alliance(self, user_id):
        p = self.players.get(user_id)
        if not p or p.alliance_id is None:
            return None
        return self.alliances.get(p.alliance_id)

    def is_alliance_head(self, user_id):
        a = self.get_alliance(user_id)
        return bool(a and a.head_id == user_id)

    def is_same_alliance(self, uid1, uid2):
        p1 = self.players.get(uid1)
        p2 = self.players.get(uid2)
        if not p1 or not p2:
            return False
        if p1.alliance_id is None or p2.alliance_id is None:
            return False
        return p1.alliance_id == p2.alliance_id

    def winner(self):
        alive = self.alive_players()
        if len(alive) <= 1:
            return alive[0] if alive else None
        return None

    def lobby_size(self):
        return len(self.join_order)
