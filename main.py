import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pformat

import pandas as pd
from geopy.distance import GeodesicDistance
from mitmproxy.http import HTTPFlow, Request, Response

from trilateration import trilaterate, distance_to_score


EVENTS_FILE = "whats_going_on.txt"
GUESSES_FILE = "guesses.parquet"
BACKUPS_DIR = "backups"

Path(BACKUPS_DIR).mkdir(exist_ok=True)

# TODO:
#  * If trilateration doesn't produce a perfect score, the data could be poisoned by a manual data entry mistake. Delete existing data points. Alternatively, develop trilateration further by picking the highest performing three points.
#  * Only allow GET and POST
#  * Create guess class so that guess elements are not accessed by index
#  * Store score range in a constant to reduce magic numbers
#  * Capture each picture
#  * See if there are duplicate locations on different pics


def valid_guess_row(row: tuple | list) -> bool:
    """Validate to be
    string id, latitude, longitude, numeric score
    """
    length = 4
    if (
        isinstance(row, (tuple, list))
        and len(row) == length
        and isinstance(row[0], str)
        and row[0] != "None"
        and isinstance(row[1], float)
        and -90 <= row[1] <= 90
        and isinstance(row[2], float)
        and -180 <= row[2] <= 180
        and isinstance(row[3], (float, int))
        and 0 <= row[3] <= 30000
    ):
        return True
    else:
        return False


class EventsOut:
    def __init__(self, filepath: str | Path) -> None:
        self.filepath = filepath

    def clear(self) -> None:
        with open(self.filepath, "w"):
            pass

    def write(self, line: str) -> None:
        with open(self.filepath, "a") as f:
            print(line, file=f)


class Guesses:
    backup_time_format = "%Y-%m-%dT%H-%M-%S-%Z"

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
        time_string = now_utc_aware.strftime(self.backup_time_format)
        return f"_backup_{time_string}"

    def backup_filestem_time_parse(self, stem: str):
        time_string = stem.split("_")[-1]
        return datetime.strptime(time_string, self.backup_time_format)

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

    def create_backup(self) -> None:
        filestem = self.filepath.stem
        new_stem = filestem + self.backup_filestem_suffix()
        backup_filename = self.filepath.with_stem(new_stem).name
        backup_filepath = self.backups_dir / backup_filename
        self.save_to_file(path=backup_filepath)

    def total_guesses(self):
        return len(self.df)

    def get_guesses(self, pic: str) -> list[tuple]:
        guesses_df = self.df.loc[self.df.iloc[:, 0] == pic]
        guesses_tuples = list(guesses_df.itertuples(index=False, name=None))
        return guesses_tuples

    def has_perfect_guess(self, pic: str) -> bool:
        guesses = self.get_guesses(pic)
        return any(guess[3] == 30000 for guess in guesses)

    def add_guess(self, guess: tuple | list) -> None:
        """Add guess, if valid, to the pile."""
        if not valid_guess_row(guess):
            raise ValueError(f"Invalid guess row. Got {guess}")
        new = pd.DataFrame([guess], columns=self.df.columns)
        self.df = pd.concat([self.df, new])
        self.save_to_file()
        if self.time_to_create_backup():
            self.create_backup()

    def estimate_true_location(self, pic: str) -> tuple[float, float] | None:
        """Return estimate for location (lat, lon)
        if there are at least three previous guesses,
        otherwise return None.
        """
        guesses = self.get_guesses(pic)
        # Check for perfect scores
        for guess in guesses:
            score = guess[3]
            if score == 30000:
                lat = guess[1]
                lon = guess[2]
                return (lat, lon)
        # No perfect score yet, estimate
        if len(guesses) >= 3:
            estimate = trilaterate(guesses)
            return estimate
        else:
            return None


def has_json_content_type(event: Request | Response) -> bool:
    content_type = event.headers.get("Content-Type")
    return (
        content_type is not None
        and isinstance(content_type, str)
        and content_type.startswith("application/json")
    )


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


