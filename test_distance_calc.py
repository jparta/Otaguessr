from math import pow, log

from geopy.distance import distance as distance_func
from scipy.optimize import minimize

# Startup Sauna
points = [
    ("d6d73e4d84c92f8c5fff4340a5dce12f", 60.18498637, 24.83608603, 29395),
    ("d6d73e4d84c92f8c5fff4340a5dce12f", 60.18466897, 24.83625233, 24566),
    ("d6d73e4d84c92f8c5fff4340a5dce12f", 60.18432798, 24.83515263, 19105),
    ("d6d73e4d84c92f8c5fff4340a5dce12f", 60.18508505, 24.83578026, 27669),
]

def distance_from_truth(score: int | float):
    if score < 0 or score > 30000:
        raise ValueError("score has to be between 0 and 30000")
    a = 30000
    b = -0.005
    return log(score / a) / b

def mse(x, locations, distances):
    mse = 0.0
    for location, distance in zip(locations, distances):
        distance_calculated = distance_func(x, location).meters
        mse += pow(distance_calculated - distance, 2.0)
    return mse / len(distances)

locations = []
distances = []
for point in points[1:]:
    coords = (point[1], point[2])
    score = point[3]
    locations.append(coords)
    d = distance_from_truth(score)
    distances.append(d)

print(f"{locations = }")
print(f"{distances = }")

initial_location = min(zip(distances, locations), key=lambda x: x[0])[1]
print(f"{initial_location = }")

result = minimize(
    mse,
    initial_location,
    args=(locations, distances),
    method='L-BFGS-B',
    options={
        'ftol':1e-5,
        'maxiter': 1e+7
    })
estimated_location = result.x

print(f"{estimated_location = }")
