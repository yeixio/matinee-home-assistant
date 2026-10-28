"""Constants for the Matinee integration."""

DOMAIN = "matinee"

CONF_SERVER_ID = "server_id"

# Fired on Home Assistant's event bus for everything the server reports,
# with the server's event as data (see the guide's Home Assistant page).
EVENT = "matinee_event"

# Sent with the client id when a client appears, changes or goes.
SIGNAL_CLIENT = "matinee_client_{entry_id}"
SIGNAL_NEW_CLIENT = "matinee_new_client_{entry_id}"
SIGNAL_LIBRARIES = "matinee_libraries_{entry_id}"
SIGNAL_EVENT = "matinee_server_event_{entry_id}"
SIGNAL_CONNECTION = "matinee_connection_{entry_id}"

# Events the server sends, as event entity types (dots become underscores).
EVENT_TYPES = [
    "playback_started",
    "playback_paused",
    "playback_resumed",
    "playback_stopped",
    "item_added",
    "library_scanned",
]

MEDIA_ID_PREFIX = "matinee://"
