# DUEL/1.0 — Turn-Based RPG Combat Protocol

A socket-programming mini-project: a two-player turn-based RPG duel, played
over a custom Application-Layer protocol (**DUEL/1.0**) running on top of
TCP.

This README doubles as the write-up for Part 1 of the assignment
(objective, characteristics, transport choice, and protocol design).

---

## 1. Objective & Application Overview

**What it is:** A two-player, turn-based combat game (Warrior / Mage / Rogue,
inspired by classic RPG duels) played by two clients connecting to a
referee server over the network.

**What it's for:** Each client picks a class, then the two players take
alternating turns choosing an action (attack, spell, block, buff, etc.).
The server is the single source of truth for game state — it validates
every action, applies damage/status-effect rules, and reports the result
back to both players with an explicit status code, the same way an HTTP
server reports success/failure.

**Characteristics:**
- **Client–Server**, not peer-to-peer: the server holds authoritative game
  state (HP, mana, status effects, turn order) so both clients always see
  a consistent view of the match.
- **Stateful / session-based**: a match progresses through distinct phases
  (handshake → lobby/ready → combat → game over), and the server must
  remember where each connection is in that lifecycle.
- **Turn-based, not real-time**: exactly one player acts at a time; there's
  no strict timing requirement between messages.
- **Request/response, text-based**: every client message gets exactly one
  (or more, for broadcasts) server response, each carrying a 3-digit status
  code and phrase — modeled deliberately on HTTP so it's easy to log,
  demo, and grade.

### Why TCP (not UDP)?

| Requirement | Why it points to TCP |
|---|---|
| **Reliability** | If an `ACTION` message is lost, the game desyncs — one player thinks it's still their turn, the other is waiting forever. Turn-based state cannot tolerate silent packet loss the way, say, video streaming can. |
| **Ordering** | Status-effect ticks, action resolution, and turn-swap must happen in a fixed order. UDP does not guarantee messages arrive in the order they were sent. |
| **Session/connection state** | The lobby handshake (`HELLO` → `CHOOSE` → `READY`) naturally maps onto a persistent TCP connection per player; the server can detect disconnects (forfeits) via the socket closing. |
| **No hard latency requirement** | Combat is turn-based with human think-time between actions, so TCP's slightly higher overhead (handshake, ACKs) is irrelevant here — we're not fighting for every millisecond like a real-time shooter would. |

Since we don't need to hand-roll retransmission, ordering, or connection
tracking ourselves, TCP lets the project effort go into the actual
application protocol and game logic rather than reinventing reliability.

---

## 2. Protocol Design — DUEL/1.0

Full technical spec (message grammar, status code table, headers) lives as
a docstring at the top of **`protocol.py`** — read it there for the
authoritative reference. Summary below.

### Message shape

**Request (Client → Server)** — one line, HTTP-request-line style:
```
VERB arg1 arg2 ... argN\r\n
```

**Response (Server → Client)** — a status line plus headers, terminated by
a blank line (HTTP-style):
```
DUEL/1.0 <code> <phrase>\r\n
Key: Value\r\n
...\r\n
\r\n
```

### Verbs (requests)

| Verb | Example | Meaning |
|---|---|---|
| `HELLO` | `HELLO Nont` | Identify yourself when connecting |
| `CHOOSE` | `CHOOSE WARRIOR LIGHT` | Pick class (and variant, for Warrior) |
| `READY` | `READY` | Signal you're ready to start the match |
| `ACTION` | `ACTION HEAVY` / `ACTION SPELL FIREBALL` / `ACTION BUFF HEAL SELF` | Take your combat action on your turn |
| `BLOCK` | `BLOCK` | Defensive stance (any class) |
| `QUIT` | `QUIT` | Forfeit the match |

### Status codes (responses)

| Code | Phrase | Meaning |
|---|---|---|
| 100 | CONTINUE | Waiting on something (opponent to connect/ready, or it's not your turn) |
| 200 | OK | Simple acknowledgement (e.g. HELLO/CHOOSE accepted) |
| 201 | MATCH_STARTED | Both players ready; combat begins |
| 202 | TURN_RESOLVED | An action was legally applied — includes full state in headers |
| 204 | NO_EFFECT | Action resolved but did nothing (miss/dodge/block/resist) |
| 210 | GAME_OVER | Match concluded — `Winner` header included |
| 400 | BAD_REQUEST | Malformed message |
| 401 | NOT_YOUR_TURN | Reserved for out-of-turn sends |
| 403 | INVALID_ACTION | Illegal action (e.g. not enough mana) |
| 404 | UNKNOWN_TARGET | Target does not exist |
| 409 | CONFLICT | Status-effect prerequisite not met |
| 500 | SERVER_ERROR | Unexpected server-side failure |

Both client and server print every request sent/received and every
response's status line + headers to the console, satisfying the
"print messages and status codes" requirement.

---

## 3. Game Rules Implemented

Implements the RPG combat system from the project plan (`game_engine.py`):

- **Classes**: Warrior (Light & Heavy variants), Mage, Rogue — each with
  their own HP/mana pools and normal/heavy/spell actions.
- **Status effects**: Burning, Bleeding → Severe Bleeding escalation,
  Shocking (stun), Dazed, Freezing (breaks on hit, bonus damage from heavy
  weapons is a natural extension point), Poison, elemental imbues, Iron
  Skin.
- **Defensive mechanics**: Blocking (full negate / parry for Warrior
  Light), Heavy Warrior's stability resistance and miss chance, Rogue
  dodge and critical hits. **Blocking is available to every class** and
  always restores 1 mana on top of its damage-reduction effect, at the
  cost of doing nothing else that turn.
- **Turn engine**: status effects tick at the start of each character's
  turn (damage-over-time, stun checks) before the player is prompted to
  act.

This is a solid working core; some of the finer rule interactions from the
plan (e.g. every possible imbue/element combo) are left as easy extensions
in `game_engine.py` — the `resolve_action()` function is the single place
new verbs/rules get added.

---

## 4. How to Run

Requires Python 3.8+, no external dependencies (standard library `socket`
only).

**1. Start the server** (on the host that will referee the match):
```bash
python3 server.py 5050
```

**2. Start two clients** (can be on the same machine for testing, or two
different machines pointed at the server's IP):
```bash
python3 client.py <server_ip> 5050
```
You'll be prompted for your name and class (`WARRIOR` / `MAGE` / `ROGUE`,
plus `LIGHT`/`HEAVY` variant for Warrior). Once both clients are connected
and `READY`, the match begins and you'll get a menu each turn.

You can also pass everything as arguments for a quick non-interactive
test:
```bash
python3 client.py 127.0.0.1 5050 Nont WARRIOR LIGHT
python3 client.py 127.0.0.1 5050 Belle MAGE
```

Every request/response with its status code is printed on both the
server and client consoles, so you can screen-record the terminals
directly for the demo video.

---

## 5. File Structure

```
duel-project/
├── protocol.py     # DUEL/1.0 wire format: encode/decode, status codes (this is the protocol spec)
├── game_engine.py  # Character classes, status effects, combat resolution (network-independent)
├── server.py       # TCP server: lobby handshake + turn-based referee loop
├── client.py       # TCP client: interactive menu, prints all protocol traffic
└── README.md       # This file — also the Part 1 protocol write-up
```

## 6. Presentation Clip

[![Presentation Video Clip](https://drive.google.com/file/d/1Kw8eAw9oJuNA2B8x5r649ZOiLfI6S2fD/view?usp=sharing)](https://drive.google.com/file/d/1Kw8eAw9oJuNA2B8x5r649ZOiLfI6S2fD/view?usp=sharing)