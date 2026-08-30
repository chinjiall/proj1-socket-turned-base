"""
protocol.py
============
Implementation of DUEL/1.0 — a text-based, request/response Application-Layer
protocol for a turn-based RPG combat game, running over TCP.

Design philosophy: modeled loosely on HTTP so that every exchange has a
verb-based request and a status-coded response, both of which are
human-readable and easy to print/log (a requirement of the assignment).

--------------------------------------------------------------------------
REQUEST FORMAT (Client -> Server)
--------------------------------------------------------------------------
    VERB arg1 arg2 ... argN\r\n

Examples:
    HELLO Chino
    CHOOSE WARRIOR LIGHT
    READY
    ACTION NORMAL
    ACTION HEAVY
    ACTION BLOCK
    ACTION SPELL FIREBALL
    ACTION BUFF HEAL SELF
    ACTION BUFF IMBUE FIRE
    ACTION REGEN
    QUIT

--------------------------------------------------------------------------
RESPONSE FORMAT (Server -> Client)
--------------------------------------------------------------------------
    DUEL/1.0 <code> <phrase>\r\n
    Key: Value\r\n
    Key: Value\r\n
    \r\n                      (blank line terminates the message, like HTTP)

Example:
    DUEL/1.0 202 TURN_RESOLVED
    Actor: Chino
    Action: HEAVY
    Damage: 4
    Target: Belle
    TargetHP: 21
    StatusApplied: BLEEDING

--------------------------------------------------------------------------
STATUS CODE TABLE
--------------------------------------------------------------------------
1xx  Informational / waiting
    100 CONTINUE            - request accepted, waiting on something else
                               (e.g. waiting for opponent to connect/ready)

2xx  Success
    200 OK                  - simple ack (e.g. HELLO accepted)
    201 MATCH_STARTED       - both players ready, combat begins
    202 TURN_RESOLVED       - an action was legally applied, state included
    204 NO_EFFECT           - action resolved but did nothing (e.g. missed,
                               blocked, dodged, resisted)
    210 GAME_OVER           - match has concluded, winner included

4xx  Client Error
    400 BAD_REQUEST         - malformed / unparsable message
    401 NOT_YOUR_TURN       - action sent out of turn order
    403 INVALID_ACTION      - illegal action (not enough mana, unknown
                               action for this class, stunned, etc.)
    404 UNKNOWN_TARGET      - target does not exist
    409 CONFLICT            - status effect prerequisite not met / already
                               applied in an incompatible way

5xx  Server Error
    500 SERVER_ERROR        - unexpected server-side failure
--------------------------------------------------------------------------
"""

import socket

# ---------------------------------------------------------------------------
# Status code / phrase table
# ---------------------------------------------------------------------------
STATUS_PHRASES = {
    100: "CONTINUE",
    200: "OK",
    201: "MATCH_STARTED",
    202: "TURN_RESOLVED",
    204: "NO_EFFECT",
    210: "GAME_OVER",
    400: "BAD_REQUEST",
    401: "NOT_YOUR_TURN",
    403: "INVALID_ACTION",
    404: "UNKNOWN_TARGET",
    409: "CONFLICT",
    500: "SERVER_ERROR",
}

PROTOCOL_NAME = "DUEL/1.0"
ENCODING = "utf-8"
TERMINATOR = "\r\n"
BLANK = "\r\n"


class ProtocolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Request (Client -> Server) encode / decode
# ---------------------------------------------------------------------------
def encode_request(verb, *args):
    """Build a request line, e.g. encode_request('ACTION', 'HEAVY') ->
    'ACTION HEAVY\r\n'"""
    parts = [verb] + [str(a) for a in args]
    return " ".join(parts) + TERMINATOR


def decode_request(line: str):
    """Parse a single request line into (verb, [args])."""
    line = line.strip("\r\n")
    if not line:
        raise ProtocolError("empty request")
    tokens = line.split()
    verb = tokens[0].upper()
    args = tokens[1:]
    return verb, args


# ---------------------------------------------------------------------------
# Response (Server -> Client) encode / decode
# ---------------------------------------------------------------------------
def encode_response(code: int, headers: dict = None):
    """Build a full response message:
    DUEL/1.0 <code> <phrase>\r\n
    Key: Value\r\n
    ...
    \r\n
    """
    phrase = STATUS_PHRASES.get(code, "UNKNOWN")
    lines = [f"{PROTOCOL_NAME} {code} {phrase}"]
    if headers:
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
    lines.append("")  # blank line terminator
    return TERMINATOR.join(lines) + TERMINATOR


def decode_response(raw: str):
    """Parse a full response message (status line + headers) into
    (code, phrase, headers_dict)."""
    lines = raw.strip(TERMINATOR).split(TERMINATOR)
    if not lines:
        raise ProtocolError("empty response")
    status_line = lines[0].split()
    if len(status_line) < 3 or status_line[0] != PROTOCOL_NAME:
        raise ProtocolError(f"bad status line: {lines[0]!r}")
    code = int(status_line[1])
    phrase = status_line[2]
    headers = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return code, phrase, headers


# ---------------------------------------------------------------------------
# Socket helpers — read exactly one message terminated by a blank line
# (for responses) or a single line (for requests)
# ---------------------------------------------------------------------------
def send_line(sock: socket.socket, text: str):
    sock.sendall(text.encode(ENCODING))


def recv_request_line(sock_file):
    """Read a single request line from a buffered socket file object."""
    line = sock_file.readline()
    if not line:
        return None  # connection closed
    return line


def recv_response(sock_file):
    """Read a full response (status line + headers until blank line)."""
    lines = []
    while True:
        line = sock_file.readline()
        if not line:
            return None  # connection closed
        if line.strip("\r\n") == "":
            break
        lines.append(line.rstrip("\r\n"))
    return TERMINATOR.join(lines) + TERMINATOR