"""
game_engine.py
==============
Core RPG combat rules, independent of networking. The server imports this
module to resolve actions; keeping it separate makes the protocol layer
and the game-logic layer testable independently.

All randomness (crit/dodge/block/miss chances, etc.) is resolved here via
`random.random() < chance`.
"""

import random

# ---------------------------------------------------------------------------
# Status effect definitions: name -> default duration in turns
# ---------------------------------------------------------------------------
STATUS_DURATIONS = {
    "BURNING": 3,      # 1 heart/turn
    "BLEEDING": 3,      # 1 heart/turn
    "SEVERE_BLEEDING": 0,  # instant burst, not a lingering status
    "SHOCKING": 2,      # stunned, can't act
    "DAZED": 3,          # 50% chance to fail an attack
    "FREEZING": 3,      # can't act; broken instantly if hit
    "POISON": 3,         # 1 heart/turn (rogue)
    "SNEAKY": 1,          # rogue evasion buff (2 turns on first use)
    "IRON_SKIN": 2,      # 50% damage reduction buff
    "FIRE_IMBUE": 999,     # lasts until weapon swapped / match end (simplified: 3 turns)
    "THUNDER_IMBUE": 999,
    "FROST_IMBUE": 999,
}

DOT_STATUSES = {"BURNING", "BLEEDING", "POISON"}  # damage-over-time, 1 heart/turn

# "Hard stuns" - statuses that fully prevent the target from acting on
# their turn. Reapplying the SAME hard stun while it's already active
# breaks it instead of refreshing the duration (see _try_apply_status),
# which prevents infinite stun-lock loops.
STUN_STATUSES = {"SHOCKING", "FREEZING"}
STUN_BREAK_BONUS_DAMAGE = 1  # bonus damage dealt when a repeat-stun backfires

BLOCK_MANA_REGEN = 1  # every class regains this much mana when they choose BLOCK


class StatusEffect:
    def __init__(self, name, turns_left=None):
        self.name = name
        self.turns_left = STATUS_DURATIONS.get(name, 1) if turns_left is None else turns_left

    def __repr__(self):
        return f"{self.name}({self.turns_left})"


class Character:
    """Base class for all playable classes."""

    def __init__(self, owner_name):
        self.owner_name = owner_name       # player/username
        self.class_name = "BASE"
        self.hp = 0
        self.max_hp = 0
        self.mana = 0
        self.max_mana = 0
        self.statuses = {}                 # name -> StatusEffect
        self.bleeding_turns_survived = 0    # tracks consecutive bleeding turns for SEVERE_BLEEDING
        self.alive = True

    # -- status helpers -----------------------------------------------
    def has_status(self, name):
        return name in self.statuses

    def apply_status(self, name, turns=None):
        self.statuses[name] = StatusEffect(name, turns)

    def remove_status(self, name):
        self.statuses.pop(name, None)

    def is_stunned(self):
        """Statuses that outright prevent acting this turn."""
        return self.has_status("SHOCKING") or self.has_status("FREEZING")

    def take_damage(self, amount, log):
        amount = max(0, amount)
        self.hp = max(0, self.hp - amount)
        if self.hp == 0:
            self.alive = False
        return amount

    def heal(self, amount):
        self.hp = min(self.max_hp, self.hp + amount)

    def spend_mana(self, amount):
        if self.mana < amount:
            return False
        self.mana -= amount
        return True

    # -- start-of-turn upkeep: tick DOT / duration-based statuses -----
    def tick_statuses(self, log):
        """Called at the start of this character's turn. Applies
        damage-over-time and decrements durations. Returns True if the
        character is stunned/frozen and must skip their action."""
        skip_turn = False

        for name in list(self.statuses.keys()):
            eff = self.statuses[name]

            if name in DOT_STATUSES:
                dmg = self.take_damage(1, log)
                log.append(f"{self.owner_name} takes {dmg} damage from {name}")
                if name == "BLEEDING":
                    self.bleeding_turns_survived += 1

            if name == "SHOCKING":
                skip_turn = True
                log.append(f"{self.owner_name} is stunned by SHOCKING and cannot act")

            if name == "FREEZING":
                skip_turn = True
                log.append(f"{self.owner_name} is frozen solid and cannot act")

            if name == "DAZED":
                # resolved at attack-time (50% fail chance), not here
                pass

            eff.turns_left -= 1
            if eff.turns_left <= 0 and name not in ("FIRE_IMBUE", "THUNDER_IMBUE", "FROST_IMBUE"):
                del self.statuses[name]
                log.append(f"{self.owner_name}'s {name} has worn off")

        return skip_turn

    def status_summary(self):
        return ",".join(self.statuses.keys()) if self.statuses else "NONE"


