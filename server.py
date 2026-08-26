"""
server.py
=========
DUEL/1.0 server. Accepts exactly two TCP client connections, runs a small
lobby handshake (HELLO -> CHOOSE -> READY), then referees turn-based combat
between them using game_engine.py, printing every request received and
every response sent (with status code + phrase) to the console.

Usage:
    python3 server.py [port]        (default port 5050)
"""

import socket
import sys
import threading

import protocol
import game_engine as ge

HOST = "0.0.0.0"
DEFAULT_PORT = 5050


class PlayerConn:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.file = sock.makefile("r", encoding=protocol.ENCODING, newline="")
        self.name = None
        self.character = None
        self.lock = threading.Lock()

    def send(self, code, headers=None):
        msg = protocol.encode_response(code, headers)
        print(f"[SERVER -> {self.name or self.addr}] {msg.strip()}")
        self.sock.sendall(msg.encode(protocol.ENCODING))

    def recv_request(self):
        line = self.file.readline()
        if not line:
            return None
        print(f"[{self.name or self.addr} -> SERVER] {line.strip()}")
        return protocol.decode_request(line)


def lobby_phase(p1: PlayerConn, p2: PlayerConn):
    """HELLO + CHOOSE + READY handshake for both players."""
    for p in (p1, p2):
        verb, args = p.recv_request()
        if verb != "HELLO" or not args:
            p.send(400, {"Reason": "Expected HELLO <name>"})
            raise ConnectionAbortedError("bad handshake")
        p.name = args[0]
        p.send(200, {"Welcome": p.name})

    for p in (p1, p2):
        verb, args = p.recv_request()
        if verb != "CHOOSE" or not args:
            p.send(400, {"Reason": "Expected CHOOSE <class> [variant]"})
            raise ConnectionAbortedError("bad choose")
        class_name = args[0]
        variant = args[1] if len(args) > 1 else None
        try:
            p.character = ge.make_character(p.name, class_name, variant)
        except ValueError as e:
            p.send(400, {"Reason": str(e)})
            raise
        p.send(200, {"Class": p.character.class_name, "HP": p.character.hp, "Mana": p.character.mana})

    for p in (p1, p2):
        verb, args = p.recv_request()
        if verb != "READY":
            p.send(400, {"Reason": "Expected READY"})
            raise ConnectionAbortedError("bad ready")
        p.send(100, {"Reason": "Waiting for opponent"})

    for p, opp in ((p1, p2), (p2, p1)):
        p.send(201, {
            "You": f"{p.character.class_name}",
            "Opponent": f"{opp.name}:{opp.character.class_name}",
            "FirstTurn": p1.name,
        })


def broadcast_turn_result(p1, p2, actor, target, verb, args, result, log):
    """Send the TURN_RESOLVED / NO_EFFECT / etc. response to BOTH players
    so each side sees the full public game state."""
    headers = {
        "Actor": actor.owner_name,
        "Target": target.owner_name,
        "Verb": verb,
        "Args": " ".join(args),
        "Damage": result["damage"],
        "StatusApplied": ",".join(result["status_applied"]) or "NONE",
        "ActorHP": actor.hp,
        "ActorMana": actor.mana,
        "TargetHP": target.hp,
        "TargetMana": target.mana,
        "ActorStatuses": actor.status_summary(),
        "TargetStatuses": target.status_summary(),
        "Note": result["note"],
    }
    for p in (p1, p2):
        p.send(result["code"], headers)
    for line in log:
        print(f"[GAME] {line}")


def combat_loop(p1: PlayerConn, p2: PlayerConn):
    turn_order = [p1, p2]
    turn_idx = 0

    while True:
        acting = turn_order[turn_idx]
        waiting = turn_order[1 - turn_idx]
        actor_char = acting.character
        target_char = waiting.character

        # --- upkeep: tick DOT / stun statuses for the acting character ---
        log = []
        must_skip = actor_char.tick_statuses(log)
        if log:
            headers = {
                "Actor": actor_char.owner_name,
                "ActorHP": actor_char.hp,
                "ActorStatuses": actor_char.status_summary(),
                "Note": "; ".join(log),
            }
            for p in (p1, p2):
                p.send(202, headers)
            for line in log:
                print(f"[GAME] {line}")

        if not actor_char.alive:
            winner = waiting.name
            for p in (p1, p2):
                p.send(210, {"Winner": winner, "Reason": f"{actor_char.owner_name} has fallen"})
            print(f"[GAME] {winner} wins! ({actor_char.owner_name} was defeated by status damage)")
            return

        if must_skip:
            turn_idx = 1 - turn_idx
            continue

        # --- request an action from the acting player -----------------
        acting.send(100, {"Reason": "Your turn", "YourHP": actor_char.hp, "YourMana": actor_char.mana})
        waiting.send(100, {"Reason": f"Waiting for {acting.name}"})

        req = acting.recv_request()
        if req is None:
            winner = waiting.name
            for p in (p1, p2):
                try:
                    p.send(210, {"Winner": winner, "Reason": "opponent disconnected"})
                except OSError:
                    pass
            print(f"[GAME] {acting.name} disconnected. {winner} wins by default.")
            return
        verb, args = req

        if verb == "QUIT":
            winner = waiting.name
            for p in (p1, p2):
                p.send(210, {"Winner": winner, "Reason": f"{acting.name} forfeited"})
            print(f"[GAME] {acting.name} quit. {winner} wins.")
            return

        turn_log = []
        result = ge.resolve_action(actor_char, target_char, verb, args, turn_log)

        if result["code"] in (400, 403, 404, 409):
            acting.send(result["code"], {"Reason": result["note"]})
            continue  # same player must try again, doesn't consume the turn

        broadcast_turn_result(p1, p2, actor_char, target_char, verb, args, result, turn_log)

        if not target_char.alive:
            for p in (p1, p2):
                p.send(210, {"Winner": acting.name, "Reason": f"{target_char.owner_name} was defeated"})
            print(f"[GAME] {acting.name} wins!")
            return

        turn_idx = 1 - turn_idx


def run_server(port=DEFAULT_PORT):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(2)
    print(f"[SERVER] DUEL/1.0 server listening on port {port}. Waiting for 2 players...")

    conns = []
    while len(conns) < 2:
        sock, addr = srv.accept()
        print(f"[SERVER] Connection from {addr}")
        conns.append(PlayerConn(sock, addr))

    p1, p2 = conns
    try:
        lobby_phase(p1, p2)
        combat_loop(p1, p2)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as e:
        print(f"[SERVER] Match aborted: {e}")
    finally:
        for p in (p1, p2):
            try:
                p.sock.close()
            except OSError:
                pass
        srv.close()
        print("[SERVER] Shut down.")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run_server(port)