import json
from dataclasses import dataclass
from pprint import pprint

from mitmproxy import ctx, exceptions
from mitmproxy.addons.modifyheaders import parse_modify_spec, ModifySpec


@dataclass(frozen=True)
class Coordinates:
    lat: float
    lon: float


class Question:
    def __init__(self, id) -> None:
        self.id = id
        self.answers: set[Coordinates] = set()

    def add_answer(self, coords: Coordinates):
        if not self.has_triangle():
            self.answers.add(coords)

    def has_triangle(self):
        return len(self.answers) >= 3


def parse_cookies(cookie_string: str) -> dict[str, str]:
    """
    Parses a cookie string into a cookie dict.
    """
    cookies = {}
    for c in cookie_string.split(";"):
        c = c.strip()
        if c:
            k, v = c.split("=", 1)
            cookies[k] = v
    return cookies


class Guessr:
    def __init__(self) -> None:
        self.save_file = "dump.txt"
        self.host = "api.otaguessr.fi"
        self.paths = ["/api/play", "/api/answer"]
        self.session_id_cookie_key = "connect.sid"
        # Game state maps session ID (game ID) to current image (question / challenge)
        self.game_state: dict[str, str] = {}
        # Clear output
        with open(self.save_file, "w"):
            pass

    def response(self, flow):
        if flow.request.pretty_host != self.host:
            return
        if (flow.request.path not in self.paths):
            return
        _req_cookies_str = flow.request.headers.get("cookie", "")
        req_cookies = parse_cookies(_req_cookies_str)
        with open(self.save_file, "a") as f:
            print(flow.request.pretty_url, file=f)
            if flow.request.content:
                print(flow.request.text, file=f)
            if flow.response.content:
                print(flow.response.text, file=f)
            pprint(req_cookies, stream=f)
            print("-------", file=f)
        session_id = req_cookies.get(self.session_id_cookie_key)
        if session_id is None:
            return



addons = [Guessr()]