# ---------------------------------------------------------------------------
# Concrete classes
# ---------------------------------------------------------------------------
class WarriorLight(Character):
    def __init__(self, owner_name):
        super().__init__(owner_name)
        self.class_name = "WARRIOR_LIGHT"
        self.hp = self.max_hp = 20
        self.mana = self.max_mana = 10
        self.block_full_negate_chance = 0.45
        self.parry_chance = 0.20  # physical only

    def normal_attack(self):
        return {"damage": 3, "status": None, "mana_cost": 0}

    def heavy_attack(self):
        return {"damage": 4, "status": "BLEEDING", "mana_cost": 3}


class WarriorHeavy(Character):
    def __init__(self, owner_name):
        super().__init__(owner_name)
        self.class_name = "WARRIOR_HEAVY"
        self.hp = self.max_hp = 25
        self.mana = self.max_mana = 5
        self.stable_resist_chance = 0.50  # resist dazed/shocking/freezing/burning
        self.miss_chance = 0.40

    def normal_attack(self):
        return {"damage": 4, "status": "DAZED", "mana_cost": 0}

    def heavy_attack(self):
        return {"damage": 5, "status": ["DAZED", "BLEEDING"], "mana_cost": 3}


class Mage(Character):
    def __init__(self, owner_name):
        super().__init__(owner_name)
        self.class_name = "MAGE"
        self.hp = self.max_hp = 15
        self.mana = self.max_mana = 20

    def spell(self, name):
        table = {
            "FIREBALL": {"damage": 3, "status": "BURNING", "mana_cost": 4, "aoe": False},
            "THUNDER_SHOCK": {"damage": 2, "status": "SHOCKING", "mana_cost": 4, "aoe": False},
            "EARTHQUAKE": {"damage": 3, "status": "DAZED", "mana_cost": 4, "aoe": True},
            "FROSTBITE": {"damage": 3, "status": "FREEZING", "mana_cost": 4, "aoe": False},
        }
        return table.get(name)

    def buff_cost(self):
        return 2

    def heal_cost(self):
        return 3

    def heal_amount(self):
        return 10

    def regen_amount(self):
        return 10


class Rogue(Character):
    def __init__(self, owner_name):
        super().__init__(owner_name)
        self.class_name = "ROGUE"
        self.hp = self.max_hp = 18
        self.mana = self.max_mana = 10
        self.crit_chance = 0.20
        self.dodge_chance = 0.35
        self.used_sneaky_once = False

    def normal_attack(self):
        return {"damage": 3, "status": None, "mana_cost": 0}

    def heavy_attack(self):
        return {"damage": 4, "status": "BLEEDING", "mana_cost": 0}

    def poison_imbue_cost(self):
        return 3


def make_character(owner_name, class_name, variant=None):
    class_name = class_name.upper()
    if class_name == "WARRIOR":
        variant = (variant or "LIGHT").upper()
        return WarriorLight(owner_name) if variant == "LIGHT" else WarriorHeavy(owner_name)
    if class_name == "MAGE":
        return Mage(owner_name)
    if class_name == "ROGUE":
        return Rogue(owner_name)
    raise ValueError(f"Unknown class: {class_name}")