def replace_request_json(flow: HTTPFlow, body: dict) -> None:
    """Replaces the current request body with the given dict in JSON format."""
    if (
        flow.request is None
        or flow.request.text is None
        or not has_json_content_type(flow.request)
    ):
        ValueError(
            "Not able to write JSON. Request or body not available, or content type not set."
        )
    request_text = json.dumps(body)
    flow.request.text = request_text


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
        self.current_pic: str | None = None
        # Clear output
        self.events_out.clear()

    def output_next_pic_info(self, pic: str):
        self.events_out.write(f"Next picture id: {pic}")
        guess_count = len(self.guesses.get_guesses(pic))
        new_counts = (guess_count, self.guesses.total_guesses())
        self.events_out.write(f"guess count (pic, total): {new_counts}")
        existing_guesses = self.guesses.get_guesses(pic)
        guesses_pretty = pformat(existing_guesses)
        self.events_out.write("existing guesses:")
        self.events_out.write(guesses_pretty)
        location_estimate = self.guesses.estimate_true_location(pic)
        self.events_out.write(f"{location_estimate = }")

    def response(self, flow: HTTPFlow) -> None:
        if flow.request.pretty_host != self.host:
            return
        self.events_out.write("-------")
        self.events_out.write(f"Response: {flow.request.pretty_url}")
        self.events_out.write(f"Method: {flow.request.method}")
        if flow.request.path == self.play_path:
            self.handle_play_response(flow)
        elif flow.request.path == self.answer_path:
            self.handle_answer_response(flow)
        else:
            self.events_out.write("No path match")

    def handle_play_response(self, flow: HTTPFlow) -> None:
        _, response_json = try_read_json(flow)
        # First picture's id
        if response_json and isinstance(response_json, dict):
            picture_id = response_json.get("name")
        else:
            picture_id = None
        if picture_id:
            self.current_pic = picture_id
            self.output_next_pic_info(picture_id)

    def handle_answer_response(self, flow: HTTPFlow) -> None:
        if self.current_pic is None:
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
            new_picture_id = response_json.get("nextPicture")
        else:
            score = new_picture_id = None
        guess = (self.current_pic, answer_lat, answer_lon, score)
        to_spreadsheet = "\t".join(map(str, guess))
        self.events_out.write(to_spreadsheet)
        if guess[0] is not None and not self.guesses.has_perfect_guess(guess[0]):
            self.guesses.add_guess(guess)
        if new_picture_id:
            self.current_pic = new_picture_id
            self.output_next_pic_info(new_picture_id)
        else:
            self.events_out.write("No new picture id given in response, game is over")
            # Don't carry current pic over to next game
            self.current_pic = None

    def replace_body_with_estimate(
        self, flow: HTTPFlow, location_estimate: tuple[float, float]
    ):
        # Replace answer body with good estimate
        old, _ = try_read_json(flow)
        new_lat, new_lon = location_estimate
        new_body = {"lat": new_lat, "lon": new_lon}
        replace_request_json(flow, new_body)
        old_lat, old_lon = old["lat"], old["lon"]
        likely_distance_of_old = GeodesicDistance(
            (old_lat, old_lon), (new_lat, new_lon)
        ).meters
        old_formatted = f"({old_lat:.6f}, {old_lon:.6f})"
        new_formatted = f"({new_lat:.6f}, {new_lon:.6f})"
        likely_points_of_old = distance_to_score(likely_distance_of_old)
        self.events_out.write("Replaced answer location")
        self.events_out.write(
            f"was: {old_formatted}, likely with {likely_distance_of_old:.1f} meters distance and a score of {likely_points_of_old:.0f}"
        )
        self.events_out.write(
            f"new: {new_formatted}, likely with close to 0 meters distance and a score of {distance_to_score(0):.0f}"
        )

    def request(self, flow: HTTPFlow) -> None:
        if flow.request.pretty_host != self.host:
            return
        self.events_out.write("-------")
        self.events_out.write(f"Request: {flow.request.pretty_url}")
        self.events_out.write(f"Method: {flow.request.method}")
        if self.current_pic is None:
            self.events_out.write("No current picture")
            return
        self.events_out.write(f"{self.current_pic = }")
        location_estimate = self.guesses.estimate_true_location(self.current_pic)
        if location_estimate:
            self.replace_body_with_estimate(flow, location_estimate)


events_out = EventsOut(EVENTS_FILE)
guesses = Guesses(GUESSES_FILE, BACKUPS_DIR)
addons = [Guessr(events_out, guesses)]
