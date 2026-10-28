# Matinee for Home Assistant

See what's playing on every [Matinee](https://matinee.yeix.io) player in Home Assistant, control them, and automate around what you watch.

- A media player for every Matinee player: the web player, the phone and tablet apps, and the TV apps. Play, pause, seek, stop, next episode, volume, and start any title from the media browser or an automation.
- Sleep timer and subtitle actions.
- Sensors for what's playing now, each library's size, and the newest addition, and a button that scans every library.
- An **Activity** event entity and `matinee_event` bus events for playback, new titles and finished scans.
- Set up without API keys: Home Assistant finds Matinee on your network, shows a code, and you approve it in Matinee, like signing in a TV.

The [Home Assistant page of the Matinee guide](https://matinee.yeix.io/docs/server/home-assistant/) has everything: setup, actions, events and example automations.

## Install

1. In HACS, choose **Custom repositories** in the menu and add `https://github.com/yeixio/matinee-home-assistant` as an **Integration**.
2. Search HACS for **Matinee**, download it, and restart Home Assistant.
3. Matinee shows up under **Settings → Devices & services → Discovered**. Click **Add**, then approve the code on your server's `/link` page while signed in as an admin.

Needs Home Assistant 2026.1 or newer and a Matinee server with remote control (v0.1.1 or newer).

## Development

```sh
python -m venv .venv && .venv/bin/pip install -r requirements-test.txt
.venv/bin/pytest
```

The tests run Home Assistant against a pretend Matinee server (`tests/conftest.py`). The server's API is documented in `internal/api/openapi.yaml` in [yeixio/matinee](https://github.com/yeixio/matinee), and how players take commands in its `docs/remote-control.md`.

## License

Apache License 2.0, the same as Home Assistant, so the integration can move into Home Assistant itself one day.