# ---------------------------------------------------------------------------
# Combat resolution
# ---------------------------------------------------------------------------
def resolve_action(actor: Character, target: Character, verb, args, log):
    """
    Resolves one player's action against their opponent.
    Returns a dict describing the outcome:
        {"code": int, "damage": int, "status_applied": [...], "note": str}
    `log` is a list of human-readable strings appended during resolution
    (useful for server-side console printing).
    """
    result = {"code": 202, "damage": 0, "status_applied": [], "note": ""}

    # --- BLOCK (any class) ------------------------------------------
    # Blocking has two effects for every class: it reduces/negates the next
    # incoming hit (handled in _apply_damage_with_defenses), AND it restores
    # 1 mana, per the original game design ("blocking... restores mana on
    # that turn"). The player can't do anything else this turn.
    if verb == "BLOCK":
        result["note"] = f"{actor.owner_name} braces to block."
        actor.apply_status("_BLOCKING", turns=1)
        mana_before = actor.mana
        actor.mana = min(actor.max_mana, actor.mana + BLOCK_MANA_REGEN)
        gained = actor.mana - mana_before
        log.append(f"{actor.owner_name} chooses to BLOCK this turn.")
        if gained:
            log.append(f"{actor.owner_name} regenerates {gained} mana from blocking (mana: {actor.mana}/{actor.max_mana})")
        return result

    # --- WARRIOR actions ----------------------------------------------
    if isinstance(actor, (WarriorLight, WarriorHeavy)):
        if verb == "ACTION" and args and args[0] in ("NORMAL", "HEAVY"):
            mode = args[0]
            atk = actor.normal_attack() if mode == "NORMAL" else actor.heavy_attack()
            if not actor.spend_mana(atk["mana_cost"]):
                result["code"] = 403
                result["note"] = "Not enough mana."
                return result

            if isinstance(actor, WarriorHeavy) and random.random() < actor.miss_chance:
                result["code"] = 204
                result["note"] = f"{actor.owner_name}'s heavy swing misses!"
                log.append(result["note"])
                return result

            dmg = _apply_damage_with_defenses(actor, target, atk["damage"], log)
            result["damage"] = dmg
            statuses = atk["status"] if isinstance(atk["status"], list) else (
                [atk["status"]] if atk["status"] else []
            )
            for s in statuses:
                bonus, applied = _try_apply_status(target, s, log)
                result["damage"] += bonus
                if applied:
                    result["status_applied"].append(s)
            log.append(f"{actor.owner_name} used {mode} attack on {target.owner_name} for {dmg} damage.")
            return result

    # --- MAGE actions ---------------------------------------------------
    if isinstance(actor, Mage):
        if verb == "ACTION" and args and args[0] == "SPELL":
            spell_name = args[1] if len(args) > 1 else ""
            spell = actor.spell(spell_name)
            if not spell:
                result["code"] = 400
                result["note"] = f"Unknown spell {spell_name}"
                return result
            if not actor.spend_mana(spell["mana_cost"]):
                result["code"] = 403
                result["note"] = "Not enough mana."
                return result
            dmg = _apply_damage_with_defenses(actor, target, spell["damage"], log)
            result["damage"] = dmg
            if spell["status"]:
                bonus, applied = _try_apply_status(target, spell["status"], log)
                result["damage"] += bonus
                if applied:
                    result["status_applied"].append(spell["status"])
            log.append(f"{actor.owner_name} cast {spell_name} on {target.owner_name} for {dmg} damage.")
            return result

        if verb == "ACTION" and args and args[0] == "BUFF":
            sub = args[1] if len(args) > 1 else ""
            if sub == "HEAL":
                if not actor.spend_mana(actor.heal_cost()):
                    result["code"] = 403
                    result["note"] = "Not enough mana."
                    return result
                target_char = actor if (len(args) > 2 and args[2] == "SELF") else target
                target_char.heal(actor.heal_amount())
                result["note"] = f"{actor.owner_name} heals {target_char.owner_name} for {actor.heal_amount()}."
                log.append(result["note"])
                return result
            if sub == "IMBUE":
                element = args[2] if len(args) > 2 else "FIRE"
                if not actor.spend_mana(actor.buff_cost()):
                    result["code"] = 403
                    result["note"] = "Not enough mana."
                    return result
                imbue_status = f"{element.upper()}_IMBUE"
                actor.apply_status(imbue_status, turns=3)
                result["note"] = f"{actor.owner_name} imbues weapon with {element}."
                log.append(result["note"])
                return result
            if sub == "IRONSKIN":
                if not actor.spend_mana(actor.buff_cost()):
                    result["code"] = 403
                    result["note"] = "Not enough mana."
                    return result
                target_char = actor if (len(args) > 2 and args[2] == "SELF") else target
                target_char.apply_status("IRON_SKIN", turns=2)
                result["note"] = f"{actor.owner_name} grants Iron Skin to {target_char.owner_name}."
                log.append(result["note"])
                return result

        if verb == "ACTION" and args and args[0] == "REGEN":
            actor.mana = min(actor.max_mana, actor.mana + actor.regen_amount())
            result["note"] = f"{actor.owner_name} regenerates mana."
            log.append(result["note"])
            return result

    # --- ROGUE actions ----------------------------------------------
    if isinstance(actor, Rogue):
        if verb == "ACTION" and args and args[0] in ("NORMAL", "HEAVY"):
            mode = args[0]
            atk = actor.normal_attack() if mode == "NORMAL" else actor.heavy_attack()
            dmg_base = atk["damage"]
            is_crit = random.random() < actor.crit_chance
            if is_crit:
                dmg_base *= 2
            dmg = _apply_damage_with_defenses(actor, target, dmg_base, log)
            result["damage"] = dmg
            if atk["status"]:
                bonus, applied = _try_apply_status(target, atk["status"], log)
                result["damage"] += bonus
                if applied:
                    result["status_applied"].append(atk["status"])
            note = f"{actor.owner_name} used {mode} attack on {target.owner_name} for {dmg} damage."
            if is_crit:
                note += " CRITICAL HIT!"
            result["note"] = note
            log.append(note)
            return result

        if verb == "ACTION" and args and args[0] == "POISON_IMBUE":
            if not actor.spend_mana(actor.poison_imbue_cost()):
                result["code"] = 403
                result["note"] = "Not enough mana."
                return result
            actor.apply_status("POISON_IMBUE_READY", turns=99)
            result["note"] = f"{actor.owner_name} coats daggers with poison."
            log.append(result["note"])
            return result

    result["code"] = 400
    result["note"] = f"Unrecognized action {verb} {args} for class {actor.class_name}"
    return result


