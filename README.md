# VEX-SCORE

Live match scoring for VEX robotics events. Referees score from their phones, the audience watches a projected live display, and everything runs on a Raspberry Pi over local WiFi — **no internet required**.

Built with Python Flask, SQLite, and plain HTML/CSS/JS. Scores persist across refreshes and restarts.

## Referee & display links

> First join the Pi's own WiFi network (**VEX-SCORE** — setup below), then these links just work.
> On its own network the Pi is always `10.42.0.1`.

| Page | Link | Who uses it |
|---|---|---|
| Referee home (Red/Blue buttons) | [http://10.42.0.1:5000/](http://10.42.0.1:5000/) | Referees |
| Red score panel | [http://10.42.0.1:5000/panel/red](http://10.42.0.1:5000/panel/red) | Red-side referee |
| Blue score panel | [http://10.42.0.1:5000/panel/blue](http://10.42.0.1:5000/panel/blue) | Blue-side referee |
| Live match display | [http://10.42.0.1:5000/display](http://10.42.0.1:5000/display) | Projector / audience |
| Match admin (timer, teams, resets) | [http://10.42.0.1:5000/admin](http://10.42.0.1:5000/admin) | Event staff |

## Event WiFi: the Pi makes its own network

School and venue WiFi usually has **client isolation** (devices can't talk to each other), so don't fight it — the Pi broadcasts its own hotspot and the phones + projector laptop join that instead. One-time setup on Raspberry Pi OS (Bookworm or newer):

```bash
# create a WiFi network named VEX-SCORE (password: vexscore123)
sudo nmcli device wifi hotspot ssid VEX-SCORE password vexscore123

# make it come back automatically on every boot
sudo nmcli connection modify Hotspot connection.autoconnect yes connection.autoconnect-priority 100
```

Pick any SSID/password you like (password needs 8+ characters). While hosting the hotspot the Pi's WiFi has no internet — the scoreboard doesn't need any.

**Match day:** power the Pi → the VEX-SCORE network appears → refs and the projector laptop join it → open the links above.

## Pages

- **`/`** — referee home: two big buttons, Red Team and Blue Team.
- **`/panel/red`, `/panel/blue`** — giant − / + buttons around the current score. Every tap updates the live display within a second; no submit button. Back button returns home.
- **`/display`** — projector view: match number, countdown timer, both team names and scores in huge text, penalties per team (display-only for now). Refreshes every second.
- **`/admin`** — start/pause/reset the match timer, set match number, team names, match length (default 2:00), and reset scores between matches.

## Running on the Raspberry Pi

```bash
sudo apt install python3-flask     # or: pip3 install -r requirements.txt
python3 app.py
```

The server listens on port 5000 on all interfaces. On the hotspot the Pi is always `http://10.42.0.1:5000`. (If you put the Pi on an existing network instead, find its address with `hostname -I` and use that in the links — but beware client isolation on managed WiFi.)

`scores.db` (the SQLite database) is created automatically next to `app.py` on first run.

### Optional: start automatically on boot

Create `/etc/systemd/system/vexscore.service`:

```ini
[Unit]
Description=VEX-SCORE match scoring app
After=network.target

[Service]
WorkingDirectory=/home/pi/VEX-SCORE
ExecStart=/usr/bin/python3 /home/pi/VEX-SCORE/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then: `sudo systemctl enable --now vexscore`

## API

All state lives in a single SQLite row; every page polls `GET /api/state` once per second.

| Endpoint | Method | Body | Purpose |
|---|---|---|---|
| `/api/state` | GET | — | Full current state (scores, teams, penalties, timer) |
| `/api/adjust` | POST | `{"team": "red"\|"blue", "delta": 1\|-1}` | Bump a score (clamped at 0) |
| `/api/timer` | POST | `{"action": "start"\|"pause"\|"reset"}` | Control the countdown |
| `/api/setup` | POST | `{"match_number", "red_team", "blue_team", "timer_duration", "reset_scores"}` (all optional) | Match setup / reset |

The timer is stored as an end-timestamp on the server, so every screen shows the same clock no matter when it connects.
