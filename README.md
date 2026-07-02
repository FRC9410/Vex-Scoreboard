# VEX-SCORE

Live match scoring for VEX robotics events. Referees score from their phones, the audience watches a projected live display, and everything runs on a Raspberry Pi over local WiFi — **no internet required**.

Built with Python Flask, SQLite, and plain HTML/CSS/JS. Scores persist across refreshes and restarts.

## Referee & display links

> These links work for any device **on the same WiFi network as the Pi**.
> `raspberrypi.local` is the default Pi hostname — if it doesn't resolve on your network, replace it with the Pi's IP address (run `hostname -I` on the Pi), e.g. `http://192.168.1.50:5000/`.

| Page | Link | Who uses it |
|---|---|---|
| Referee home (Red/Blue buttons) | [http://raspberrypi.local:5000/](http://raspberrypi.local:5000/) | Referees |
| Red score panel | [http://raspberrypi.local:5000/panel/red](http://raspberrypi.local:5000/panel/red) | Red-side referee |
| Blue score panel | [http://raspberrypi.local:5000/panel/blue](http://raspberrypi.local:5000/panel/blue) | Blue-side referee |
| Live match display | [http://raspberrypi.local:5000/display](http://raspberrypi.local:5000/display) | Projector / audience |
| Match admin (timer, teams, resets) | [http://raspberrypi.local:5000/admin](http://raspberrypi.local:5000/admin) | Event staff |

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

The server listens on port 5000 on all interfaces. Find the Pi's address with `hostname -I`, then open the links above from any phone or laptop on the same network.

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