def _try_apply_status(target: Character, status_name, log):
    """Applies a status honoring resistances and interaction rules
    (e.g. WarriorHeavy's 50% resist chance, freezing-breaks-on-hit,
    bleeding -> severe bleeding escalation, stun-lock prevention).

    Returns (bonus_damage, applied):
      - bonus_damage: extra damage dealt as a side effect (e.g. a repeat
        stun backfiring). Callers should add this to their reported damage.
      - applied: whether the status actually ended up active on the
        target (False if resisted, broken, or backfired) — callers should
        only report the status as applied when this is True.
    """
    if status_name is None:
        return 0, False

    # Heavy warrior stability: 50% resist to dazed/shocking/freezing/burning
    if isinstance(target, WarriorHeavy) and status_name in ("DAZED", "SHOCKING", "FREEZING", "BURNING"):
        if random.random() < target.stable_resist_chance:
            log.append(f"{target.owner_name}'s Heavy Warrior stability RESISTS {status_name}!")
            return 0, False

    # Anti stun-lock rule: reapplying the SAME hard stun (Shocking/Freezing)
    # while it's already active breaks it instead of refreshing the
    # duration, and deals bonus damage. This is what stops a caster from
    # spamming one stun spell to lock a target out of the game forever.
    if status_name in STUN_STATUSES and target.has_status(status_name):
        target.remove_status(status_name)
        bonus = target.take_damage(STUN_BREAK_BONUS_DAMAGE, log)
        log.append(
            f"{target.owner_name}'s {status_name} destabilizes from the repeat hit! "
            f"Stun broken, {bonus} bonus damage dealt."
        )
        return bonus, False

    # Freezing breaks instantly when the frozen target is hit by anything else
    if status_name != "FREEZING" and target.has_status("FREEZING"):
        target.remove_status("FREEZING")
        log.append(f"{target.owner_name}'s FREEZING shatters from the hit!")

    # Bleeding -> Severe Bleeding escalation (2+ turns of bleeding, hit with sharp+heavy)
    if status_name == "BLEEDING" and target.has_status("BLEEDING") and target.bleeding_turns_survived >= 2:
        target.remove_status("BLEEDING")
        target.bleeding_turns_survived = 0
        log.append(f"{target.owner_name}'s bleeding escalates to SEVERE BLEEDING!")
        return 0, False  # caller should separately deal severe bleeding burst damage if desired

    target.apply_status(status_name)
    log.append(f"{target.owner_name} is now afflicted with {status_name}")
    return 0, True


def _apply_damage_with_defenses(actor, target, base_damage, log):
    """Applies dodge / block / parry / iron-skin modifiers, then damage."""
    # Dodge (Rogue)
    if isinstance(target, Rogue) and random.random() < target.dodge_chance:
        log.append(f"{target.owner_name} DODGES the attack!")
        return 0

    # Blocking (WarriorLight full negate / parry, generic block = 50% reduction)
    if target.has_status("_BLOCKING"):
        target.remove_status("_BLOCKING")
        if isinstance(target, WarriorLight):
            if random.random() < target.block_full_negate_chance:
                log.append(f"{target.owner_name} completely BLOCKS the attack!")
                return 0
            if random.random() < target.parry_chance:
                log.append(f"{target.owner_name} PARRIES the attack!")
                return 0
        # generic block: half damage
        base_damage = base_damage // 2
        log.append(f"{target.owner_name} blocks, reducing the damage.")

    # Iron Skin buff: 50% reduction
    if target.has_status("IRON_SKIN"):
        base_damage = base_damage // 2
        log.append(f"{target.owner_name}'s Iron Skin reduces the damage.")

    return target.take_damage(base_damage, log)