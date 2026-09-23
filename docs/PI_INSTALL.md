# Raspberry Pi installation draft

The current release has an emulator, a sensor interface and a durable sender. It has **no physical detector driver**. Do not enable simulated measurements against an operational station. The following setup prepares the Python environment and an outbox retry timer. Complete the driver and verify the electrical interface when hardware arrives.

Use Raspberry Pi OS with Python 3.11 or newer. Run the following as an administrator, adjusting the source path after copying the `radiation-pi` project to the Pi:

```sh
sudo useradd --system --home /var/lib/radiation-pi --shell /usr/sbin/nologin radiation-pi
sudo install -d -o radiation-pi -g radiation-pi -m 0750 /var/lib/radiation-pi
sudo install -d -o root -g root -m 0755 /opt/radiation-pi
sudo install -d -o root -g root -m 0750 /etc/radiation-pi
# Copy the project files to /opt/radiation-pi before the next commands.
cd /opt/radiation-pi
python3 -m venv .venv
.venv/bin/python -m pip install .
sudo install -m 0640 -o root -g radiation-pi config.example.toml /etc/radiation-pi/config.toml
```

Edit `/etc/radiation-pi/config.toml`: set the station's HTTPS URL, its assigned station and detector IDs, and `outbox_path = "/var/lib/radiation-pi/outbox.sqlite3"`. Never point the emulator at the operational station. The configured device token must belong to this station only.

Create `/etc/radiation-pi/device.env` with one line, `RADIATION_PI_TOKEN=<this-station-token>`, then restrict it:

```sh
sudo chown root:root /etc/radiation-pi/device.env
sudo chmod 0600 /etc/radiation-pi/device.env
sudo install -m 0644 deploy/radiation-pi-outbox.service /etc/systemd/system/
sudo install -m 0644 deploy/radiation-pi-outbox.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now radiation-pi-outbox.timer
```

The timer retries existing queued messages every minute. Exit code 2 means some messages are pending for a later retry and is accepted by the service; code 3 means a blocked message needs investigation. Inspect it with `systemctl status radiation-pi-outbox.service`, `journalctl -u radiation-pi-outbox.service`, and the `outbox-status` CLI command. The outbox lives under `/var/lib/radiation-pi` and must survive restarts.

Before connecting a physical sensor, run unit tests in the Pi environment and a dry-run emulator. A real sensor adapter must implement `radiation_pi.sensor.Sensor` and be tested for unit conversion, read failures and timestamp behavior. A continuous sensor service will be defined after that adapter and the real device are available.
