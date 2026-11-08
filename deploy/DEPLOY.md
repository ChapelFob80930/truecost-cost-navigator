# Deploy TrueCost on Vultr (API + https + Tiger Data database)

What you end up with: `https://api.yourdomain.com` serving the FastAPI backend, using Tiger Data for prices, with Caddy handling https automatically and the service restarting itself if it crashes.

You need: a Vultr account, a domain you control (or use the free `sslip.io` fallback in step 2), and your local `.env` (Gemini key, `DATABASE_URL`, `PGPASSWORD`).

## 1. Create the server
In Vultr: deploy a new **Cloud Compute** instance, **Ubuntu 24.04**, the smallest plan with 1 vCPU and at least 1 GB RAM, IPv4 on. Note its **IP address** and the root password (or add your SSH key).

## 2. Point a name at it
- **Your domain (GoDaddy Registry etc.):** add an **A record** `api` (or `@`) pointing to the server IP. Wait a few minutes, then check `nslookup api.yourdomain.com` shows that IP.
- **No domain yet?** Use `IP-WITH-DASHES.sslip.io`, for example IP `45.32.10.20` becomes `45-32-10-20.sslip.io`. It resolves to that IP with no DNS setup, and Caddy can still get a real https certificate for it.

Caddy can only get the certificate after this name points at the server.

## Build the upload bundle (from the project folder)
```bash
zip -r truecost-deploy.zip backend deploy scripts requirements.txt README.md truecost.db \
  -x "*/__pycache__/*" "*.pyc" "*/.pytest_cache/*" ".env" "*.env"
unzip -l truecost-deploy.zip | grep -i "\.env" || echo "no env file in the bundle: good"
```

## 3. Upload (from your PC, PowerShell in the project folder)
```powershell
scp truecost-deploy.zip root@SERVER_IP:/root/
scp .env root@SERVER_IP:/root/truecost.env
```
The zip does **not** contain `.env` (it holds secrets). Open your `.env` first and confirm `DATABASE_URL` is active (no `#` in front) and `PGPASSWORD` is filled in. Also set `CORS_ORIGINS` (step 6) now if you know your frontend address.

## 4. Install (on the server)
```bash
ssh root@SERVER_IP
apt-get update && apt-get install -y unzip
mkdir -p /opt/truecost && unzip -o /root/truecost-deploy.zip -d /opt/truecost
mv /root/truecost.env /opt/truecost/.env
sed -i 's/\r$//' /opt/truecost/deploy/*.sh      # safety: strips Windows line endings
bash /opt/truecost/deploy/setup.sh api.yourdomain.com
```
It installs Python and Caddy, creates a locked-down service user, installs the API as a service on port 8010 (private), proxies https from Caddy, and opens only ports 22, 80 and 443.

## 5. Check it
```bash
bash /opt/truecost/deploy/check.sh https://api.yourdomain.com
```
You want `ALL GOOD`, including `database is Postgres (Tiger Data)`. Also open `https://api.yourdomain.com/docs` in a browser.

## Emailing the appeal letter: nothing to set up
The appeal screen's "Open in Gmail" button builds a prefilled Gmail compose link **in the browser**. The patient reviews the letter and presses Send in their own Gmail. There is no Google sign-in, no stored login and nothing to configure on the server.

The API also contains an optional "Connect my Gmail" (OAuth) feature. It stays **off** unless `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are in `.env`. For a public launch, leave them unset: the public can't use that feature until Google verifies the app, and it keeps tokens in memory only.

## 6. Connect the frontend
- In the Lovable project, set `API_BASE` in `src/lib/api.ts` to `https://api.yourdomain.com` (no trailing slash).
- On the server, edit `/opt/truecost/.env` and set `CORS_ORIGINS` to your frontend address(es), comma-separated, for example `https://your-project.lovable.app,https://yourdomain.com`. Then `systemctl restart truecost`.
- Run `check.sh` again; the CORS line should pass.

## Updating after a code change
Rebuild the zip (see "Build the upload bundle"), then:
```bash
scp truecost-deploy.zip root@SERVER_IP:/root/
ssh root@SERVER_IP "unzip -o /root/truecost-deploy.zip -d /opt/truecost && /opt/truecost/.venv/bin/pip install -r /opt/truecost/backend/requirements.txt && chown -R truecost:truecost /opt/truecost && systemctl restart truecost"
```
`.env` is not in the zip, so unzipping never overwrites your secrets.

## If something fails
| Symptom | Look at |
|---|---|
| `setup.sh` says API did not start | `journalctl -u truecost -n 50 --no-pager` |
| "database unreachable" / password error | `.env`: `DATABASE_URL` active, `PGPASSWORD` correct, no quotes or spaces |
| https certificate errors | `journalctl -u caddy -n 50 --no-pager`; DNS must already point at the server; ports 80/443 open |
| Browser says CORS blocked | `CORS_ORIGINS` in `.env`, then `systemctl restart truecost` |
| `/health` shows `"db":"sqlite"` | `DATABASE_URL` is commented out or missing in `/opt/truecost/.env` |
| Everything worked, then stopped | `systemctl status truecost caddy` and the Tiger service status in its console |

## Know the limits
- **Gmail compose links have a length limit.** A very long letter fills in the address and subject only, and the screen tells the patient to copy and paste the letter.
- **No login or rate limiting.** Anyone with the URL can use your Gemini quota. Fine for a hackathon; don't share the URL widely or leave it up afterwards.
- **Never commit or paste `.env`.** It holds the Gemini key and the Tiger password.
- **Not tested on a real server.** The scripts passed a syntax check and the app passed its tests in a clean Python environment, but this exact install was never run on Vultr. Expect to fix one small thing on the first try.
- **Tiger free trial** lasts 28 days from sign-up.
