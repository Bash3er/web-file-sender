import io
import json
import os
import tempfile
import time
import unittest
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.server_app import app, recent_devices
from core.server_app import expire_pauses
from core import config as config_module
from core.transfer_manager import (
    active_transfers, cancel_transfer, finish_transfer, pace_transfer_chunk,
    pause_transfer, start_transfer,
)


class DownloadZipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Shared"
        self.root.mkdir()
        (self.root / "hello.txt").write_text("hello", encoding="utf-8")
        (self.root / ".hidden.txt").write_text("hidden", encoding="utf-8")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "world.txt").write_text("world", encoding="utf-8")
        secret = self.root / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        self.config = {
            "password": "",
            "downloads_enabled": True,
            "hidden_files": False,
            "shares": [{"path": str(self.root), "name": "Shared"}],
            "rules": [{"path": str(secret), "action": "deny"}],
        }
        self.client = app.test_client()

    def archive_names(self, response):
        data = response.data
        response.close()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return set(archive.namelist())

    def test_download_all_zip_only_contains_visible_files(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/api/download-zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.archive_names(response),
            {"Shared/hello.txt", "Shared/nested/world.txt"},
        )
        self.assertIn("attachment;", response.headers["Content-Disposition"])

    def test_active_download_progress_is_reported_to_admin(self):
        config = {**self.config, "port": 6789}
        with patch("core.server_app.cfg", return_value=config):
            response = self.client.get(
                "/api/download?path=Shared%2Fhello.txt",
                environ_overrides={"REMOTE_ADDR": "192.168.1.90"},
                buffered=False,
            )
        iterator = iter(response.response)
        self.assertEqual(next(iterator), b"hello")
        with patch("core.server_app.cfg", return_value=config):
            admin = self.client.get(
                "/api/admin/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
            )
        transfers = admin.get_json()["transfers"]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["address"], "192.168.1.90")
        self.assertEqual(transfers[0]["path"], "Shared/hello.txt")
        self.assertEqual(transfers[0]["bytes_sent"], 5)
        self.assertEqual(transfers[0]["total_bytes"], 5)
        response.close()
        with patch("core.server_app.cfg", return_value=config):
            after_close = self.client.get(
                "/api/admin/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
            )
        self.assertEqual(after_close.get_json()["transfers"], [])

    def test_range_requests_for_one_file_are_grouped_as_one_download(self):
        config = {**self.config, "port": 6789}
        responses = []
        for byte_range in ("bytes=0-1", "bytes=2-4"):
            with patch("core.server_app.cfg", return_value=config):
                response = self.client.get(
                    "/api/download?path=Shared%2Fhello.txt",
                    headers={"Range": byte_range},
                    environ_overrides={"REMOTE_ADDR": "192.168.1.90"},
                    buffered=False,
                )
            responses.append(response)
            self.assertEqual(response.status_code, 206)
            next(iter(response.response))
        with patch("core.server_app.cfg", return_value=config):
            admin = self.client.get(
                "/api/admin/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
            )
        transfers = admin.get_json()["transfers"]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["path"], "Shared/hello.txt")
        self.assertEqual(transfers[0]["total_bytes"], 5)
        self.assertEqual(transfers[0]["bytes_sent"], 5)
        self.assertEqual(len(transfers[0]["request_ids"]), 2)
        for response in responses:
            response.close()

    def test_transfer_can_be_paused_resumed_and_cancelled_from_admin(self):
        request_id = start_transfer("192.168.1.91", "Shared/hello.txt", 5)
        self.addCleanup(finish_transfer, request_id)
        transfer_id = next(
            item["id"] for item in active_transfers()
            if request_id in item["request_ids"]
        )
        pause_url = f"/api/admin/transfers/{transfer_id}"
        with patch("core.server_app.cfg", return_value=self.config):
            paused = self.client.post(
                pause_url, json={"action": "pause"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertTrue(next(
                transfer["paused"] for transfer in active_transfers()
                if transfer["id"] == transfer_id
            ))
            resumed = self.client.post(
                pause_url, json={"action": "resume"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertFalse(next(
                transfer["paused"] for transfer in active_transfers()
                if transfer["id"] == transfer_id
            ))
            cancelled = self.client.post(
                pause_url, json={"action": "cancel"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(pause_transfer(request_id, True))
        self.assertFalse(cancel_transfer(request_id))

    def test_per_device_speed_limit_is_saved_and_applied_to_active_downloads(self):
        address = "192.168.1.93"
        transfer_id = start_transfer(address, "Shared/hello.txt", 5)
        self.addCleanup(finish_transfer, transfer_id)
        with patch("core.server_app.cfg", return_value=self.config), patch(
            "core.server_app.save_config"
        ) as save:
            response = self.client.post(
                "/api/admin/device-speed-limit",
                json={"address": address, "limit_kbps": 128},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.config["device_speed_limits_kbps"][address], 128)
        save.assert_called_once()
        self.assertEqual(
            next(item for item in active_transfers() if item["address"] == address)[
                "speed_limit_bytes_per_second"
            ],
            128 * 1024,
        )

    def test_receiver_device_name_persists_and_tracks_ip_changes(self):
        device_id = str(uuid.uuid4())
        config = {**self.config, "receiver_devices": {}}
        with patch("core.server_app.cfg", return_value=config), patch(
            "core.server_app.save_config"
        ) as save:
            first = self.client.post(
                "/api/receiver/profile",
                json={"device_id": device_id, "name": "Vinay phone"},
                environ_overrides={"REMOTE_ADDR": "192.168.1.94"},
            )
            second = self.client.post(
                "/api/receiver/profile",
                json={"device_id": device_id, "name": "Vinay phone"},
                environ_overrides={"REMOTE_ADDR": "192.168.1.95"},
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        profile = config["receiver_devices"][device_id]
        self.assertEqual(profile["name"], "Vinay phone")
        self.assertEqual(profile["address"], "192.168.1.95")
        save.assert_has_calls([unittest.mock.call(config), unittest.mock.call(config)])
        self.assertEqual(
            next(
                device for device in recent_devices(config)
                if device["address"] == "192.168.1.95"
            )["name"],
            "Vinay phone",
        )

    def test_receiver_name_dialog_blocks_browsing_until_name_is_saved(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get(
                "/", environ_overrides={"REMOTE_ADDR": "192.168.1.96"}
            )
        html = response.get_data(as_text=True)
        self.assertIn('<main id="receiverApp" inert>', html)
        self.assertIn('<dialog id="receiverNameDialog"', html)
        self.assertIn("Save name &amp; continue", html)
        self.assertIn("/api/receiver/profile", html)
        self.assertIn("receiverNameDialog.close();receiverApp.inert=false;load(current)", html)

    def test_download_speed_limit_paces_chunks(self):
        transfer_id = start_transfer(
            "192.168.1.92", "large.bin", 512, device_speed_limit_kbps=1
        )
        self.addCleanup(finish_transfer, transfer_id)
        self.assertTrue(pace_transfer_chunk(transfer_id, 256))
        started = time.monotonic()
        self.assertTrue(pace_transfer_chunk(transfer_id, 256))
        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    def test_dashboard_groups_connection_and_download_controls_in_single_panels(self):
        with patch("core.server_app.local_ip", return_value="192.168.1.10"):
            response = self.client.get(
                "/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
            )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(html.count('<h2>Connect to this sender</h2>'), 1)
        self.assertEqual(html.count('<h2>Scan to connect</h2>'), 0)
        self.assertEqual(html.count('<h2>Connection link</h2>'), 0)
        self.assertIn("Unblock &amp; resolve", html)
        self.assertIn("controlTransfer", html)
        self.assertNotIn("All devices combined", html)
        self.assertIn("Device speed limit (KB/s, 0 = unlimited)", html)

    def test_download_folder_zip_keeps_folder_contents(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/api/download-zip?path=Shared%2Fnested")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.archive_names(response), {"Shared/nested/world.txt"})

    def test_download_zip_obeys_download_setting(self):
        self.config["downloads_enabled"] = False
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/api/download-zip")
        self.assertEqual(response.status_code, 403)

    def test_file_listing_reports_download_setting(self):
        self.config["downloads_enabled"] = False
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/api/files")
        self.assertFalse(response.get_json()["downloads_enabled"])

    def test_receiver_page_offers_bulk_download_actions(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Download folder", response.data)
        self.assertIn(b"Download everything", response.data)
        self.assertIn(b"Download selected", response.data)
        self.assertIn(b"type='checkbox'", response.data)
        self.assertIn(b"/api/download-selected", response.data)
        self.assertIn(b"/api/download-zip", response.data)
        self.assertIn(b'id="backButton"', response.data)
        self.assertIn(b"function goBack()", response.data)
        self.assertIn(b"function goHome()", response.data)
        self.assertIn(b"folderHistory.pop()", response.data)
        self.assertIn(b'aria-label="Home"', response.data)
        self.assertIn(b"/asset/icon/folder.png", response.data)
        self.assertIn(b"background:#f4f7f4", response.data)
        self.assertIn(b"background:#367c39", response.data)
        self.assertIn(b"button:hover{background:#28652d", response.data)
        self.assertIn(b"accent-color:#367c39", response.data)
        self.assertNotIn(b"#2875f5", response.data)

    def test_blocked_receiver_can_submit_appeal(self):
        address = "192.168.1.83"
        self.config["blocked_clients"] = [address]
        with patch("core.server_app.cfg", return_value=self.config), patch(
            "core.server_app.save_config"
        ), patch.dict("core.server_app._appeal_last_submitted", {}, clear=True):
            page = self.client.get("/", environ_overrides={"REMOTE_ADDR": address})
            response = self.client.post(
                "/api/appeals",
                json={"message": "I believe this block was applied by mistake."},
                environ_overrides={"REMOTE_ADDR": address},
            )
        self.assertIn(b"Submit appeal", page.data)
        self.assertIn(b"maxlength=\"1000\"", page.data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.config["appeals"]), 1)
        self.assertEqual(self.config["appeals"][0]["address"], address)
        self.assertEqual(
            self.config["appeals"][0]["message"],
            "I believe this block was applied by mistake.",
        )
        appeal_id = self.config["appeals"][0]["id"]
        with patch("core.server_app.cfg", return_value=self.config), patch(
            "core.server_app.save_config"
        ):
            dismissed = self.client.delete(
                f"/api/admin/appeals/{appeal_id}",
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(dismissed.status_code, 200)
        self.assertEqual(self.config["appeals"], [])

    def test_unblock_from_appeal_removes_device_and_appeal(self):
        address = "192.168.1.85"
        appeal = {
            "id": "appeal-id",
            "address": address,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "message": "Please review this block.",
        }
        config = {
            **self.config,
            "blocked_clients": [address],
            "blocked_client_reasons": {address: "Review requested"},
            "appeals": [appeal],
        }
        with patch("core.server_app.cfg", return_value=config), patch(
            "core.server_app.save_config"
        ) as save:
            response = self.client.post(
                f"/api/admin/appeals/{appeal['id']}/unblock",
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(config["blocked_clients"], [])
        self.assertEqual(config["blocked_client_reasons"], {})
        self.assertEqual(config["appeals"], [])
        save.assert_called_once_with(config)

    def test_appeal_submission_requires_block_and_valid_message(self):
        address = "192.168.1.84"
        with patch("core.server_app.cfg", return_value=self.config):
            not_blocked = self.client.post(
                "/api/appeals", json={"message": "A long enough message."},
                environ_overrides={"REMOTE_ADDR": address},
            )
            self.config["blocked_clients"] = [address]
            too_short = self.client.post(
                "/api/appeals", json={"message": "Short"},
                environ_overrides={"REMOTE_ADDR": address},
            )
        self.assertEqual(not_blocked.status_code, 403)
        self.assertEqual(too_short.status_code, 400)

    def test_qr_route_generates_local_receiver_link_only_for_admin(self):
        config = {**self.config, "port": 6789}
        with patch("core.server_app.cfg", return_value=config), patch(
            "core.server_app.local_ip", return_value="192.168.1.10"
        ):
            response = self.client.get(
                "/api/admin/receiver-qr.svg",
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            forbidden = self.client.get(
                "/api/admin/receiver-qr.svg",
                environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", response.data)
        self.assertEqual(forbidden.status_code, 403)

    def test_supplied_icons_are_served_for_ui(self):
        icon_names = (
            "add.png", "blocked.png", "copy.png", "folder.png", "pause.png",
            "refresh.png", "resume.png", "save.png", "unblocked.png",
        )
        for filename in icon_names:
            with self.subTest(filename=filename):
                response = self.client.get(f"/asset/icon/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")
                self.assertTrue(response.data.startswith(b"\x89PNG\r\n\x1a\n"))
                response.close()
        unknown = self.client.get("/asset/icon/unknown.png")
        self.assertEqual(unknown.status_code, 404)
        unknown.close()

    def test_dashboard_offers_browse_for_excluded_path(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertIn(b'id="denyPath"', response.data)
        self.assertIn(b"browsePath('folder','denyPath')", response.data)
        self.assertIn(b"browsePath('file','denyPath')", response.data)
        self.assertEqual(response.data.count(b"onclick=\"browsePath('file'"), 2)
        self.assertEqual(response.data.count(b"/asset/icon/add.png"), 2)
        self.assertIn(b'<img class="btn-icon white-icon" src="/asset/icon/add.png" alt="">Add to shared library', response.data)
        self.assertIn(b"Add to shared library", response.data)
        self.assertIn(b'<img class="btn-icon white-icon" src="/asset/icon/add.png" alt="">Add exclusion rule', response.data)

    def test_dashboard_shows_receiver_link_and_device_controls(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertIn(b'id="receiverUrl"', response.data)
        self.assertIn(b"Connected devices", response.data)
        self.assertIn(b'id="statusDot" class="dot"', response.data)
        self.assertIn(b"id=\"statusText\">Checking server", response.data)
        self.assertIn(b"Online ", response.data)
        self.assertIn(b"${d.config.name}", response.data)
        self.assertIn(b"Offline ", response.data)
        self.assertIn(b"${e.message}", response.data)
        self.assertIn(b'id="receiverQr"', response.data)
        self.assertIn(b"/api/admin/receiver-qr.svg", response.data)
        self.assertIn(b"Active downloads", response.data)
        self.assertIn(b"speed_bytes_per_second", response.data)
        self.assertIn(b"Device appeals", response.data)
        self.assertIn(b"Dismiss appeal", response.data)
        self.assertIn(b"Settings backup", response.data)
        self.assertIn(b"/api/admin/config/export", response.data)
        self.assertIn(b"Restore settings", response.data)
        self.assertNotIn(b"Unblock all devices", response.data)
        self.assertNotIn(b"Resume all devices", response.data)
        self.assertIn(b"5 min", response.data)
        self.assertIn(b"24 hours", response.data)
        self.assertIn(b"/asset/icon/save.png", response.data)
        self.assertIn(b'.btn-icon.white-icon{filter:brightness(0) invert(1)!important}', response.data)
        self.assertIn(b'<img class="btn-icon white-icon" src="/asset/icon/save.png" alt="">Save settings', response.data)
        self.assertIn(b"/asset/icon/copy.png", response.data)
        self.assertIn(b"/asset/icon/refresh.png", response.data)
        self.assertIn(b"icon('pause')", response.data)
        self.assertIn(b"icon('resume')", response.data)
        self.assertIn(b"icon(device.blocked?'unblocked':'blocked')", response.data)
        self.assertIn(b"function toggleBlockDevice(address,blocked)", response.data)
        self.assertIn(b'<dialog id="blockDialog" class="block-dialog"', response.data)
        self.assertIn(b'<textarea id="blockReason" maxlength="300"', response.data)
        self.assertIn(b'onclick="closeBlockDialog()">Cancel</button>', response.data)
        self.assertIn(b'onsubmit="submitBlockDialog(event)"', response.data)
        self.assertNotIn(b"prompt('Optional reason", response.data)
        self.assertIn(b"device.block_reason", response.data)
        self.assertIn(b'class="pause-duration-picker"', response.data)
        self.assertIn(b'role="radiogroup" aria-label="Pause sharing duration"', response.data)
        self.assertIn(b'class="pause-countdown" data-remaining="${device.pause_remaining_seconds}"', response.data)
        self.assertIn(b"function updatePauseCountdowns()", response.data)
        self.assertIn(b"setInterval(updatePauseCountdowns,1000)", response.data)
        self.assertIn(b"['300','5 min']", response.data)
        self.assertIn(b"['900','15 min']", response.data)
        self.assertIn(b"['3600','1 hour']", response.data)
        self.assertIn(b"['86400','24 hours']", response.data)
        self.assertIn(b'value="custom"', response.data)
        self.assertIn(b'min="1" max="43200"', response.data)
        self.assertIn(b'placeholder="min" required', response.data)
        self.assertIn(b'class="pause-custom-field" id="pause-custom-field-${esc(device.address)}" hidden', response.data)
        self.assertIn(b'aria-label="Custom pause duration in minutes"', response.data)
        self.assertIn(b"updatePauseDuration(address)", response.data)
        self.assertIn(b"field.hidden=!custom", response.data)
        self.assertIn(
            b"if(!document.querySelector('.device-actions :focus,.transfer-actions :focus'))refresh()",
            response.data,
        )
        self.assertIn(b".pause-duration-option:has(input:checked)", response.data)
        self.assertIn(b"querySelector('input:checked')", response.data)

    def test_dashboard_uses_original_green_color_scheme(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertIn(b"background:#f4f7f4", response.data)
        self.assertIn(b"color:#17211f", response.data)
        self.assertIn(b"background:#367c39", response.data)
        self.assertIn(b"background:#eff5ee", response.data)
        self.assertIn(b"#172b25", response.data)
        self.assertIn(b"#9be27c35", response.data)
        self.assertNotIn(b"background:#2875f5", response.data)
        self.assertIn(b".pause-duration-picker", response.data)

    def test_local_dashboard_and_admin_api_need_no_admin_password(self):
        config = {
            **self.config,
            "password": "receiver-secret",
            "port": 6789,
            "admin_username": "legacy",
            "admin_password_hash": "legacy-hash",
            "admin_session_key": "legacy-key",
        }
        client = app.test_client()
        with patch("core.server_app.cfg", return_value=config):
            dashboard = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            admin_page = client.get("/admin", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            admin_config = client.get(
                "/api/admin/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
            )
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Server settings", dashboard.data)
        self.assertNotIn(b"Admin login", dashboard.data)
        self.assertNotIn(b"Admin account", dashboard.data)
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"Server settings", admin_page.data)
        self.assertEqual(admin_config.status_code, 200)
        for key in ("admin_username", "admin_password_hash", "admin_session_key"):
            self.assertNotIn(key, admin_config.get_json()["config"])

    def test_settings_export_and_restore(self):
        receiver_id = str(uuid.uuid4())
        config = {
            **self.config,
            "name": "Backup sender",
            "port": 6789,
            "password": "keep-private",
            "blocked_clients": ["192.168.1.77"],
            "blocked_client_reasons": {"192.168.1.77": "Test reason"},
            "device_speed_limits_kbps": {"192.168.1.77": 128},
            "receiver_devices": {
                receiver_id: {
                    "name": "Vinay phone",
                    "address": "192.168.1.77",
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                },
            },
        }
        with patch("core.server_app.cfg", return_value=config), patch(
            "core.server_app.save_config"
        ) as save:
            exported = self.client.get(
                "/api/admin/config/export",
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(exported.status_code, 200)
            self.assertIn("attachment;", exported.headers["Content-Disposition"])
            self.assertEqual(exported.get_json()["password"], "keep-private")
            self.assertEqual(
                exported.get_json()["blocked_client_reasons"]["192.168.1.77"],
                "Test reason",
            )
            save.assert_not_called()

            restored = self.client.post(
                "/api/admin/config/restore",
                json=exported.get_json(),
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertEqual(restored.status_code, 200)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0]["password"], "keep-private")
        self.assertEqual(save.call_args.args[0]["blocked_clients"], ["192.168.1.77"])
        self.assertEqual(
            save.call_args.args[0]["device_speed_limits_kbps"]["192.168.1.77"], 128
        )
        self.assertEqual(
            save.call_args.args[0]["receiver_devices"][receiver_id]["name"],
            "Vinay phone",
        )

    def test_settings_restore_rejects_invalid_port(self):
        with patch("core.server_app.save_config") as save:
            response = self.client.post(
                "/api/admin/config/restore",
                json={"port": 80},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_admin_config_keeps_missing_shares_and_reports_availability(self):
        missing = str(self.root / "removed-later.txt")
        self.config["port"] = 6789
        self.config["shares"] = [
            {"path": str(self.root), "name": "Available"},
            {"path": missing, "name": "Missing"},
        ]
        with patch("core.server_app.cfg", return_value=self.config), patch(
            "core.server_app.save_config"
        ) as save:
            response = self.client.get(
                "/api/admin/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}
            )
        self.assertEqual(response.status_code, 200)
        shares = response.get_json()["config"]["shares"]
        self.assertEqual([share["availability"] for share in shares],
                         ["Available", "File or folder not found"])
        self.assertEqual(len(self.config["shares"]), 2)
        save.assert_not_called()

    def test_legacy_admin_credentials_are_removed_from_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "server.json"
            config_path.write_text(
                json.dumps({
                    "admin_username": "legacy",
                    "admin_password_hash": "legacy-hash",
                    "admin_session_key": "legacy-key",
                    "download_speed_limit_kbps": 400,
                    "shares": [],
                }),
                encoding="utf-8",
            )
            with patch.object(config_module, "CONFIG_DIR", directory), patch.object(
                config_module, "CONFIG_PATH", str(config_path)
            ):
                loaded = config_module.load_config()
            persisted = json.loads(config_path.read_text(encoding="utf-8"))
        for key in (
            "admin_username", "admin_password_hash", "admin_session_key",
            "download_speed_limit_kbps",
        ):
            self.assertNotIn(key, loaded)
            self.assertNotIn(key, persisted)

    def test_admin_pages_and_apis_remain_unavailable_to_lan_devices(self):
        with patch("core.server_app.cfg", return_value=self.config):
            dashboard = self.client.get(
                "/admin", environ_overrides={"REMOTE_ADDR": "192.168.1.20"}
            )
            admin_api = self.client.get(
                "/api/admin/config", environ_overrides={"REMOTE_ADDR": "192.168.1.20"}
            )
        self.assertEqual(dashboard.status_code, 403)
        self.assertEqual(admin_api.status_code, 403)

    def test_receiver_password_still_applies_to_lan_devices(self):
        config = {**self.config, "password": "receiver-secret"}
        with patch("core.server_app.cfg", return_value=config):
            denied = self.client.get(
                "/", environ_overrides={"REMOTE_ADDR": "192.168.1.20"}
            )
            allowed = self.client.get(
                "/",
                headers={"Authorization": "Basic dXNlcjpyZWNlaXZlci1zZWNyZXQ="},
                environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
            )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)

    def test_health_reports_reachable_receiver_link_and_port(self):
        config = {**self.config, "name": "Sender", "port": 6789}
        with patch("core.server_app.cfg", return_value=config), patch(
            "core.server_app.local_ip", return_value="192.168.1.10"
        ):
            response = self.client.get("/api/health")
        self.assertEqual(response.get_json()["receiver_url"], "http://192.168.1.10:6789/")
        self.assertEqual(response.get_json()["port"], 6789)

    def test_admin_can_block_and_unblock_device(self):
        address = "192.168.1.42"
        with patch("core.server_app.cfg", return_value=self.config), patch("core.server_app.save_config") as save:
            blocked = self.client.post(
                "/api/admin/device-control",
                json={"address": address, "action": "block", "reason": "Repeated failed requests"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(blocked.status_code, 200)
            self.assertIn(address, self.config["blocked_clients"])
            self.assertEqual(
                self.config["blocked_client_reasons"][address], "Repeated failed requests"
            )
            unblocked = self.client.post(
                "/api/admin/device-control",
                json={"address": address, "action": "unblock"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(unblocked.status_code, 200)
        self.assertNotIn(address, self.config["blocked_clients"])
        self.assertNotIn(address, self.config["blocked_client_reasons"])
        self.assertEqual(save.call_count, 2)

    def test_block_reason_must_be_text_and_within_limit(self):
        for reason in (123, "x" * 301):
            with self.subTest(reason_type=type(reason).__name__), patch(
                "core.server_app.cfg", return_value=self.config
            ):
                response = self.client.post(
                    "/api/admin/device-control",
                    json={"address": "192.168.1.42", "action": "block", "reason": reason},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                )
            self.assertEqual(response.status_code, 400)

    def test_pause_sets_selected_expiry_and_resume_clears_it(self):
        address = "192.168.1.44"
        self.config["pause_expiries"] = {}
        with patch("core.server_app.cfg", return_value=self.config), patch("core.server_app.save_config"):
            response = self.client.post(
                "/api/admin/device-control",
                json={"address": address, "action": "pause", "duration_seconds": 3600},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(response.status_code, 200)
            expiry = datetime.fromisoformat(self.config["pause_expiries"][address])
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            self.assertGreater(remaining, 3590)
            self.assertLessEqual(remaining, 3600)
            resumed = self.client.post(
                "/api/admin/device-control",
                json={"address": address, "action": "resume"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(resumed.status_code, 200)
        self.assertNotIn(address, self.config["paused_clients"])
        self.assertNotIn(address, self.config["pause_expiries"])

    def test_pause_rejects_unsupported_duration(self):
        for duration in (59, 2592001, 1.5):
            with self.subTest(duration=duration), patch("core.server_app.cfg", return_value=self.config):
                response = self.client.post(
                    "/api/admin/device-control",
                    json={"address": "192.168.1.44", "action": "pause", "duration_seconds": duration},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                )
            self.assertEqual(response.status_code, 400)

    def test_pause_accepts_custom_duration(self):
        address = "192.168.1.45"
        with patch("core.server_app.cfg", return_value=self.config), patch("core.server_app.save_config"):
            response = self.client.post(
                "/api/admin/device-control",
                json={"address": address, "action": "pause", "duration_seconds": 30 * 60},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 200)
        expiry = datetime.fromisoformat(self.config["pause_expiries"][address])
        remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
        self.assertGreater(remaining, 29 * 60)
        self.assertLessEqual(remaining, 30 * 60)

    def test_expired_pause_is_removed_automatically(self):
        address = "192.168.1.44"
        now = datetime.now(timezone.utc)
        config = {
            "paused_clients": [address],
            "pause_expiries": {address: (now - timedelta(seconds=1)).isoformat()},
        }
        changed = expire_pauses(config, now)
        self.assertTrue(changed)
        self.assertEqual(config["paused_clients"], [])
        self.assertEqual(config["pause_expiries"], {})

    def test_expired_pause_is_removed_on_device_request(self):
        address = "192.168.1.44"
        self.config["paused_clients"] = [address]
        self.config["pause_expiries"] = {
            address: (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        }
        with patch("core.server_app.cfg", return_value=self.config), patch("core.server_app.save_config") as save:
            response = self.client.get("/api/files", environ_overrides={"REMOTE_ADDR": address})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.config["paused_clients"], [])
        save.assert_called_once_with(self.config)

    def test_paused_device_cannot_browse_shared_files(self):
        address = "192.168.1.42"
        self.config["paused_clients"] = [address]
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/api/files", environ_overrides={"REMOTE_ADDR": address})
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Sharing is paused", response.data)

    def test_paused_receiver_sees_styled_pause_page(self):
        address = "192.168.1.42"
        self.config["paused_clients"] = [address]
        self.config["pause_expiries"] = {
            address: (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        }
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": address})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn(b"404", response.data)
        self.assertIn(b"Sharing unavailable", response.data)
        self.assertNotIn(b"resume automatically in", response.data)
        self.assertNotIn(b'id="countdown"', response.data)
        self.assertIn(b"background:#f4f7f4", response.data)
        self.assertIn(b"color:#367c39", response.data)

    def test_blocked_device_cannot_download_file_or_webpage(self):
        address = "192.168.1.42"
        self.config["blocked_clients"] = [address]
        with patch("core.server_app.cfg", return_value=self.config):
            file_response = self.client.get(
                "/api/download?path=Shared%2Fhello.txt",
                environ_overrides={"REMOTE_ADDR": address},
            )
            page_response = self.client.get("/", environ_overrides={"REMOTE_ADDR": address})
        self.assertEqual(file_response.status_code, 403)
        self.assertEqual(page_response.status_code, 403)
        self.assertEqual(page_response.mimetype, "text/html")
        self.assertIn(b"Access blocked", page_response.data)
        self.assertIn(b"until the sender unblocks this device", page_response.data)
        self.assertNotIn(b"<strong>Reason</strong>", page_response.data)
        self.assertNotIn(b"id=\"countdown\"", page_response.data)

    def test_blocked_ipv4_address_matches_ipv4_mapped_ipv6_request(self):
        self.config["blocked_clients"] = ["192.168.1.42"]
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get(
                "/api/files",
                environ_overrides={"REMOTE_ADDR": "::ffff:192.168.1.42"},
            )
        self.assertEqual(response.status_code, 403)

    def test_blocked_receiver_page_shows_escaped_optional_reason(self):
        address = "192.168.1.42"
        self.config["blocked_clients"] = [address]
        self.config["blocked_client_reasons"] = {address: "<policy>"}
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": address})
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"<strong>Reason</strong>&lt;policy&gt;", response.data)
        self.assertIn(b"background:#f4f7f4", response.data)
        self.assertIn(b"color:#367c39", response.data)

    def test_admin_can_clear_all_device_restrictions(self):
        self.config["blocked_clients"] = ["192.168.1.42"]
        self.config["blocked_client_reasons"] = {"192.168.1.42": "Test reason"}
        self.config["paused_clients"] = ["192.168.1.43"]
        with patch("core.server_app.cfg", return_value=self.config), patch("core.server_app.save_config"):
            unblock = self.client.post(
                "/api/admin/device-control",
                json={"action": "unblock-all"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(unblock.status_code, 200)
            self.assertEqual(self.config["blocked_clients"], [])
            self.assertEqual(self.config["blocked_client_reasons"], {})
            self.assertEqual(self.config["paused_clients"], ["192.168.1.43"])
            resume = self.client.post(
                "/api/admin/device-control",
                json={"action": "resume-all"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(resume.status_code, 200)
        self.assertEqual(self.config["blocked_clients"], [])
        self.assertEqual(self.config["paused_clients"], [])

    def test_device_controls_reject_invalid_ip(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/admin/device-control",
                json={"address": "not-an-ip", "action": "block"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 400)

    def test_recent_devices_shows_last_activity_and_controls(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        devices = recent_devices(
            {"blocked_clients": [], "paused_clients": ["192.168.1.43"]},
            [{"client": "192.168.1.42", "time": now}, {"client": "192.168.1.43", "time": now}],
        )
        self.assertEqual([device["status"] for device in devices], ["Active", "Paused"])

    def test_paused_device_stays_visible_without_recent_activity(self):
        address = "192.168.1.44"
        devices = recent_devices(
            {
                "blocked_clients": [],
                "paused_clients": [address],
                "pause_expiries": {
                    address: (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
                },
            },
            [],
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["address"], address)
        self.assertEqual(devices[0]["status"], "Paused")
        self.assertTrue(devices[0]["paused"])
        self.assertEqual(devices[0]["last_seen"], "No recent activity")

    def test_recent_devices_reports_pause_countdown_seconds(self):
        address = "192.168.1.46"
        expiry = datetime.now(timezone.utc) + timedelta(seconds=45)
        devices = recent_devices(
            {
                "blocked_clients": [],
                "paused_clients": [address],
                "pause_expiries": {address: expiry.isoformat()},
            },
            [],
        )
        self.assertGreaterEqual(devices[0]["pause_remaining_seconds"], 43)
        self.assertLessEqual(devices[0]["pause_remaining_seconds"], 45)

    def test_download_selected_zip_contains_only_requested_files(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/download-selected",
                data={"path": ["Shared/hello.txt", "Shared/nested/world.txt"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.archive_names(response),
            {"Shared/hello.txt", "Shared/nested/world.txt"},
        )

    def test_download_selected_single_file_keeps_original_name_and_extension(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/download-selected",
                data={"path": "Shared/hello.txt"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"hello")
        self.assertIn('filename=hello.txt', response.headers["Content-Disposition"])
        self.assertNotIn("application/zip", response.headers["Content-Type"])
        response.close()

    def test_download_selected_folder_recursively_collects_visible_files(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/download-selected",
                data={"path": "Shared/nested"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"world")
        self.assertIn('filename=world.txt', response.headers["Content-Disposition"])
        response.close()

    def test_download_selected_folder_with_multiple_files_returns_zip(self):
        (self.root / "nested" / "second.txt").write_text("second", encoding="utf-8")
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/download-selected",
                data={"path": "Shared/nested"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.archive_names(response),
            {"Shared/nested/world.txt", "Shared/nested/second.txt"},
        )

    def test_download_selected_zip_rejects_unshared_path(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/download-selected",
                data={"path": "Shared/secret.txt"},
            )
        self.assertEqual(response.status_code, 403)

    def test_download_selected_rejects_empty_selection(self):
        with patch("core.server_app.cfg", return_value=self.config):
            empty_response = self.client.post("/api/download-selected", data={})
        self.assertEqual(empty_response.status_code, 400)

    def test_download_selected_zip_obeys_download_setting(self):
        self.config["downloads_enabled"] = False
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.post(
                "/api/download-selected",
                data={"path": "Shared/hello.txt"},
            )
        self.assertEqual(response.status_code, 403)

    def test_dashboard_has_native_folder_and_file_browse_actions(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertIn(b"Browse folder", response.data)
        self.assertIn(b"Browse file", response.data)
        self.assertIn(b"Absolute path", response.data)

    def test_native_picker_only_accepts_folder_or_file_on_loopback(self):
        response = self.client.post(
            "/api/admin/pick-path",
            json={"kind": "drive"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            "/api/admin/pick-path",
            json={"kind": "folder"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
        )
        self.assertEqual(response.status_code, 403)

    def test_native_picker_returns_selected_path(self):
        selected = str(self.root)
        with patch("core.server_app.select_path_dialog", return_value=selected) as picker:
            response = self.client.post(
                "/api/admin/pick-path",
                json={"kind": "folder"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 200)
        returned_path = response.get_json()["path"]
        self.assertTrue(os.path.samefile(returned_path, selected))
        picker.assert_called_once_with("folder")

    def test_download_zip_rejects_traversal(self):
        with patch("core.server_app.cfg", return_value=self.config):
            response = self.client.get("/api/download-zip?path=Shared%2F..%2Fsecret.txt")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
