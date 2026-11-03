# Run as a systemd service

The sample service uses `/opt/company-watch` and a dedicated `companywatch` user.
Change the paths and account in `systemd/company-watch.service` for your
installation.

1. Place a fresh source checkout at `/opt/company-watch`.
2. Create the service account if it does not exist:

   ```bash
   sudo useradd --system --home-dir /opt/company-watch --shell /usr/sbin/nologin companywatch
   ```

3. Follow the README setup steps to create and edit the local configuration.
4. Give the service account ownership of the checkout so it can write SQLite
   files. Restrict access to `.env`:

   ```bash
   sudo chown -R companywatch:companywatch /opt/company-watch
   sudo chmod 600 /opt/company-watch/.env
   ```

5. Verify that `/usr/bin/python3` is version 3.11 or later. If you use Codex,
   set `CODEX_COMMAND` to its absolute executable path and authenticate as the
   service account. A login belonging to a different account is not sufficient.
6. Install and start the service:

   ```bash
   sudo cp /opt/company-watch/systemd/company-watch.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now company-watch
   sudo journalctl -u company-watch -f
   ```

After updating code, restart the service to load the changes:

```bash
sudo systemctl restart company-watch
```

Keep database backups private. For a live database, use SQLite's backup API or
stop the service before copying database files. A live WAL database can have
committed data in its companion files.

For an existing installation, follow the [rename migration guide](migration.md)
before installing a second service.
