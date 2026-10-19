from collections import deque
from datetime import datetime
import threading
import time
import uuid

_lock = threading.Lock()
_events = deque(maxlen=300)
_active_transfers = {}
_limit_buckets = {}

def set_device_speed_limit(address, kilobytes_per_second):
    bucket_key = f"device:{address}"
    with _lock:
        _limit_buckets.pop(bucket_key, None)
        for transfer in _active_transfers.values():
            if transfer["address"] == address:
                transfer["device_speed_limit_bytes_per_second"] = kilobytes_per_second * 1024
                transfer["condition"].notify_all()

def pause_transfer(transfer_id, paused):
    with _lock:
        transfer = _active_transfers.get(transfer_id)
        if transfer is None or transfer["cancelled"]:
            return False
        transfer["paused"] = paused
        transfer["condition"].notify_all()
        return True

def cancel_transfer(transfer_id):
    with _lock:
        transfer = _active_transfers.get(transfer_id)
        if transfer is None or transfer["cancelled"]:
            return False
        transfer["cancelled"] = True
        transfer["condition"].notify_all()
        return True

def transfer_chunk_size(transfer_id, maximum):
    with _lock:
        transfer = _active_transfers.get(transfer_id)
        if transfer is None:
            return 0
        speed_limit = transfer["device_speed_limit_bytes_per_second"]
        if not speed_limit:
            return min(maximum, 64 * 1024)
        return max(1, min(maximum, 64 * 1024, speed_limit // 4))

def pace_transfer_chunk(transfer_id, byte_count):
    with _lock:
        transfer = _active_transfers.get(transfer_id)
        if transfer is None:
            return False
        while not transfer["cancelled"]:
            while transfer["paused"] and not transfer["cancelled"]:
                transfer["condition"].wait()
            if transfer["cancelled"]:
                return False
            speed_limit = transfer["device_speed_limit_bytes_per_second"]
            if not speed_limit:
                return True
            now = time.monotonic()
            key = f"device:{transfer['address']}"
            bucket = _limit_buckets.setdefault(
                key,
                {"tokens": float(max(speed_limit / 4, byte_count)), "updated": now},
            )
            elapsed = now - bucket["updated"]
            bucket_capacity = max(speed_limit / 4, byte_count)
            bucket["tokens"] = min(
                bucket_capacity, bucket["tokens"] + elapsed * speed_limit
            )
            bucket["updated"] = now
            if bucket["tokens"] >= byte_count:
                bucket["tokens"] -= byte_count
                return True
            needed = (byte_count - bucket["tokens"]) / speed_limit
            transfer["condition"].wait(timeout=needed)
        return False

def log_event(action, path="", client=""):
    event = {"time": datetime.now().astimezone().isoformat(timespec="seconds"),
             "action": action, "path": path, "client": client}
    with _lock:
        _events.appendleft(event)

def recent_events():
    with _lock:
        return list(_events)

def start_transfer(address, path, total_bytes, device_speed_limit_kbps=0):
    transfer_id = uuid.uuid4().hex
    with _lock:
        _active_transfers[transfer_id] = {
            "id": transfer_id,
            "address": address,
            "path": path,
            "bytes_sent": 0,
            "total_bytes": total_bytes,
            "started": time.monotonic(),
            "device_speed_limit_bytes_per_second": device_speed_limit_kbps * 1024,
            "paused": False,
            "cancelled": False,
            "condition": threading.Condition(_lock),
        }
    return transfer_id

def update_transfer(transfer_id, bytes_sent):
    with _lock:
        transfer = _active_transfers.get(transfer_id)
        if transfer is not None:
            transfer["bytes_sent"] = min(
                transfer["total_bytes"], transfer["bytes_sent"] + bytes_sent
            )

def finish_transfer(transfer_id):
    with _lock:
        _active_transfers.pop(transfer_id, None)

def active_transfers():
    now = time.monotonic()
    with _lock:
        grouped = {}
        for transfer in _active_transfers.values():
            key = (transfer["address"], transfer["path"])
            group = grouped.setdefault(key, {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, "\0".join(key)).hex,
                "address": transfer["address"],
                "path": transfer["path"],
                "request_ids": [],
                "bytes_sent": 0,
                "total_bytes": 0,
                "started": transfer["started"],
                "paused": True,
                "device_speed_limit_bytes_per_second": transfer["device_speed_limit_bytes_per_second"],
            })
            group["request_ids"].append(transfer["id"])
            group["bytes_sent"] += transfer["bytes_sent"]
            group["total_bytes"] = max(group["total_bytes"], transfer["total_bytes"])
            group["started"] = min(group["started"], transfer["started"])
            group["paused"] = group["paused"] and transfer["paused"]
        return [
            {
                **group,
                "bytes_sent": min(group["bytes_sent"], group["total_bytes"]),
                "speed_bytes_per_second": int(
                    group["bytes_sent"] / max(now - group["started"], 0.001)
                ),
                "speed_limit_bytes_per_second": group["device_speed_limit_bytes_per_second"],
            }
            for group in grouped.values()
        ]

def control_transfer_group(group_id, action):
    with _lock:
        matching = [
            transfer for transfer in _active_transfers.values()
            if uuid.uuid5(
                uuid.NAMESPACE_URL, "\0".join((transfer["address"], transfer["path"]))
            ).hex == group_id
        ]
        if not matching:
            return False
        if action == "pause":
            for transfer in matching:
                transfer["paused"] = True
                transfer["condition"].notify_all()
        elif action == "resume":
            for transfer in matching:
                transfer["paused"] = False
                transfer["condition"].notify_all()
        elif action == "cancel":
            for transfer in matching:
                transfer["cancelled"] = True
                transfer["condition"].notify_all()
        else:
            return False
        return True
