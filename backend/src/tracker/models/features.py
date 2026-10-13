import numpy as np
import pandas as pd

WP_FEATURES = [
    "inning",
    "is_bottom",
    "score_diff",
    "outs",
    "on_1b",
    "on_2b",
    "on_3b",
    "balls",
    "strikes",
]
WP_MONOTONIC = [0, 0, 1, 0, 0, 0, 0, 0, 0]

PITCH_FEATURES = [
    "start_speed",
    "spin_rate",
    "spin_axis",
    "pfx_x",
    "pfx_z",
    "ivb",
    "hb",
    "x0",
    "z0",
    "extension",
    "is_lhp",
]

PITCH_COLUMNS = [
    "start_speed",
    "spin_rate",
    "spin_direction",
    "pfx_x",
    "pfx_z",
    "ivb",
    "hb",
    "x0",
    "z0",
    "extension",
    "pitch_hand",
]


def wp_frame(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "inning": df["inning"].astype(int).clip(1, 12),
            "is_bottom": (df["half"] == "bottom").astype(int),
            "score_diff": (df["home_score"] - df["away_score"]).astype(int).clip(-10, 10),
            "outs": df["outs"].astype(int).clip(0, 2),
            "on_1b": df["on_1b"].astype(int),
            "on_2b": df["on_2b"].astype(int),
            "on_3b": df["on_3b"].astype(int),
            "balls": df["balls"].astype(int).clip(0, 3),
            "strikes": df["strikes"].astype(int).clip(0, 2),
        },
        index=df.index,
    )


def pitch_frame(df: pd.DataFrame) -> pd.DataFrame:
    lhp = (df["pitch_hand"] == "L").to_numpy()
    sign = np.where(lhp, -1.0, 1.0)
    spin = pd.to_numeric(df["spin_direction"], errors="coerce").to_numpy(dtype=float)
    return pd.DataFrame(
        {
            "start_speed": pd.to_numeric(df["start_speed"], errors="coerce"),
            "spin_rate": pd.to_numeric(df["spin_rate"], errors="coerce"),
            "spin_axis": np.where(lhp, 360.0 - spin, spin),
            "pfx_x": pd.to_numeric(df["pfx_x"], errors="coerce") * sign,
            "pfx_z": pd.to_numeric(df["pfx_z"], errors="coerce"),
            "ivb": pd.to_numeric(df["ivb"], errors="coerce"),
            "hb": pd.to_numeric(df["hb"], errors="coerce") * sign,
            "x0": pd.to_numeric(df["x0"], errors="coerce") * sign,
            "z0": pd.to_numeric(df["z0"], errors="coerce"),
            "extension": pd.to_numeric(df["extension"], errors="coerce"),
            "is_lhp": lhp.astype(int),
        },
        index=df.index,
    )


def re_key(outs: int, on_1b: bool, on_2b: bool, on_3b: bool, balls: int, strikes: int) -> str:
    return f"{outs}{int(on_1b)}{int(on_2b)}{int(on_3b)}-{balls}{strikes}"


def re24_key(outs: int, on_1b: bool, on_2b: bool, on_3b: bool) -> str:
    return f"{outs}{int(on_1b)}{int(on_2b)}{int(on_3b)}"
