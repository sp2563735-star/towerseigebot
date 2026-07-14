"""
Tower Siege — night resolution engine.

Resolution order (Phase 1):
   1. Judgement Seal registered (blocks NORMAL moves only — never soldiers)
   1.5 Boundless Tower Domain ward pre-registered (blocks alliance moves + Zoya freeze)
   2. Ice Spear freeze registered (warded alliances immune)
   3. Soldier deployments gathered (post-freeze), work/rest tracked
   4. Chandal Eyes redirect rules registered (named-move attacks + Judgement Seal)
   5. Defensive flags registered (shield / mirror / ward)
   6. Named-move attacks resolved (redirect -> shield -> counters -> damage; Bhoomi ward blocks alliance_attack only)
   7. Soldier attacks resolved (shield only; Bhoomi ward does NOT block soldiers)
   8. Heals noted for post-phase application

Phase 2 (after heal window):
  9. Apply heal decisions from the heal window
  10. Apply net damage (damage - defense - heal)
  11. Floor-crossing + elimination narrated
   12. Alliance auto-break on death

GROUP-CHAT MESSAGE GROUPING:
Public report lines are collected into per-move-type "buckets" (via _add)
instead of a single flat list, so that all lines belonging to the same move
(e.g. every Nirvana Sword Slash use that night) stay contiguous in the final
output and share exactly ONE banner GIF, rather than getting interleaved
with unrelated events in submission order. The final flat list is only
assembled at the very end of Phase 2, in a fixed category order, and DM
content / damage math are completely unaffected by this — only the ordering
and de-duplication of what gets posted to the group chat changed.
"""

from config import MOVES, ALLIANCE_MOVES, FLOOR_THRESHOLDS, SOLDIER_DAMAGE, SOLDIER_DEFENSE, SOLDIER_DAMAGE_BOOSTED, SOLDIER_DEFENSE_BOOSTED, PREEMPTIVE_MILK_ABSORB, SOLDIER_COUNT, MAX_SOLDIER_DEFENSE, MAX_SOLDIER_PER_TARGET, EVENT_BANNERS

CATEGORY_ORDER = [
    "ice_spear", "seal", "chandal_eyes", "nirvana", "dino",
    "alliance_attack", "soldier", "heal", "misc", "floor_elim",
]


def _mv(move_id):
    return MOVES.get(move_id) or ALLIANCE_MOVES.get(move_id)


