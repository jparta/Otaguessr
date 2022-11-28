from collections import Counter
from pprint import pprint

import pandas as pd
import xlwings as xw

from main import valid_guess_row, GUESSES_FILE

xlsx_filepath = r"C:\Users\jopa0\OneDrive\Spreadsheets\Otaguessr.xlsx"
summary_sheet = "Distance to score and v.v."
table_upper_left = "B3"
column_names = ["pic", "lat", "lon", "score"]


def add_guesses_to_df(df: pd.DataFrame, rows: list[list]) -> pd.DataFrame:
    """Add valid guesses to dataframe, returning the new df"""

    def valid_run(rows: list[list]):
        firsts = [row[0] for row in rows]
        return len(set(firsts)) == 1

    valid_runs = []
    current_run = []
    for row in rows:
        if valid_guess_row(row):
            current_run.append(row)
        else:
            if valid_run(current_run):
                valid_runs.append(current_run)
            current_run = []
    if valid_run(current_run):
        valid_runs.append(current_run)

    # Join lists
    valid_guesses = []
    for run in valid_runs:
        valid_guesses.extend(run)
    for guess in valid_guesses:
        print(f"{guess = }")

    new_guesses_df = pd.DataFrame(valid_guesses)
    return pd.concat([df, new_guesses_df])


def get_from_excel() -> pd.DataFrame:
    df_guesses = pd.DataFrame()
    print(df_guesses)
    with xw.Book(xlsx_filepath) as book:
        sheet: xw.Sheet
        for sheet in book.sheets:
            if sheet.name == summary_sheet:
                continue
            guesses = sheet[table_upper_left].expand().value
            if any(not isinstance(e, list) for e in guesses):
                guesses = [guesses]
            df_guesses = add_guesses_to_df(df_guesses, guesses)
            df_guesses = df_guesses.drop_duplicates()
    print(f"{len(df_guesses)}")
    df_guesses.columns = column_names  # type: ignore
    return df_guesses


if __name__ == "__main__":
    df = get_from_excel()
    df.to_parquet(GUESSES_FILE)
