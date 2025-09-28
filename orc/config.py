import os

BASE_URL = os.getenv("BASE_URL")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN")
SUNRISE_URL = os.getenv("SUNRISE_URL")

NAME_TO_HUBITAT = {
    "night light": "BEDROOM_NIGHT_LIGHT",
    "entrance desk lamp": "ENTANCE_DESK_LAMP",
    "kitchen lights": "KITCHEN_LIGHTS",
    "living room desk lamp": "LIVING_ROOM_DESK_LAMP",
    "living room floor lamp": "LIVING_ROOM_FLOOR_LAMP",
    "office desk lamp": "OFFICE_DESK_LAMP",
    "office floor lamp": "OFFICE_FLOOR_LAMP",
}