def _join_names(names):
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def resolve_night_phase1(game, name_fn):
    """Calculate all raw damage, blocks, and effects. Returns data for DMs + heal window."""
    players = game.alive_players()
    report_buckets = {}
    banner_used = set()
    dm_data = {}

    def _dm(uid, text):
        dm_data.setdefault(uid, []).append(text)

    def _add(cat, text, banner_key=None):
        bucket = report_buckets.setdefault(cat, [])
        if banner_key and cat not in banner_used and EVENT_BANNERS.get(banner_key):
            banner_used.add(cat)
            bucket.append({"__banner__": banner_key})
        bucket.append(text)

    damage_map = {}
    defend_block = {}
    heal_available = {}

    # ---- Step 1: Judgement Seal (normal moves only) ---------------------
    sealed = set()
    for p in players:
        for act in p.night_actions:
            if act["move_id"] == "jharkhand_seal" and act.get("target_id"):
                sealed.add(act["target_id"])

    # ---- Step 1.5: Pre-register Boundless Tower Domain wards (before Ice Spear freeze) ---
    warded_alliance_ids = set()
    for p in players:
        for act in p.night_actions:
            if act["move_id"] == "bhoomi_domain":
                a = game.get_alliance(p.user_id)
                if a and not a.bhoomi_forced_rest:
                    warded_alliance_ids.add(a.id)

    # ---- Step 2: Ice Spear freeze (normal moves AND soldiers) ----------
    zoya_alliance = None
    zoya_caster_id = None
    for p in players:
        for act in p.night_actions:
            if act["move_id"] == "zoya_spear":
                zoya_alliance = game.get_alliance(p.user_id)
                zoya_caster_id = p.user_id

    frozen = set()
    if zoya_alliance:
        for p in players:
            if p.alliance_id != zoya_alliance.id:
                if p.alliance_id is not None and p.alliance_id in warded_alliance_ids:
                    _dm(p.user_id, f"\U0001f331 Your alliance's Boundless Tower Domain shielded you from the Ice Spear freeze!")
                    continue
                frozen.add(p.user_id)
        caster_name = name_fn(zoya_caster_id) if zoya_caster_id else "Someone"
        _add("ice_spear", f"A bitter frost swept the land. {caster_name} used Ice Spear, consuming the mainland in ice. All unallied kings found themselves frozen in place tonight.", banner_key="zoya_spear")
        if zoya_caster_id:
            _dm(zoya_caster_id, f"\u2744\ufe0f Your Ice Spear swept the land \u2014 {len(frozen)} player(s) frozen tonight.")

    normal_voided = sealed | frozen

    for p in players:
        p.sealed = p.user_id in sealed
        p.frozen = p.user_id in frozen
        if p.frozen:
            p.soldier_deployment = {}

    # Build effective named-move actions (alliance moves always go through)
    # Holy Shield and Super Soldier Serum bypass Judgement Seal but not Ice Spear freeze
    sealed_exempt = {"bengali_shield", "super_soldier_serum"}
    effective = []
    for p in players:
        if p.user_id in frozen:
            normal_acts = [a for a in p.night_actions if a["move_id"] not in ALLIANCE_MOVES]
            alliance_acts = [a for a in p.night_actions if a["move_id"] in ALLIANCE_MOVES]
            if normal_acts:
                _add("ice_spear", f"Ice Spear froze {name_fn(p.user_id)} \u2014 their normal moves and soldiers were voided tonight.")
                _dm(p.user_id, f"\u2744\ufe0f Ice Spear froze you tonight \u2014 your normal moves AND soldiers were voided.")
            for act in alliance_acts:
                effective.append((p.user_id, act["move_id"], act.get("target_id"), act.get("redirect_id")))
            continue
        if p.user_id in sealed:
            # NOTE: no generic "your moves were voided" line here anymore — the
            # single Judgement Seal flavor line below (with both attacker AND
            # target named) already covers this publicly. The private DM to
            # the sealed player still fires as before.
            voided = [a for a in p.night_actions if a["move_id"] not in ALLIANCE_MOVES and a["move_id"] not in sealed_exempt]
            exempt = [a for a in p.night_actions if a["move_id"] in sealed_exempt]
            alliance_acts = [a for a in p.night_actions if a["move_id"] in ALLIANCE_MOVES]
            if voided:
                _dm(p.user_id, "\U0001f512 Judgement Seal locked your normal moves tonight \u2014 they were voided.")
            for act in exempt:
                effective.append((p.user_id, act["move_id"], act.get("target_id"), act.get("redirect_id")))
            for act in alliance_acts:
                effective.append((p.user_id, act["move_id"], act.get("target_id"), act.get("redirect_id")))
            continue
        for act in p.night_actions:
            effective.append((p.user_id, act["move_id"], act.get("target_id"), act.get("redirect_id")))

    # ---- Step 3: Soldier deployments (post-freeze), track work/rest ----
    soldier_attacks = []
    soldier_defend_count = {}

    # Apply rest to all soldiers first (for soldiers not being deployed)
    for p in players:
        deployed_indices = set(p.soldier_deployment.keys())
        for idx in range(SOLDIER_COUNT):
            if idx not in deployed_indices:
                p.rest_soldier(idx)

    for p in players:
        if not p.soldier_deployment:
            continue
        atk_counts = {}
        home_count = 0
        for idx, dest in list(p.soldier_deployment.items()):
            if idx >= len(p.soldier_work_streak):
                continue
            if dest == "home":
                home_count += 1
            else:
                atk_counts[dest] = atk_counts.get(dest, 0) + 1
            p.deploy_soldier(idx)
        if home_count:
            soldier_defend_count[p.user_id] = min(home_count, MAX_SOLDIER_DEFENSE)
        for tgt, cnt in atk_counts.items():
            cnt = min(cnt, MAX_SOLDIER_PER_TARGET)
            soldier_attacks.append((p.user_id, tgt, cnt))

    # ---- Step 4: Register redirects (named-move attacks + Judgement Seal) ----
    redirect_rules = {}
    for actor_id, move_id, target_id, redirect_id in effective:
        if move_id == "chandal_eyes" and target_id:
            redirect_rules[target_id] = (actor_id, redirect_id)

    # ---- Step 5: Register defensive flags & boosts & wards -------------
    bhoomi_alliances_used = set()
    serum_boosted_names = []
    for actor_id, move_id, target_id, redirect_id in effective:
        p = game.players[actor_id]
        if move_id == "bengali_shield":
            p.shield_active = True
        elif move_id == "rajpoot_mirror":
            p.mirror_active = True
        elif move_id == "super_soldier_serum":
            p.soldier_boosted = True
            serum_boosted_names.append(p.name)
            _dm(actor_id, "\U0001f9ea Super Soldier Serum injected \u2014 your soldiers hit harder and defend better tonight!")
        elif move_id == "bhoomi_domain":
            a = game.get_alliance(actor_id)
            if a and not a.bhoomi_forced_rest:
                a.cast_bhoomi()
                a.warded = True
                bhoomi_alliances_used.add(a.id)
                _dm(actor_id, "\U0001f331 Boundless Tower Domain ward cast over your alliance. No one will know until they strike.")

    if serum_boosted_names:
        _add("soldier", f"A surge of power runs through {_join_names(serum_boosted_names)}'s troops as a Super Soldier Serum is injected.")

    # Skip Boundless Tower Domain for alliances whose head didn't cast it
    for a in game.alliances.values():
        if a.id not in bhoomi_alliances_used:
            a.skip_bhoomi()

    # ---- Step 6: Apply redirects, resolve named-move attacks -------------
    resolved = []
    for actor_id, move_id, target_id, redirect_id in effective:
        mv = _mv(move_id)
        if mv and mv["type"] in ("attack", "alliance_attack", "seal"):
            if game.is_same_alliance(actor_id, target_id):
                continue
            if actor_id in redirect_rules:
                caster_id, dest_id = redirect_rules[actor_id]
                if target_id == caster_id and dest_id:
                    mv_name = _mv(move_id)["name"]
                    if move_id == "pandey_slash":
                        _add("nirvana", f"A miracle unfolded as {name_fn(caster_id)} the Mage redirected {name_fn(actor_id)} the Knight's {mv_name} from their tower to {name_fn(dest_id)}'s tower!")
                    elif move_id == "jharkhand_seal":
                        _add("seal", f"{name_fn(caster_id)} the Mage squared {name_fn(actor_id)} the Mage's {mv_name} by redirecting it to {name_fn(dest_id)}'s tower, sealing all three pillars!")
                    else:
                        _add("chandal_eyes", _flavor("chandal_eyes", caster_id, actor_id, dest_id, name_fn), banner_key="chandal_eyes_redirect")
                    _dm(caster_id, f"\U0001f441 {name_fn(actor_id)}'s {mv_name} on you was redirected to {name_fn(dest_id)} by your Chandal Eyes!")
                    _dm(actor_id, f"\U0001f441 Your {mv_name} on {name_fn(caster_id)} was redirected to {name_fn(dest_id)} by their Chandal Eyes!")
                    _dm(dest_id, f"\U0001f441 {name_fn(actor_id)}'s {mv_name} on {name_fn(caster_id)} was redirected to you by Chandal Eyes!")
                    target_id = dest_id
            resolved.append((actor_id, move_id, target_id))
        elif mv and mv["type"] in ("heal", "shield", "counter_pandey", "kidnap"):
            resolved.append((actor_id, move_id, target_id))

    for actor_id, move_id, target_id in resolved:
        mv = _mv(move_id)
        if not mv:
            continue

        if mv["type"] == "attack":
            tgt = game.players.get(target_id)
            if not tgt:
                continue
            if tgt.shield_active:
                _add("nirvana", f"The Holy Shield rose from {name_fn(target_id)}'s tower, effortlessly withstanding {name_fn(actor_id)}'s {mv['name']}!", banner_key="pandey_slash_shield")
                _dm(target_id, f"\U0001f6e1 {name_fn(actor_id)} attacked you with {mv['name']}, but your Holy Shield held firm!")
                _dm(actor_id, f"\U0001f6e1 Your {mv['name']} on {name_fn(target_id)} was blocked by their Holy Shield!")
                continue
            if move_id == "pandey_slash" and tgt.mirror_active:
                damage_map[target_id] = damage_map.get(target_id, 0) + 25
                damage_map[actor_id] = damage_map.get(actor_id, 0) + 25
                _add("nirvana", _flavor("rajpoot_mirror", target_id, actor_id, None, name_fn), banner_key="pandey_slash_mirror")
                _dm(target_id, f"\U0001fa78 {name_fn(actor_id)}'s Nirvana Sword Slash struck your tower \u2014 but your Divine Slash Reflection reflected it, you only took 25 damage.")
                _dm(actor_id, f"\U0001fa78 Your Nirvana Sword Slash on {name_fn(target_id)} was reflected by their Divine Slash Reflection \u2014 you took 25 damage!")
                continue
            damage_map[target_id] = damage_map.get(target_id, 0) + mv["damage"]
            _dm(target_id, f"\U0001f5e1 {name_fn(actor_id)} attacked your tower with {mv['name']}!")
            _dm(actor_id, f"\U0001f5e1 You used {mv['name']} on {name_fn(target_id)}'s tower, dealing {mv['damage']} HP damage.")
            if mv.get("narrate"):
                _add("nirvana", _flavor(move_id, actor_id, target_id, None, name_fn), banner_key="pandey_slash_hit")
            else:
                _add("nirvana", f"{name_fn(actor_id)} struck {name_fn(target_id)} with {mv['name']} for {mv['damage']} damage.", banner_key="pandey_slash_hit")

        elif mv["type"] == "alliance_attack":
            tgt = game.players.get(target_id)
            if not tgt:
                continue
            tgt_alliance = game.get_alliance(target_id)
            if tgt_alliance and tgt_alliance.warded:
                _add("alliance_attack", f"As the battle continued, {name_fn(actor_id)} used {mv['name']}, one of the deadliest moves, but {name_fn(target_id)} had Boundless Tower Domain cast on their tower to save them.", banner_key="seelampur_strike_blocked")
                _dm(target_id, f"\U0001f331 {name_fn(actor_id)} struck you with {mv['name']}, but your alliance's Boundless Tower Domain ward protected your tower.")
                _dm(actor_id, f"\U0001f331 Your {mv['name']} on {name_fn(target_id)} was deflected by their Boundless Tower Domain ward.")
                continue
            damage_map[target_id] = damage_map.get(target_id, 0) + mv["damage"]
            _dm(target_id, f"\U0001f4a5 {name_fn(actor_id)} attacked you with {mv['name']}! You had no Boundless Tower Domain cast on your tower \u2014 it's been destroyed.")
            _dm(actor_id, f"\U0001f4a5 Your {mv['name']} hit {name_fn(target_id)}'s tower, destroying it completely!")
            _add("alliance_attack", f"As the long battle concluded, {name_fn(actor_id)} completely destroyed {name_fn(target_id)}'s tower with {mv['name']}.", banner_key="seelampur_strike_hit")

        elif mv["type"] == "heal":
            heal_available[actor_id] = heal_available.get(actor_id, 0) + PREEMPTIVE_MILK_ABSORB
            _dm(actor_id, f"\U0001f4a7 You used {mv['name']} preemptively.")

        elif mv["type"] == "seal":
            _add("seal", _flavor(move_id, actor_id, target_id, None, name_fn))
            _dm(target_id, f"\U0001f512 {name_fn(actor_id)} hit you with Judgement Seal \u2014 your normal moves couldn't act!")
            _dm(actor_id, f"\U0001f512 Your Judgement Seal locked {name_fn(target_id)}'s normal moves tonight.")

        elif mv["type"] == "kidnap":
            tgt = game.players.get(target_id)
            if tgt:
                tgt.pending_kidnap_by = actor_id
                _add("dino", _flavor(move_id, actor_id, target_id, None, name_fn), banner_key="chamar_teleporter")
                _dm(target_id, f"\U0001f300 {name_fn(actor_id)} the Knight teleported you from your tower! You'll be skipped next night unless you escape.")
                _dm(actor_id, f"\U0001f300 You kidnapped {name_fn(target_id)} with your Knight's exceptional teleporting ability! They'll be skipped next night unless they escape.")

    # ---- Step 7: Resolve soldier attacks (shield only, no ward/mirror/dodge) ---
    soldier_groups = {}
    for actor_id, target_id, count in soldier_attacks:
        tgt = game.players.get(target_id)
        if not tgt:
            continue
        if game.is_same_alliance(actor_id, target_id):
            _add("soldier", f"\U0001f91d {name_fn(actor_id)}'s soldiers refused to strike their allies in {name_fn(target_id)}'s tower.")
            _dm(actor_id, f"\U0001f91d Your soldiers refused to strike {name_fn(target_id)} \u2014 they're your alliance!")
            continue
        actor_p = game.players.get(actor_id)
        atk_damage = SOLDIER_DAMAGE_BOOSTED if (actor_p and actor_p.soldier_boosted) else SOLDIER_DAMAGE
        dmg = count * atk_damage
        if tgt.shield_active:
            _dm(target_id, f"\U0001f6e1 {name_fn(actor_id)} sent soldiers at your tower, only to retreat as your Holy Shield withstood the assault!")
            _dm(actor_id, f"\U0001f6e1 Your soldiers reached {name_fn(target_id)}'s tower but were turned away by their Holy Shield!")
            continue
        damage_map[target_id] = damage_map.get(target_id, 0) + dmg
        soldier_groups.setdefault(actor_id, []).append((target_id, count, dmg))
        _dm(target_id, f"\U0001f96a {name_fn(actor_id)} sent {count} soldier(s) at you, dealing {dmg} damage.")
        _dm(actor_id, f"\U0001f96a You sent {count} soldier(s) to attack {name_fn(target_id)} for {dmg} damage.")
    for actor_id, hits in soldier_groups.items():
        parts = [f"{c} soldier(s) to strike {name_fn(t)}" for t, c, d in hits]
        _add("soldier", f"{name_fn(actor_id)} sent {', '.join(parts)}.")

    # ---- Step 8: Calculate defense blocks -------------------------------
    for p in players:
        raw = damage_map.get(p.user_id, 0)
        block = 0
        defend_n = soldier_defend_count.get(p.user_id, 0)
        if defend_n > 0 and raw > 0:
            def_val = SOLDIER_DEFENSE_BOOSTED if p.soldier_boosted else SOLDIER_DEFENSE
            block = min(defend_n * def_val, raw)
            _dm(p.user_id, f"\U0001f96a Your {defend_n} soldier(s) on defense blocked {block} damage this round!")
        defend_block[p.user_id] = block

    return {
        "report_buckets": report_buckets,
        "banner_used": banner_used,
        "dm_data": dm_data,
        "damage_map": damage_map,
        "defend_block": defend_block,
        "heal_available": heal_available,
        "effective_actions": effective,
        "bhoomi_alliances": bhoomi_alliances_used,
    }


