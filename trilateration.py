from math import pow, log, exp
from collections.abc import Iterable, Sequence

from geopy.distance import distance as distance_func
from scipy.optimize import minimize


def score_to_distance(score: int | float) -> float:
    """Distance from the real answer.
    Derived by finding best fit using different models.
    """
    if score < 0 or score > 30000:
        raise ValueError("score has to be between 0 and 30000")
    a = 30000
    b = -0.005
    return log(score / a) / b


def distance_to_score(distance: int | float):
    """Score gotten when at a certain distance from the real answer.
    Derived by finding best fit using different models.
    """
    if distance < 0:
        raise ValueError("Distance has to be 0 or positive")
    a = 30000
    b = -0.005
    return a * exp(b * distance)


def mse(
    x: tuple[float, float],
    locations: Iterable[tuple[float, float]],
    distances: Sequence[float],
) -> float:
    """Mean squared error for optimization"""
    mse = 0.0
    for location, distance in zip(locations, distances):
        distance_calculated = distance_func(x, location).meters
        mse += pow(distance_calculated - distance, 2.0)
    return mse / len(distances)


def trilaterate(guesses: list | tuple) -> tuple[float, float]:
    """Find the real location by trilateration.
    Takes guesses of form (pic, lat, lon, score).
    """
    locations = []
    distances = []
    for guess in guesses:
        coords = (guess[1], guess[2])
        score = guess[3]
        locations.append(coords)
        d = score_to_distance(score)
        distances.append(d)
    initial_location = min(zip(distances, locations), key=lambda x: x[0])[1]
    result = minimize(
        mse,
        initial_location,
        args=(locations, distances),
        method="L-BFGS-B",
        options={"ftol": 1e-5, "maxiter": 1e7},
    )
    estimated_location = tuple(result.x)
    return estimated_location
