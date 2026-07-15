#!/bin/bash
# ============================================================
#  VEX-SCORE "autopilot" setup — run ON the Raspberry Pi:
#      bash pi-autopilot-setup.sh      (no sudo in front)
#
#  Sets up two things:
#   1. KIOSK: the Pi's own screen opens the live display
#      fullscreen automatically on every boot.
#   2. CAPTIVE PORTAL: when a phone joins the VEX-SCORE
#      WiFi, it automatically pops open the referee page —
#      no typing any address.
# ============================================================

echo "=== [1/3] Kiosk: auto-open display on the Pi's screen ==="
BROWSER=$(command -v chromium-browser || command -v chromium)
mkdir -p ~/.config/labwc
if grep -q vex-kiosk ~/.config/labwc/autostart 2>/dev/null; then
  echo "  kiosk autostart: already set"
else
  echo "sh -c 'sleep 8; $BROWSER --kiosk --noerrdialogs --disable-session-crashed-bubble http://localhost:5000/display' & # vex-kiosk" >> ~/.config/labwc/autostart
  echo "  kiosk autostart: added"
fi

echo "=== [2/3] Captive portal: DNS answers everything with the Pi ==="
sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d
sudo tee /etc/NetworkManager/dnsmasq-shared.d/vexscore-portal.conf > /dev/null << 'EOF'
# While on the VEX-SCORE hotspot, every domain resolves to the Pi
address=/#/10.42.0.1
EOF
echo "  dnsmasq portal config: written"

echo "=== [3/3] Redirect server on port 80 -> scoreboard ==="
sudo tee /home/ayden/VEX-SCORE/portal.py > /dev/null << 'EOF'
"""Captive-portal helper: answers every HTTP request with a
redirect to the scoreboard, which makes phones pop open the
referee page the moment they join the VEX-SCORE WiFi."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGET = "http://10.42.0.1:5000/"

class Redirect(BaseHTTPRequestHandler):
    def _go(self):
        self.send_response(302)
        self.send_header("Location", TARGET)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
    do_GET = _go
    do_POST = _go
    do_HEAD = _go
    def log_message(self, *args):
        pass

ThreadingHTTPServer(("0.0.0.0", 80), Redirect).serve_forever()
EOF

sudo tee /etc/systemd/system/vexscore-portal.service > /dev/null << 'EOF'
[Unit]
Description=VEX-SCORE captive portal redirect (port 80)
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/ayden/VEX-SCORE/portal.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vexscore-portal
sleep 1

echo ""
echo "==================== SUMMARY ===================="
echo "  Kiosk autostart : $(grep -q vex-kiosk ~/.config/labwc/autostart && echo OK || echo MISSING)"
echo "  Portal DNS conf : $(test -f /etc/NetworkManager/dnsmasq-shared.d/vexscore-portal.conf && echo OK || echo MISSING)"
echo "  Portal service  : $(systemctl is-active vexscore-portal)"
echo "  Scoreboard      : $(systemctl is-active vexscore)"
echo "================================================="
echo ""
echo "Now REBOOT the Pi to apply everything:  sudo reboot"
echo "After reboot: screen shows the display, and phones that"
echo "join VEX-SCORE pop the referee page automatically."
