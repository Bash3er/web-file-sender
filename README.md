# Web File Sender

A local-first, read-only LAN file-sharing project for Windows, macOS and Linux. The sender publishes only explicitly selected files/folders, and receivers browse and download using a normal web browser.

## Features

- **Sender dashboard:** add multiple folders and individual files using either a native Browse dialog or manual absolute paths, assign virtual names, exclude paths, toggle downloads/video streaming, configure the optional receiver password, and review recent activity. UI action icons are served from `asset/icon/`.
- **Device management:** receivers can name themselves on first visit; the name is saved locally and in sender settings under a persistent browser ID, so it stays recognizable if the device's IP changes. View receiver names/IPs and pause/resume or block/unblock an individual IP.
- **Receiver access helpers:** open the receiver link or scan its QR code in one dashboard panel; blocked receivers can submit an appeal for sender review.
- **Transfer controls:** monitor active downloads grouped by receiver and file (including HTTP range/retry streams), pause/resume or cancel each file, and optionally set a per-device speed limit.
- **Settings backup:** export and restore server settings and restrictions using a local JSON backup.
- **Receiver webpage:** browse shared folders, download individual files, download a folder or the entire shared library as a ZIP, and play supported videos inline; shared folder rows use the supplied folder icon.
- **Video seeking:** Flask/Werkzeug conditional file responses support HTTP range requests on supported versions, which allows browser seeking without first downloading the whole video.
- **Read-only sharing:** no upload, delete, rename, or remote file-write endpoints.

## Quick start

Install Python 3.10+ and run in a terminal:

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate

python -m pip install -r requirements.txt
python server.py
```

Open the local dashboard shown in the terminal (normally `http://127.0.0.1:6789`) and add the folders/files you want to share. The dashboard is available only on the sender computer and does not require an admin password. On another device, open the sender's LAN URL in a web browser to browse and download shared files.

### Run the tests

```bash
python -m unittest discover -s tests -v
```

GitHub Actions runs this test suite on Windows and Ubuntu with Python 3.10, 3.12, and 3.13 for pushes and pull requests.

The dashboard's **Connect to this sender** panel shows the LAN URL and QR code. **Connected devices** lists IPs observed requesting files; “Active” means an access request was seen in the last 60 seconds, not that the server maintains a permanent device connection. **Pause sharing** temporarily denies new receiver access for 5 minutes, 15 minutes, 1 hour, 24 hours, or a custom duration from 1 minute to 30 days, then automatically resumes sharing. The sender dashboard shows a seconds-updating pause countdown; paused receivers instead see a 404-style unavailable page without a timer. **Block device** denies access until explicitly unblocked; the sender can provide an optional reason, which is shown to the blocked receiver. Paused and blocked devices can be resumed or unblocked individually.

The dashboard's **Server status** indicator shows Online when its local admin endpoint responds, or Offline with the request error if it cannot be reached. Shared paths that are unavailable remain listed with a status such as “File or folder not found” or “Drive unavailable”, and are not exposed to receivers until they are available again.

The receiver asks for a device name before showing the shared files. That label and a random browser ID are saved in sender configuration; the current IP is refreshed when the browser reconnects, so the label survives IP changes in that browser. The dashboard includes per-file progress with pause/resume and cancel controls; multiple HTTP requests for the same file from one device are combined into one row. Downloads use the fastest available speed by default. If needed, set a per-device limit in **Connected devices**; `0` means unlimited. Limits are in KB/s. From an appeal, the sender can either unblock the device (which also removes its appeal) or dismiss only the appeal. A blocked receiver may optionally provide an appeal message; block reasons are optional and are not shown to receivers when left blank. Settings backup files include the optional receiver password and saved device names, so keep exported backups private.

The receiver file browser and its paused/blocked access pages use the same green color scheme as the sender dashboard.

### Share a folder directly from terminal

```bash
python server.py --share "C:\\Users\\YourName\\Videos"
python server.py --share "/home/you/share" --port 6789
```

`--share` can be repeated. On first run, no folder is automatically shared. This is intentional: you must explicitly choose what is exposed.

### Receiver webpage

Open `http://SENDER-LAN-IP:6789/` in a browser. Use the **Back** arrow to return to the previously visited folder and the **Home** icon to return to the shared-files root. Select file/folder checkboxes and use **Download selected**; a single resulting file downloads with its original filename and extension, while multiple files download together in a ZIP. The page also supports per-file downloads, **Download folder** for the current folder, and **Download everything** for all permitted shares. Supported videos can be previewed inline without downloading first. Downloads honor the sender's download setting, hidden-file policy, and exclusions.

In the sender dashboard on the sender computer, use **Browse folder…** or **Browse file…** to select a share or exclusion path, or continue entering the absolute path manually. Native Browse dialogs require the sender to be running in an interactive desktop session.

## Dashboard settings and security

- Dashboard pages and APIs only accept requests from loopback (`127.0.0.1` / `::1`), so a phone or other LAN device cannot open or administer the dashboard. Open it on the sender computer itself; no dashboard password is required.
- Shared content is **default-deny**: only configured shares are listed. Exclusion/deny rules override shared folders. Canonical path checks reject `..` traversal and symlinks that escape the configured share root.
- The optional receiver password uses HTTP Basic authentication for LAN file browsing and transfers. It does not gate the local dashboard. This is suitable for a trusted home LAN, not a substitute for HTTPS on an untrusted network.
- The server is read-only from the receiver perspective. Downloads and video streams still transmit the file bytes; streaming is not copy protection.
- Hidden files are excluded by default. Avoid sharing an entire drive unless you understand that all non-excluded files under that share may become available.
- Discovery uses UDP broadcast on port `6790`; file sharing uses TCP port `6789` by default. Allow these ports in the sender's firewall for private networks if you want LAN discovery/sharing.
- Network/client isolation on some guest Wi-Fi networks can prevent discovery or connections. Use the sender URL printed in its terminal as a fallback.
- A browser may not play every codec. MP4/H.264 is usually a good compatibility target; support depends on the browser and operating system.

## Current limitations

- The dashboard's native Browse actions require an interactive desktop session; entering an absolute path manually remains available.
- `Download all` mirrors the virtual shared folder names into the receiver destination. If a transfer fails, run it again; this version does not yet resume partial downloads or verify hashes.
- The optional receiver password is shared across all receiver endpoints; there are no per-device accounts or permissions yet.
- The app is intended for a trusted local network. It does not provide end-to-end encryption or internet-facing hardening.

## Project layout

```text
web-file-sender/
├── server.py
├── asset/
│   └── icon/
├── core/
│   ├── server_app.py
│   ├── access_control.py
│   ├── file_manager.py
│   ├── streaming.py
│   ├── transfer_manager.py
│   └── config.py
├── config/                 # Local settings are created on first run and ignored by Git
├── requirements.txt
├── tests/
├── .github/workflows/
├── README.md
├── LICENSE
└── .gitignore
```

## Troubleshooting

- **No senders found:** ensure both computers are on the same private LAN, sender is running, and firewall allows TCP `6789` and UDP `6790`. Enter the sender IP manually if broadcast is blocked.
- **Dashboard unreachable:** open `http://127.0.0.1:6789/` on the sender computer, not the receiver.
- **403 on a file:** check the configured share and any exclusions. A denied item is intentionally hidden.
- **Video won't play:** try a browser-supported MP4/H.264 file.
- **Port busy:** run `python server.py --port 6791` and connect to that port.
