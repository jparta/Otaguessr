import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from mitmproxy.http import HTTPFlow, Request, Response

from trilateration import trilaterate


EVENTS_FILE = "whats_going_on.txt"
GUESSES_FILE = "guesses.parquet"
BACKUPS_DIR = "backups"

Path(BACKUPS_DIR).mkdir(exist_ok=True)

# TODO:
#  * Use perfect locations (30k score)
#  * Send estimates when 1) perfect scores not available and 2) estimate available

def valid_guess_row(row: list):
    """Validate to be
    string id, latitude, longitude, numeric score
    """
    length = 4
    if (
        len(row) == length and
        isinstance(row[0], str) and
        row[0] != "None" and
        isinstance(row[1], float) and
        -90 <= row[1] <= 90 and
        isinstance(row[2], float) and
        -180 <= row[2] <= 180 and
        isinstance(row[3], (float, int)) and
        0 <= row[3] <= 30000
    ):
        return True
    else:
        return False


class EventsOut():
    def __init__(self, filepath) -> None:
        self.filepath = filepath

    def clear(self):
        with open(self.filepath, "w"):
            pass

    def write(self, line):
        with open(self.filepath, "a") as f:
            print(line, file=f)


class Guesses():
    back_time_format = "%Y-%m-%dT%H-%M-%S-%Z"

    def __init__(
        self,
        filepath: str | Path,
        backups_dir: str | Path,
    ) -> None:
        self.filepath = Path(filepath)
        self.backups_dir = Path(backups_dir)
        self.backup_interval = timedelta(minutes=10)
        self.df = pd.read_parquet(filepath)

    def save_to_file(self, path: Path | None = None):
        if path is None:
            path = self.filepath
        self.df.to_parquet(path)

    def backup_filestem_suffix(self):
        now_utc_aware = datetime.utcnow().replace(tzinfo=timezone.utc)
        time_string = now_utc_aware.strftime(self.back_time_format)
        return f"_backup_{time_string}"

    def backup_filestem_time_parse(self, stem: str):
        time_string = stem.split("_")[-1]
        return datetime.strptime(time_string, self.back_time_format)

    def time_to_create_backup(self) -> bool:
        now = datetime.utcnow()
        times = []
        for path in self.backups_dir.iterdir():
            dt = self.backup_filestem_time_parse(path.stem)
            times.append(dt)
        if times:
            latest = max(times)
            return now - latest >= self.backup_interval
        else:
            return True

    def create_backup(self):
        filestem = self.filepath.stem
        new_stem = filestem + self.backup_filestem_suffix()
        backup_filename = self.filepath.with_stem(new_stem).name
        backup_filepath = self.backups_dir / backup_filename
        self.save_to_file(path=backup_filepath)

    def add_guess(self, guess) -> int:
        """Add guess, if valid, to the pile.
        If added (was valid), return new guess count.
        """
        if not valid_guess_row(guess):
            raise ValueError(f"Invalid guess row. Got {guess}")
        new = pd.DataFrame([guess], columns=self.df.columns)
        self.df = pd.concat([self.df, new])
        self.save_to_file()
        if self.time_to_create_backup():
            self.create_backup()
        return len(self.df)

    def estimate_true_location(self, pic: str) -> tuple | None:
        """Return estimate for location (lat, lon)
        if there are at least three previous guesses,
        otherwise return None.
        """
        guesses_df = self.df.loc[self.df.iloc[:,0] == pic]
        guesses_tuples = list(guesses_df.itertuples(index=False, name=None))
        if len(guesses_tuples) >= 3:
            estimate = trilaterate(guesses_tuples)
            return estimate
        else:
            return None


def has_json_content_type(event: Request | Response):
    content_type = event.headers.get("Content-Type")
    return content_type and content_type.startswith("application/json")


def try_read_json(flow: HTTPFlow) -> tuple:
    """Returns a pair (request_body, response_body)
    of parsed JSON, or if malformed or not present, None.
    Request and response are handled independently.
    """
    bodies = []
    for event in (flow.request, flow.response):
        if event is None or event.text is None or not has_json_content_type(event):
            bodies.append(None)
            continue
        try:
            request_text = event.text
        except ValueError:
            bodies.append(None)
            continue
        request_json = json.loads(request_text)
        bodies.append(request_json)
    return tuple(bodies)


