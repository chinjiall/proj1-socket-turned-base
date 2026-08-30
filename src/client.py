"""
client.py
=========
DUEL/1.0 client. Connects to the server, joins the lobby, then lets the
player pick actions each turn via a simple text menu. Every request sent
and response received (status code + phrase + headers) is printed to the
console, as required by the assignment.

Usage:
    python3 client.py <server_host> [port] [name] [class] [variant]

    If name/class/variant are omitted you will be prompted interactively.
    Non-interactive args are provided mainly so the client can be scripted
    for automated demos/tests.
"""

import socket
import sys

import protocol

DEFAULT_PORT = 5050


class DuelClient:
    def __init__(self, host, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.file = self.sock.makefile("r", encoding=protocol.ENCODING, newline="")

    def send_request(self, verb, *args):
        msg = protocol.encode_request(verb, *args)
        print(f"[CLIENT -> SERVER] {msg.strip()}")
        self.sock.sendall(msg.encode(protocol.ENCODING))

    def recv_response(self):
        raw = protocol.recv_response(self.file)
        if raw is None:
            return None, None, None
        code, phrase, headers = protocol.decode_response(raw)
        print(f"[SERVER -> CLIENT] {protocol.PROTOCOL_NAME} {code} {phrase}")
        for k, v in headers.items():
            print(f"    {k}: {v}")
        return code, phrase, headers

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def prompt(text, default=None):
    val = input(text).strip()
    return val or default


def choose_action_menu(class_name, mana, statuses):
    print("\n--- Choose your action ---")
    print(f"(HP/Mana shown above. Active statuses: {statuses})")
    options = []
    if class_name.startswith("WARRIOR") or class_name == "ROGUE":
        options.append(("1", "ACTION NORMAL", "Normal Attack"))
        options.append(("2", "ACTION HEAVY", "Heavy Attack"))
        options.append(("3", "BLOCK", "Block"))
        if class_name == "ROGUE":
            options.append(("4", "ACTION POISON_IMBUE", "Poison Imbue Daggers"))
    elif class_name == "MAGE":
        options.append(("1", "ACTION SPELL FIREBALL", "Fireball (4 mana)"))
        options.append(("2", "ACTION SPELL THUNDER_SHOCK", "Thunder Shock (4 mana)"))
        options.append(("3", "ACTION SPELL EARTHQUAKE", "Earthquake (4 mana)"))
        options.append(("4", "ACTION SPELL FROSTBITE", "Frostbite (4 mana)"))
        options.append(("5", "ACTION BUFF HEAL SELF", "Heal Self (3 mana)"))
        options.append(("6", "ACTION BUFF IMBUE FIRE", "Imbue ally weapon: Fire (2 mana)"))
        options.append(("7", "ACTION BUFF IRONSKIN SELF", "Iron Skin buff (2 mana)"))
        options.append(("8", "ACTION REGEN", "Regenerate Mana"))
        options.append(("9", "BLOCK", "Block"))

    options.append(("q", "QUIT", "Forfeit / Quit match"))

    for key, _, label in options:
        print(f"  [{key}] {label}")

    choice = input("Choose: ").strip().lower()
    for key, verb_line, _ in options:
        if key == choice:
            parts = verb_line.split()
            return parts[0], parts[1:]
    print("Invalid choice, defaulting to BLOCK.")
    return "BLOCK", []


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 client.py <server_host> [port] [name] [class] [variant]")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    name = sys.argv[3] if len(sys.argv) > 3 else prompt("Your name: ", "Player")
    class_name = sys.argv[4] if len(sys.argv) > 4 else prompt("Class (WARRIOR/MAGE/ROGUE): ", "WARRIOR")
    variant = sys.argv[5] if len(sys.argv) > 5 else (
        prompt("Variant for Warrior (LIGHT/HEAVY): ", "LIGHT") if class_name.upper() == "WARRIOR" else None
    )

    client = DuelClient(host, port)

    # --- lobby handshake -------------------------------------------------
    client.send_request("HELLO", name)
    client.recv_response()

    if variant:
        client.send_request("CHOOSE", class_name, variant)
    else:
        client.send_request("CHOOSE", class_name)
    code, phrase, headers = client.recv_response()
    if code != 200:
        print("Failed to choose class, exiting.")
        client.close()
        return

    client.send_request("READY")
    client.recv_response()

    code, phrase, headers = client.recv_response()  # 201 MATCH_STARTED
    if code != 201:
        print("Match did not start as expected.")
        client.close()
        return

    print(f"\n=== MATCH STARTED === You are {headers.get('You')}, opponent is {headers.get('Opponent')}\n")

    my_hp, my_mana, my_statuses = None, None, "NONE"

    # --- main combat loop --------------------------------------------------
    while True:
        code, phrase, headers = client.recv_response()
        if code is None:
            print("Server closed the connection.")
            break

        if code == 210:  # GAME_OVER
            print(f"\n=== GAME OVER === Winner: {headers.get('Winner')} ({headers.get('Reason')})\n")
            break

        if code == 100 and headers.get("Reason") == "Your turn":
            my_hp = headers.get("YourHP")
            my_mana = headers.get("YourMana")
            verb, args = choose_action_menu(class_name.upper() if not variant else f"{class_name.upper()}", my_mana, my_statuses)
            client.send_request(verb, *args)
            resp_code, resp_phrase, resp_headers = client.recv_response()
            if resp_code in (400, 403, 404, 409):
                print(f"Action rejected: {resp_headers.get('Reason')}. You'll be asked again next loop.")
            continue

        if code == 100:
            # waiting for opponent
            continue

        # Any broadcast turn-resolution / status-tick message: update local
        # tracked state if it concerns us.
        if headers.get("Actor") == name:
            my_statuses = headers.get("ActorStatuses", my_statuses)
        elif headers.get("Target") == name:
            my_statuses = headers.get("TargetStatuses", my_statuses)

    client.close()


if __name__ == "__main__":
    main()