def resolve_night_phase2(game, name_fn, phase1_data, heal_accepted=None):
    """
    Phase 2: Apply heals from heal window, then net damage, floors, eliminations.
    heal_accepted: set of uid who accepted the reactive Magic Milk heal (25 HP flat).
    Returns (gc_report_lines, eliminated_uids, winner_player_or_None).
    """
    if heal_accepted is None:
        heal_accepted = set()

    report_buckets = {k: list(v) for k, v in phase1_data["report_buckets"].items()}
    banner_used = set(phase1_data["banner_used"])
    damage_map = dict(phase1_data["damage_map"])
    defend_block = dict(phase1_data["defend_block"])
    heal_available = phase1_data.get("heal_available", {})

    def _add(cat, text, banner_key=None):
        bucket = report_buckets.setdefault(cat, [])
        if banner_key and cat not in banner_used and EVENT_BANNERS.get(banner_key):
            banner_used.add(cat)
            bucket.append({"__banner__": banner_key})
        bucket.append(text)

    players = game.alive_players()
    eliminated = []

    heal_amount = MOVES["yadav_milk"]["heal"]

    # Apply preemptive heals (Magic Milk used as a night action) — merged into one line
    preemptive_healed_names = []
    for uid, heal_amt in heal_available.items():
        p = game.players.get(uid)
        if not p or not p.alive:
            continue
        net_damage = max(0, damage_map.get(uid, 0) - defend_block.get(uid, 0))
        if net_damage <= 0:
            continue
        effective = min(heal_amt, net_damage)
        damage_map[uid] = max(0, damage_map.get(uid, 0) - effective)
        preemptive_healed_names.append(p.name)
    if preemptive_healed_names:
        _add("heal", f"{_join_names(preemptive_healed_names)}'s tower walls were reinforced by preemptive healing.")

    # Apply reactive heal (flat heal reduction for each player who accepted) — merged into one line
    reactive_healed_names = []
    for uid in heal_accepted:
        p = game.players.get(uid)
        if not p or not p.alive:
            continue
        net_damage = max(0, damage_map.get(uid, 0) - defend_block.get(uid, 0))
        if net_damage <= 0:
            continue
        effective_heal = min(heal_amount, net_damage)
        current_raw = damage_map.get(uid, 0)
        damage_map[uid] = max(0, current_raw - effective_heal)
        reactive_healed_names.append(p.name)
    if reactive_healed_names:
        healer_word = "healer" if len(reactive_healed_names) == 1 else "healers"
        _add("heal", f"Other kings noticed {_join_names(reactive_healed_names)}'s tower {healer_word} casting a spell to restore their walls.")

    # ---- Apply net damage, floors, eliminations -------------------------
    # Floor-break lines are intentionally NOT posted to the group anymore —
    # the "Kingdoms remaining" summary already shows floors-standing per
    # player at the end of the day, so only the final collapse line posts.
    for p in players:
        raw_damage = damage_map.get(p.user_id, 0)
        block = defend_block.get(p.user_id, 0)
        net_damage = max(0, raw_damage - block)

        if net_damage == 0:
            continue

        old_hp = p.hp
        new_hp = max(0, min(100, old_hp - net_damage))

        for i, threshold in enumerate(FLOOR_THRESHOLDS):
            if old_hp > threshold >= new_hp and not p.floor_broken[i]:
                p.floor_broken[i] = True

        p.hp = new_hp
        if new_hp <= 0 and p.alive:
            p.alive = False
            eliminated.append(p.user_id)
            _add("floor_elim", f"\U0001f480 {p.name}'s tower has collapsed. They are out of the game.")
            # Alliance auto-break on death
            if p.alliance_id is not None:
                a = game.alliances.get(p.alliance_id)
                if a:
                    other_id = a.other_member(p.user_id)
                    if other_id:
                        other = game.players.get(other_id)
                        if other:
                            other.alliance_id = None
                    del game.alliances[a.id]
                p.alliance_id = None

    # ---- Check winner ----------------------------------------------------
    winner = None
    alive_list = game.alive_players()
    if len(alive_list) == 2 and game.lobby_size() <= 5:
        p1, p2 = alive_list[0], alive_list[1]
        if game.is_same_alliance(p1.user_id, p2.user_id):
            a = game.get_alliance(p1.user_id)
            if a:
                _add("floor_elim", f"\U0001f494 With only two standing, the pact between {p1.name} and {p2.name} shatters. The throne must be decided alone.")
                p1.alliance_id = None
                p2.alliance_id = None
                del game.alliances[a.id]
    elif len(alive_list) == 2 and game.lobby_size() >= 6:
        p1, p2 = alive_list[0], alive_list[1]
        if game.is_same_alliance(p1.user_id, p2.user_id):
            winner = (p1, p2)
    elif len(alive_list) == 1:
        winner = (alive_list[0],)

    # ---- Flatten buckets into the final, category-grouped report --------
    report = []
    for cat in CATEGORY_ORDER:
        report.extend(report_buckets.get(cat, []))
    # catch anything from an unexpected/unlisted category, just in case
    for cat, lines in report_buckets.items():
        if cat not in CATEGORY_ORDER:
            report.extend(lines)

    return report, eliminated, winner


def _flavor(move_id, attacker_id, target_id, redirect_id, name_fn):
    mv = _mv(move_id)
    text = mv.get("flavor", "{attacker} used {move}.")
    filled = text.format(
        attacker=name_fn(attacker_id) if attacker_id else "",
        target=name_fn(target_id) if target_id else "",
        redirect=name_fn(redirect_id) if redirect_id else "",
        move=mv["name"],
    )
    return f"{filled} ({mv['name']})"
