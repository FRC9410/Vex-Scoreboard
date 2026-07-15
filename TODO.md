# TODO

Things that need the Pi powered on and switched to `this_LAN_is_my_LAN`
(network icon, top-right) so it can be reached at `192.168.1.93`.

## Rework or remove the captive portal

- Remove: `sudo rm /etc/NetworkManager/dnsmasq-shared.d/vexscore-portal.conf`
  (this is the "every address goes to the Pi" DNS rule)
- Disable the redirect server: `sudo systemctl disable --now vexscore-portal`
- Reboot or re-up the hotspot for the DNS change to take effect

After removal, refs reach the scoreboard by typing `10.42.0.1:5000` in a browser
(consider printing QR-code cards as the replacement for zero-typing).

## Update README.md
- Hotspot password is now `powerhouse` (README still says `vexscore123`)
- Document the kiosk display (auto-fullscreen on the Pi's screen, `~/vex-kiosk.sh`)
- Document whatever the portal decision above ends up being