class Guessr:
    def __init__(
        self,
        events_out: EventsOut,
        guesses: Guesses,
    ) -> None:
        self.events_out = events_out
        self.guesses = guesses
        self.host = "api.otaguessr.fi"
        self.play_path = "/api/play"
        self.answer_path = "/api/answer"
        self.paths = [self.play_path, self.answer_path]
        self.session_id_cookie_key = "connect.sid"
        # Game state maps session ID (game ID) to current picture (question / challenge)
        self.game_state: dict[str, str] = {}
        # Clear output
        self.events_out.clear()

    def response(self, flow: HTTPFlow):
        if flow.request.pretty_host != self.host:
            return
        self.events_out.write("-------")
        self.events_out.write(f"Response: {flow.request.pretty_url}")
        self.events_out.write(f"Method: {flow.request.method}")
        if flow.response:
            session_cookie = flow.response.cookies.get(self.session_id_cookie_key)
            session_id = session_cookie[0] if session_cookie else None
        else:
            session_id = None
        if session_id is None:
            self.events_out.write(f"No session id cookie by key '{self.session_id_cookie_key}'")
            return
        self.events_out.write(f"{session_id = }")
        if flow.request.path == self.play_path:
            self.handle_play_response(flow, session_id)
        elif flow.request.path == self.answer_path:
            self.handle_answer_response(flow, session_id)
        else:
            self.events_out.write("No path match")

    def handle_play_response(self, flow: HTTPFlow, session_id: str):
        _, response_json = try_read_json(flow)
        # First picture's id
        if response_json and isinstance(response_json, dict):
            picture_id = response_json.get("name")
        else:
            picture_id = None
        if picture_id:
            self.events_out.write(f"{picture_id = }")
            self.game_state[session_id] = picture_id

    def handle_answer_response(self, flow: HTTPFlow, session_id: str):
        current_picture_id = self.game_state.get(session_id)
        if current_picture_id is None:
            self.events_out.write("No current image found by session id")
        request_json, response_json = try_read_json(flow)
        # Coordinates in answer
        if request_json and isinstance(request_json, dict):
            answer_lat = request_json.get("lat")
            answer_lon = request_json.get("lon")
        else:
            answer_lat = answer_lon = None
        # Information given in response
        if response_json and isinstance(response_json, dict):
            score = response_json.get("score")
            picture_id = response_json.get("nextPicture")
        else:
            score = picture_id = None
        guess = (current_picture_id, answer_lat, answer_lon, score)
        to_spreadsheet = "\t".join(map(str, guess))
        self.events_out.write(to_spreadsheet)
        if current_picture_id is not None:
            new_count = self.guesses.add_guess(guess)
            self.events_out.write(f"guess count: {new_count}")
        if picture_id:
            self.game_state[session_id] = picture_id
            self.events_out.write(f"New picture id: {picture_id}")
        else:
            self.events_out.write("No new picture id given in response, game is over")

    def request(self, flow):
        if flow.request.pretty_host != self.host:
            return
        self.events_out.write("-------")
        self.events_out.write(f"Request: {flow.request.pretty_url}")
        self.events_out.write(f"Method: {flow.request.method}")
        session_id = flow.request.cookies.get(self.session_id_cookie_key)
        if session_id is None:
            self.events_out.write(f"No session id cookie by key '{self.session_id_cookie_key}'")
            return
        self.events_out.write(f"{session_id = }")
        current_picture_id = self.game_state.get(session_id)
        if current_picture_id is None:
            self.events_out.write("No current picture found by session id")
            return
        self.events_out.write(f"{current_picture_id = }")
        location_estimate = self.guesses.estimate_true_location(current_picture_id)
        self.events_out.write(f"{location_estimate = }")


events_out = EventsOut(EVENTS_FILE)
guesses = Guesses(GUESSES_FILE, BACKUPS_DIR)
addons = [Guessr(events_out, guesses)]
