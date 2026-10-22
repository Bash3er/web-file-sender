#!/usr/bin/env python3
import argparse
import base64
import hmac
import ipaddress
import io
import json
import mimetypes
import os
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from .config import DEFAULT_CONFIG, load_config, save_config
from .access_control import allowed_path, canonical, virtual_path
from .file_manager import list_directory, resolve_virtual_path, root_items
from .streaming import VIDEO_EXTENSIONS
from .transfer_manager import (
    active_transfers, control_transfer_group, finish_transfer, log_event,
    pace_transfer_chunk, recent_events, set_device_speed_limit,
    start_transfer,
    transfer_chunk_size, update_transfer,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
_appeal_lock = threading.Lock()
_appeal_last_submitted = {}
ICON_DIRECTORY = Path(__file__).resolve().parent.parent / "asset" / "icon"
ICON_FILES = frozenset({
    "file.png", "blocked.png", "copy.png", "folder.png", "pause.png", "add.png",
    "refresh.png", "resume.png", "save.png", "unblocked.png", "edit.png",
})
ADMIN_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Web File Sender — Dashboard</title><style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#17211f;background:#f4f7f4;font-synthesis:none}
*{box-sizing:border-box}body{margin:0}header{background:radial-gradient(ellipse at 78% -25%,#375e4d 0,transparent 42%),#172b25;color:#fff;padding:28px max(24px,calc((100% - 1100px)/2));display:flex;justify-content:space-between;align-items:center;gap:20px}
.brand{display:flex;align-items:center;gap:13px}.logo{height:42px;width:42px;border-radius:14px;background:#b9f27c;color:#172b25;display:grid;place-items:center;font-size:21px;font-weight:800}
h1{font-size:20px;letter-spacing:-.03em;margin:0}.subtitle{color:#b9c9c1;font-size:13px;margin-top:3px}
main{max-width:1100px;margin:30px auto;padding:0 20px 36px;display:grid;grid-template-columns:1fr 1fr;gap:18px}
.card{background:#fff;border:1px solid #e2eae3;border-radius:18px;padding:22px;box-shadow:0 8px 28px #1b36220a}
h2{font-size:16px;letter-spacing:-.02em;margin:0 0 6px}.hint,.muted{color:#708078;font-size:13px;line-height:1.55}
label{display:block;margin:15px 0 6px;font-size:13px;font-weight:650;color:#33443b}
input,button{font:inherit}input[type=text],input[type=password],input[type=number]{width:100%;padding:11px 12px;border:1px solid #dbe5dc;border-radius:10px;background:#fbfdfb;outline:none}
input:focus{border-color:#73ad62;box-shadow:0 0 0 3px #9be27c35}
input[type=checkbox]{accent-color:#438b3b;width:16px;height:16px;vertical-align:middle;margin:0 7px 0 0}
label:has(input[type=checkbox]){display:flex;align-items:center;margin:12px 0;font-weight:550}
button{border:0;border-radius:10px;padding:10px 14px;margin:8px 5px 0 0;background:#367c39;color:#fff;font-weight:650;cursor:pointer;transition:transform .16s,background .16s}
button:hover{background:#28652d;transform:translateY(-1px)}button:disabled{opacity:.48;cursor:not-allowed;transform:none}button.secondary{background:#eff5ee;color:#29432d}button.secondary:hover{background:#e1eddf}
.btn-icon{width:18px;height:18px;object-fit:contain;vertical-align:middle;margin-right:6px;flex:0 0 auto}
.btn-icon.white-icon{filter:brightness(0) invert(1)!important}
button:has(.btn-icon){display:inline-flex;align-items:center;justify-content:center}
.device-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:8px}
.pause-duration-picker{display:flex;flex-wrap:wrap;align-items:center;gap:4px;padding:4px;border:1px solid #e2eae3;border-radius:11px;background:#f7faf7}
.pause-duration-option{position:relative;display:flex;align-items:center;justify-content:center;margin:0;padding:7px 10px;border:1px solid transparent;border-radius:8px;color:#5d6d64;font-size:12px;font-weight:650;line-height:1.2;cursor:pointer;transition:background .16s,color .16s,border-color .16s}
.pause-duration-option input{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;clip-path:inset(50%)}
.pause-duration-option:has(input:checked){border-color:#d5e8d1;background:#e8f4e5;color:#28652d}
.pause-duration-option:has(input:focus-visible){outline:2px solid #73ad62;outline-offset:2px}
.pause-duration-option:hover{background:#eff5ee;color:#29432d}
.pause-duration-option:has(input:checked):hover{background:#e1eddf}
.pause-custom-field{position:relative;display:inline-flex;align-items:center}
.pause-custom-field[hidden]{display:none}
.pause-custom-field input[type=number]{width:82px;padding:7px 9px;border-radius:8px;background:#fff;color:#29432d;font-size:12px;font-weight:650;text-align:center}
.pause-custom-field input[type=number]::placeholder{color:#708078;opacity:1}
.block-dialog{width:min(92vw,440px);padding:0;border:1px solid #e2eae3;border-radius:18px;box-shadow:0 24px 80px #172b2540;color:#17211f}
.block-dialog::backdrop{background:#172b2580;backdrop-filter:blur(3px)}
.block-dialog form{padding:24px}
.block-dialog h2{margin:0 0 6px;font-size:18px}
.block-dialog p{margin:0 0 16px;color:#708078;font-size:13px;line-height:1.5}
.block-dialog label{margin:0 0 6px}
.block-dialog textarea{width:100%;min-height:110px;resize:vertical;padding:11px 12px;border:1px solid #dbe5dc;border-radius:10px;background:#fbfdfb;font:inherit;font-size:13px;outline:none}
.block-dialog textarea:focus{border-color:#73ad62;box-shadow:0 0 0 3px #9be27c35}
.block-dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}
.block-dialog-actions button{margin:0}
.block-dialog-error{min-height:20px;margin-top:8px;color:#a04436;font-size:12px}
.share-unavailable{color:#a04436;font-size:12px;font-weight:650}
.transfer{padding:12px 0;border-bottom:1px solid #edf1ed}.transfer:last-child{border-bottom:0}
.transfer-heading{display:flex;justify-content:space-between;gap:10px;font-size:13px}.transfer-meta{margin-top:4px;color:#708078;font-size:12px}
.transfer progress{display:block;width:100%;height:8px;margin-top:8px;accent-color:#367c39}
.transfer-actions{display:flex;gap:6px;margin-top:8px}.transfer-actions button{margin:0;padding:7px 10px;font-size:12px}
.qr-wrap{display:flex;align-items:center;gap:18px;flex-wrap:wrap}.qr-code{width:156px;height:156px;padding:8px;border:1px solid #e2eae3;border-radius:12px;background:#fff}
.connection-layout{display:flex;align-items:center;justify-content:space-between;gap:24px}.connection-copy{min-width:0;flex:1}.connection-copy .share{padding:14px 0}
.receiver-name-dialog{width:min(92vw,420px);padding:24px;border:1px solid #e2eae3;border-radius:18px;box-shadow:0 24px 80px #172b2540;color:#17211f}
.receiver-name-dialog::backdrop{background:#172b2580;backdrop-filter:blur(3px)}
.receiver-name-dialog h2{margin:0 0 8px}.receiver-name-dialog p{color:#708078;font-size:13px;line-height:1.5}
.receiver-name-dialog label{display:block;margin:14px 0 6px;font-size:13px;font-weight:650}
.receiver-name-dialog input{width:100%;padding:11px 12px;border:1px solid #dbe5dc;border-radius:10px;font:inherit}
.receiver-name-dialog button{margin-top:12px}
.appeal{padding:14px 0;border-bottom:1px solid #edf1ed}.appeal:last-child{border-bottom:0}
.appeal-message{margin:7px 0;white-space:pre-wrap;overflow-wrap:anywhere;color:#33443b;font-size:13px;line-height:1.5}
.backup-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.backup-actions button{margin:0}
.backup-actions a{display:inline-block;padding:10px 14px;border-radius:10px;background:#eff5ee;color:#29432d;font-size:14px;font-weight:650;text-decoration:none}
.backup-actions a:hover{background:#e1eddf}
.share{padding:13px 0;border-bottom:1px solid #edf1ed;overflow-wrap:anywhere}.share:last-child{border-bottom:0}
.share b{font-size:14px}.full{grid-column:1/-1}.statusline{display:flex;align-items:center;gap:10px;font-weight:650}.dot{width:9px;height:9px;border-radius:50%;background:#67bd5f;box-shadow:0 0 0 4px #67bd5f20}.dot.offline{background:#d55b52;box-shadow:0 0 0 4px #d55b5220}
pre{white-space:pre-wrap;max-height:240px;overflow:auto;background:#f7faf7;padding:14px;border-radius:12px;color:#4d5b52;font:12px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace}
@media(max-width:700px){header{padding:20px}header>span{display:none}main{grid-template-columns:1fr;margin:18px auto;padding:0 14px 24px}.full{grid-column:auto}.card{padding:18px}.connection-layout{align-items:flex-start;flex-direction:column}.connection-layout .qr-code{align-self:center}}
</style></head><body><header><div class="brand"><div class="logo">↗</div><div><h1>Web File Sender</h1><div class="subtitle">Your files, shared simply and securely</div></div></div><span class="subtitle">LOCAL SENDER DASHBOARD</span></header>
<main>
<section class="card"><h2>Server settings</h2><p class="hint">Personalize your sender and choose what receivers can do.</p>
<label>Server name</label><input id="name" type="text">
<label>Port (restart required after change)</label><input id="port" type="number" min="1024" max="65535">
<label>Optional receiver password</label><input id="password" type="password" placeholder="Leave blank for no password">
<label><input id="downloads" type="checkbox"> Allow downloads</label>
<label><input id="streaming" type="checkbox"> Allow inline video streaming</label>
<label><input id="hidden" type="checkbox"> Include hidden files (use carefully)</label>
<button onclick="saveSettings()"><img class="btn-icon white-icon" src="/asset/icon/save.png" alt="">Save settings</button><p id="settingsMsg" class="muted"></p></section>
<section class="card"><h2>Shared folders &amp; files</h2>
<p class="muted">Enter an absolute path on this computer. Only the selected paths are exposed.</p>
<label>Absolute path</label><input id="path" type="text" placeholder="C:\Users\you\Videos or /home/you/share">
<button class="secondary" onclick="browsePath('folder')"><img class="btn-icon" src="/asset/icon/folder.png" alt="">Browse folder…</button><button class="secondary" onclick="browsePath('file')"><img class="btn-icon" src="/asset/icon/file.png" alt="">Browse file…</button>
<label>Virtual name (optional)</label><input id="shareName" type="text" placeholder="Videos">
<button onclick="addShare()"><img class="btn-icon white-icon" src="/asset/icon/add.png" alt="">Add to shared library</button>
<h3>Currently shared</h3><div id="shares"></div>
<label>Exclude a path</label><input id="denyPath" type="text" placeholder="Absolute path to exclude">
<button class="secondary" onclick="browsePath('folder','denyPath')"><img class="btn-icon" src="/asset/icon/folder.png" alt="">Browse folder…</button><button class="secondary" onclick="browsePath('file','denyPath')"><img class="btn-icon" src="/asset/icon/file.png" alt="">Browse file…</button>
<button onclick="addDeny()"><img class="btn-icon white-icon" src="/asset/icon/add.png" alt="">Add exclusion rule</button><div id="rules"></div></section>
<section class="card full"><h2>Server status</h2><p id="status" class="statusline"><span id="statusDot" class="dot"></span><span id="statusText">Checking server…</span></p><p class="muted">Receivers can connect using the LAN address printed in your terminal. Dashboard administration is only available on this computer.</p><button class="secondary" onclick="refresh()"><img class="btn-icon" src="/asset/icon/refresh.png" alt="">Refresh status</button></section>
<section class="card full"><h2>Connect to this sender</h2><p class="muted">Open the link on another device or scan the QR code. Keep both devices on the same local network.</p><div class="connection-layout"><div class="connection-copy"><div class="share"><b><a id="receiverUrl" target="_blank" rel="noopener">Loading address…</a></b> <button class="secondary" onclick="copyReceiverUrl()"><img class="btn-icon" src="/asset/icon/copy.png" alt="">Copy link</button></div><p id="linkMsg" class="muted"></p></div><img id="receiverQr" class="qr-code" alt="QR code for receiver link"></div></section>
<section class="card full"><h2>Connected devices</h2><p class="muted">Devices are identified by their network IP. Pause temporarily disables sharing for the selected duration; Block disables it until you unblock the device.</p><div id="devices"></div></section>
<section class="card full"><h2>Active downloads</h2><p class="muted">Downloads use the fastest available speed unless a per-device limit is set. Pause, resume, or cancel a transfer here.</p><div id="transfers"><p class="muted">No active downloads.</p></div></section>
<section class="card full"><h2>Device appeals</h2><p class="muted">Appeals submitted from the blocked receiver page appear here.</p><div id="appeals"><p class="muted">No appeals received.</p></div></section>
<section class="card full"><h2>Settings backup</h2><p class="muted">Export or restore server settings, shared paths, restrictions, and device reasons. Backups include your optional receiver password; keep the file private.</p><div class="backup-actions"><a href="/api/admin/config/export">Export settings</a><input id="restoreFile" type="file" accept=".json,application/json"><button onclick="restoreSettings()">Restore settings</button></div><p id="backupMsg" class="muted"></p></section>
<section class="card full"><h2>Recent activity</h2><p class="hint">A local record of browsing and downloads.</p><pre id="events">Loading…</pre></section>
</main>
<dialog id="blockDialog" class="block-dialog" aria-labelledby="blockDialogTitle">
<form onsubmit="submitBlockDialog(event)">
<h2 id="blockDialogTitle">Block this device?</h2>
<p>Add an optional reason. The blocked receiver will be able to read it.</p>
<label for="blockReason">Reason <span class="muted">(optional, up to 300 characters)</span></label>
<textarea id="blockReason" maxlength="300" placeholder="e.g. Repeated unauthorized access"></textarea>
<div id="blockDialogError" class="block-dialog-error" role="alert"></div>
<div class="block-dialog-actions"><button class="secondary" type="button" onclick="closeBlockDialog()">Cancel</button><button type="submit">Block device</button></div>
</form>
</dialog>
<script>
async function api(path,opts={}){let r=await fetch(path,{headers:{'Content-Type':'application/json'},...opts});let d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d}
function icon(name){return `<img class="btn-icon" src="/asset/icon/${name}.png" alt="">`}
async function refresh(){try{let d=await api('/api/admin/config');name.value=d.config.name;port.value=d.config.port;password.value=d.config.password||'';downloads.checked=d.config.downloads_enabled;streaming.checked=d.config.streaming_enabled;hidden.checked=d.config.hidden_files;
shares.innerHTML=d.config.shares.map((s,i)=>`<div class="share"><b>${esc(s.name)}</b><br><span class="muted">${esc(s.path)}</span><br>${s.availability==='Available'?'<span class="muted">Available</span>':`<span class="share-unavailable">${esc(s.availability)}</span>`}<br><button class="secondary" onclick="removeShare(${i})">Remove</button></div>`).join('')||'<p class="muted">No shares yet.</p>';
rules.innerHTML=d.config.rules.map((s,i)=>`<div class="share"><span class="muted">${esc(s.path)}</span> — excluded <button class="secondary" onclick="removeRule(${i})">Remove</button></div>`).join('');
receiverUrl.textContent=d.receiver_url;receiverUrl.href=d.receiver_url;
receiverQr.src='/api/admin/receiver-qr.svg?v='+Date.now();
devices.innerHTML=d.devices.map(device=>`<div class="share"><b>${esc(device.name||'Receiver')}</b> <span class="muted">· ${esc(device.address)} · ${esc(device.status)} · last seen ${esc(device.last_seen)}</span><br>${device.blocked?`<span class="muted">Blocked until unblocked${device.block_reason?` · Reason: ${esc(device.block_reason)}`:''}</span>`:device.paused?`<span class="muted">Sharing paused${device.pause_remaining_seconds!==null?` · resumes in <span class="pause-countdown" data-remaining="${device.pause_remaining_seconds}"></span>`:' · no timer set'}</span>`:'<span class="muted">Sharing enabled</span>'}<div class="device-actions"><label>Device speed limit (KB/s, 0 = unlimited)</label><input id="device-speed-${esc(device.address)}" type="number" min="0" max="1000000" step="1" value="${device.speed_limit_kbps||0}"><button class="secondary" onclick="saveDeviceSpeedLimit('${esc(device.address)}')">Apply device limit</button>${device.paused?`<button class="secondary" onclick="controlDevice('${esc(device.address)}','resume')">${icon('resume')}Resume sharing</button>`:`<div class="pause-duration-picker" id="pause-${esc(device.address)}" role="radiogroup" aria-label="Pause sharing duration">${[['300','5 min'],['900','15 min'],['3600','1 hour'],['86400','24 hours']].map(([seconds,label],index)=>`<label class="pause-duration-option"><input type="radio" name="pause-duration-${esc(device.address)}" value="${seconds}" ${index===1?'checked':''} onchange="updatePauseDuration('${esc(device.address)}')"><span>${label}</span></label>`).join('')}<label class="pause-duration-option"><input type="radio" name="pause-duration-${esc(device.address)}" value="custom" onchange="updatePauseDuration('${esc(device.address)}')"><span>Custom</span></label><span class="pause-custom-field" id="pause-custom-field-${esc(device.address)}" hidden><input id="pause-custom-${esc(device.address)}" type="number" min="1" max="43200" step="1" placeholder="min" required aria-label="Custom pause duration in minutes"></span></div><button class="secondary" onclick="pauseDevice('${esc(device.address)}')">${icon('pause')}Pause sharing</button>`}<button class="secondary" onclick="toggleBlockDevice('${esc(device.address)}',${device.blocked})">${icon(device.blocked?'unblocked':'blocked')}${device.blocked?'Unblock device':'Block device'}</button></div></div>`).join('')||'<p class="muted">No receiver devices seen yet. Devices appear here after they browse shared files.</p>';
updatePauseCountdowns();
updatePauseCountdowns();
statusDot.classList.remove('offline');statusText.textContent=`Online · ${d.config.name} · port ${d.config.port} · ${d.config.shares.length} share(s)`;
transfers.innerHTML=d.transfers.length?d.transfers.map(t=>{const percent=t.total_bytes?Math.min(100,Math.floor(t.bytes_sent*100/t.total_bytes)):0;const formatSize=n=>n<1048576?`${(n/1024).toFixed(0)} KB`:`${(n/1048576).toFixed(1)} MB`;const status=t.paused?'Paused':`${formatSize(t.speed_bytes_per_second)}/s`;const pauseAction=t.paused?'resume':'pause';const pauseLabel=t.paused?'Resume':'Pause';return `<div class="transfer"><div class="transfer-heading"><b>${esc(t.path)}</b><span>${percent}% · ${status}</span></div><div class="transfer-meta">${esc(t.device_name)} · ${formatSize(t.bytes_sent)} / ${formatSize(t.total_bytes)}${t.speed_limit_bytes_per_second?` · cap ${formatSize(t.speed_limit_bytes_per_second)}/s`:''}${t.request_ids.length>1?` · ${t.request_ids.length} HTTP streams for this file`:''}</div><progress max="100" value="${percent}" aria-label="Download progress"></progress><div class="transfer-actions"><button class="secondary" onclick="controlTransfer('${esc(t.id)}','${pauseAction}')">${pauseLabel}</button><button class="secondary" onclick="controlTransfer('${esc(t.id)}','cancel')">Cancel</button></div></div>`}).join(''):'<p class="muted">No active downloads.</p>';
appeals.innerHTML=d.appeals.length?d.appeals.map(a=>`<div class="appeal"><b>${esc(a.address)}</b> <span class="muted">· ${esc(a.submitted_at)}</span><p class="appeal-message">${esc(a.message)}</p><button onclick="unblockAppeal('${esc(a.id)}')">Unblock &amp; resolve</button><button class="secondary" onclick="dismissAppeal('${esc(a.id)}')">Dismiss appeal</button></div>`).join(''):'<p class="muted">No appeals received.</p>';
events.textContent=d.events.map(e=>`${e.time} | ${e.action} | ${e.path} | ${e.client}`).join('\\n')||'No activity yet.';
}catch(e){statusDot.classList.add('offline');statusText.textContent=`Offline · ${e.message}`}}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function saveSettings(){try{await api('/api/admin/settings',{method:'POST',body:JSON.stringify({name:name.value,port:Number(port.value),password:password.value,downloads_enabled:downloads.checked,streaming_enabled:streaming.checked,hidden_files:hidden.checked})});settingsMsg.textContent='Saved. Restart the sender for port changes.';refresh()}catch(e){settingsMsg.textContent=e.message}}
async function saveDeviceSpeedLimit(address){const input=document.getElementById('device-speed-'+address);try{await api('/api/admin/device-speed-limit',{method:'POST',body:JSON.stringify({address,limit_kbps:Number(input.value)})});refresh()}catch(e){alert(e.message)}}
async function addShare(){try{await api('/api/admin/shares',{method:'POST',body:JSON.stringify({path:path.value,name:shareName.value})});path.value='';shareName.value='';refresh()}catch(e){alert(e.message)}}
async function browsePath(kind,target='path'){try{const d=await api('/api/admin/pick-path',{method:'POST',body:JSON.stringify({kind})});if(d.path){document.getElementById(target).value=d.path;if(target==='path'&&!shareName.value)shareName.value=d.path.replace(/[\\/]+$/,'').split(/[\\/]/).pop()}}catch(e){alert(e.message)}}
async function removeShare(i){try{await api('/api/admin/shares/'+i,{method:'DELETE'});refresh()}catch(e){alert(e.message)}}
async function addDeny(){try{await api('/api/admin/rules',{method:'POST',body:JSON.stringify({path:denyPath.value,action:'deny'})});denyPath.value='';refresh()}catch(e){alert(e.message)}}
async function removeRule(i){try{await api('/api/admin/rules/'+i,{method:'DELETE'});refresh()}catch(e){alert(e.message)}}
async function controlDevice(address,action){try{await api('/api/admin/device-control',{method:'POST',body:JSON.stringify({address,action})});refresh()}catch(e){alert(e.message)}}
let pendingBlockAddress='';
async function toggleBlockDevice(address,blocked){if(blocked){await controlDevice(address,'unblock');return}pendingBlockAddress=address;blockReason.value='';blockDialogError.textContent='';blockDialog.showModal();blockReason.focus()}
function closeBlockDialog(){blockDialog.close();pendingBlockAddress=''}
async function submitBlockDialog(event){event.preventDefault();if(!pendingBlockAddress)return;try{await api('/api/admin/device-control',{method:'POST',body:JSON.stringify({address:pendingBlockAddress,action:'block',reason:blockReason.value})});closeBlockDialog();refresh()}catch(e){blockDialogError.textContent=e.message}}
function updatePauseDuration(address){const picker=document.getElementById('pause-'+address);const custom=picker.querySelector('input[value="custom"]').checked;const field=document.getElementById('pause-custom-field-'+address);field.hidden=!custom;if(custom)document.getElementById('pause-custom-'+address).focus()}
function updatePauseCountdowns(){document.querySelectorAll('.pause-countdown').forEach(timer=>{let seconds=Number(timer.dataset.remaining);if(!Number.isFinite(seconds)||seconds<0)return;const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;timer.textContent=h?`${h}h ${m}m ${s}s`:m?`${m}m ${s}s`:`${s}s`;if(seconds>0)timer.dataset.remaining=String(seconds-1)})}
async function pauseDevice(address){const selected=document.getElementById('pause-'+address).querySelector('input:checked');if(!selected)return;const customField=document.getElementById('pause-custom-'+address);if(selected.value==='custom'&&!customField.reportValidity())return;const seconds=selected.value==='custom'?Number(customField.value)*60:Number(selected.value);try{await api('/api/admin/device-control',{method:'POST',body:JSON.stringify({address,action:'pause',duration_seconds:seconds})});refresh()}catch(e){alert(e.message)}}
async function copyReceiverUrl(){try{await navigator.clipboard.writeText(receiverUrl.textContent);linkMsg.textContent='Link copied.'}catch(e){linkMsg.textContent='Copy failed. Select and copy the link above.'}}
async function dismissAppeal(id){try{await api('/api/admin/appeals/'+encodeURIComponent(id),{method:'DELETE'});refresh()}catch(e){alert(e.message)}}
async function unblockAppeal(id){try{await api('/api/admin/appeals/'+encodeURIComponent(id)+'/unblock',{method:'POST'});refresh()}catch(e){alert(e.message)}}
async function controlTransfer(id,action){try{await api('/api/admin/transfers/'+encodeURIComponent(id),{method:'POST',body:JSON.stringify({action})});refresh()}catch(e){alert(e.message)}}
async function restoreSettings(){const file=restoreFile.files[0];if(!file){backupMsg.textContent='Choose a settings backup JSON file first.';return}if(!confirm('Restore settings from this file? Current settings will be replaced.'))return;try{const config=JSON.parse(await file.text());await api('/api/admin/config/restore',{method:'POST',body:JSON.stringify(config)});backupMsg.textContent='Settings restored.';restoreFile.value='';refresh()}catch(e){backupMsg.textContent=e.message}}
refresh();setInterval(()=>{if(!document.querySelector('.device-actions :focus,.transfer-actions :focus'))refresh()},5000);setInterval(updatePauseCountdowns,1000);
</script></body></html>"""

RECEIVER_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shared files — Web File Sender</title><style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#17211f;background:#f4f7f4;font-synthesis:none}
*{box-sizing:border-box}body{margin:0}.top{background:#fff;color:#172b25;padding:18px max(20px,calc((100% - 1080px)/2));border-bottom:1px solid #e2eae3}
.brand{display:flex;align-items:center;gap:12px}.logo{height:36px;width:36px;border-radius:11px;background:#e8f4e5;color:#367c39;display:grid;place-items:center;font-size:20px;font-weight:800}
h1{font-size:17px;letter-spacing:-.03em;margin:0}.top p{color:#718096;margin:4px 0 0;font-size:12px}
main{max-width:1000px;margin:30px auto;padding:0 20px 40px}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:18px}
.nav-actions{display:flex;align-items:center;gap:7px;min-width:0}.crumb{font-size:13px;color:#5d6d64;overflow-wrap:anywhere;margin-left:6px}.actions{display:flex;gap:8px;flex-shrink:0}
button{font:inherit;border:0;border-radius:8px;padding:9px 12px;background:#367c39;color:white;font-weight:600;cursor:pointer;transition:transform .16s,background .16s}
button:hover{background:#28652d;transform:translateY(-1px)}button:disabled{opacity:.48;cursor:not-allowed;transform:none}button.secondary{background:#eff5ee;color:#29432d}
.list{background:#fff;border:1px solid #e2eae3;border-radius:12px;padding:4px 16px;box-shadow:0 4px 18px #1b36220a}
.row{display:flex;align-items:center;gap:13px;padding:12px 2px;border-bottom:1px solid #edf1ed;min-width:0}.row:last-child{border-bottom:0}
.file-select{width:16px;height:16px;accent-color:#367c39;flex:0 0 auto;cursor:pointer}
.icon{width:34px;height:34px;border-radius:9px;background:#eff5ee;display:grid;place-items:center;font-size:18px;flex:0 0 auto}
.icon img{width:24px;height:24px;object-fit:contain}
.details{min-width:0;flex:1}.name{font-weight:600;font-size:13px;color:#33443b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.meta{color:#708078;font-size:11px;margin-top:4px}
.row a{color:#367c39;text-decoration:none;font-size:12px;font-weight:600}.row a:hover{text-decoration:underline}
.empty,.error{padding:36px 16px;text-align:center;color:#708078}.error{color:#a04436}
#player{margin-top:16px;background:#fff;border:1px solid #e2eae3;border-radius:12px;padding:16px;color:#33443b}#player:empty{display:none}#player h2{font-size:14px;margin:0 0 12px}
video{width:100%;max-height:65vh;background:#0b110e;border-radius:12px}
.top{animation:header-in .55s ease-out both}.list{animation:surface-in .45s ease-out both}.row{animation:row-in .3s ease-out both;transition:background .18s,transform .18s}.row:hover{background:#f4f7f4;transform:translateX(3px)}.icon{transition:transform .2s,background .2s}.row:hover .icon{transform:scale(1.06);background:#e8f4e5}
@keyframes header-in{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}@keyframes surface-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}@keyframes row-in{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.01ms!important}}
.receiver-top{display:flex;justify-content:space-between;align-items:center;gap:16px}
.receiver-profile{display:flex;align-items:center;justify-content:flex-end;gap:8px;min-width:0;max-width:100%}
#receiverIdentity{display:inline-block;max-width:min(36vw,240px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#405548;font-size:13px;font-weight:650}
.name-edit-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;flex:0 0 auto}
.name-edit-button img{width:15px;height:15px;object-fit:contain}
.receiver-name-dialog{width:min(92vw,420px);padding:24px;border:1px solid #e2eae3;border-radius:18px;box-shadow:0 24px 80px #172b2540;color:#17211f}
.receiver-name-dialog::backdrop{background:#172b2580;backdrop-filter:blur(3px)}
.receiver-name-dialog h2{margin:0 0 8px}.receiver-name-dialog p{color:#708078;font-size:13px;line-height:1.5}
.receiver-name-dialog label{display:block;margin:14px 0 6px;font-size:13px;font-weight:650}
.receiver-name-dialog input{width:100%;padding:11px 12px;border:1px solid #dbe5dc;border-radius:10px;font:inherit}
.receiver-name-dialog button{margin-top:12px}
@media(max-width:760px){main{margin:20px auto;padding:0 13px 28px}.top{padding:16px}.receiver-top{align-items:flex-start;flex-wrap:wrap}.receiver-profile{margin-left:auto;max-width:100%}#receiverIdentity{max-width:48vw}.toolbar{align-items:flex-start;flex-direction:column}.nav-actions{width:100%}.actions{width:100%;flex-wrap:wrap}.actions button{flex:1;padding:10px 8px;font-size:12px}.list{padding:5px 14px}.row{gap:10px}.row>a{font-size:12px}}
@media(max-width:380px){.name-edit-button{padding:8px}.name-edit-button .button-label{display:none}#receiverIdentity{max-width:55vw}}
</style></head><body><header class="top receiver-top"><div class="brand"><div class="logo">↗</div><div><h1 id="title">Shared files</h1><p>Browse and download files shared with you</p></div></div><div class="receiver-profile"><span id="receiverIdentity"></span><button id="changeReceiverName" class="secondary name-edit-button" onclick="openReceiverNameDialog()" title="Change device name" aria-label="Change device name"><img src="/asset/icon/edit.png" alt=""><span class="button-label">Set name</span></button></div></header>
<dialog id="receiverNameDialog" class="receiver-name-dialog">
<form onsubmit="submitReceiverName(event)"><h2>Name this device</h2><p>Choose a name so the sender can recognize this receiver in their device and download lists. It is saved on this device and sender.</p><label for="receiverNameInput">Device name</label><input id="receiverNameInput" maxlength="60" required autocomplete="nickname" placeholder="e.g. Vinay's phone"><div id="receiverNameError" class="error" role="alert"></div><button type="submit">Save name &amp; continue</button></form>
</dialog>
<main id="receiverApp" inert><div class="toolbar"><div class="nav-actions"><button id="backButton" class="secondary" onclick="goBack()" disabled aria-label="Back" title="Back">←</button><button class="secondary" onclick="goHome()" aria-label="Home" title="Home">⌂</button><div id="crumb" class="crumb">Loading shared files…</div></div><div class="actions"><button id="selectVisible" class="secondary" onclick="selectVisibleItems()">Select visible</button><button id="selectedDownload" class="secondary" onclick="downloadSelected()" disabled>Download selected <span id="selectedCount"></span></button><button id="folderDownload" class="secondary" onclick="downloadZip(current)" hidden disabled>Download folder</button><button id="allDownload" onclick="downloadZip('')" disabled>Download everything</button></div></div><div id="items" class="list"><div class="empty">Loading your shared files…</div></div><section id="player"></section><form id="selectedForm" method="post" action="/api/download-selected" hidden></form></main><script>
const params=new URLSearchParams(location.search);let current=params.get('path')||'';
const receiverDeviceIdKey='web-file-sender-device-id',receiverNameKey='web-file-sender-device-name';
function persistentValue(key){try{return localStorage.getItem(key)}catch(_){return null}}
function storePersistentValue(key,value){try{localStorage.setItem(key,value)}catch(_){return false}return true}
let receiverDeviceId=persistentValue(receiverDeviceIdKey);
if(!receiverDeviceId){receiverDeviceId=crypto.randomUUID?crypto.randomUUID():'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.random()*16|0;return(c==='x'?r:(r&3|8)).toString(16)});storePersistentValue(receiverDeviceIdKey,receiverDeviceId)}
const items=document.getElementById('items'),crumb=document.getElementById('crumb'),folderDownload=document.getElementById('folderDownload');
const allDownload=document.getElementById('allDownload'),selectedDownload=document.getElementById('selectedDownload');
const selectedCount=document.getElementById('selectedCount'),selectedForm=document.getElementById('selectedForm'),selectVisible=document.getElementById('selectVisible'),backButton=document.getElementById('backButton');
const selectedPaths=new Set();let downloadsAllowed=false;let folderHistory=current?[current.includes('/')?current.slice(0,current.lastIndexOf('/')):'']:[];document.getElementById('backButton').disabled=!folderHistory.length;
function sizeLabel(bytes){if(bytes<1024)return bytes+' B';if(bytes<1048576)return (bytes/1024).toFixed(1)+' KB';if(bytes<1073741824)return (bytes/1048576).toFixed(1)+' MB';return (bytes/1073741824).toFixed(1)+' GB'}
function updateSelectedAction(){selectedCount.textContent=selectedPaths.size?` (${selectedPaths.size})`:'';selectedDownload.disabled=!downloadsAllowed||selectedPaths.size===0}
function selectVisibleItems(){for(const checkbox of items.querySelectorAll('.file-select')){checkbox.checked=true;selectedPaths.add(checkbox.value)}updateSelectedAction()}
function navigate(path){if(path!==current)load(path)}
function goBack(){if(!folderHistory.length)return;load(folderHistory.pop(),false)}
function goHome(){folderHistory=[];load('',false)}
async function load(path='',remember=true){if(remember&&path!==current)folderHistory.push(current);current=path;backButton.disabled=folderHistory.length===0;crumb.textContent=path||'Home';folderDownload.hidden=!path;folderDownload.disabled=true;allDownload.disabled=true;document.getElementById('title').textContent=path?path.split('/').pop():'Shared files';
try{const u='/api/files'+(path?'?path='+encodeURIComponent(path):'');const response=await fetch(u);const d=await response.json();if(!response.ok||!d.items)throw Error(d.error||'Could not load shared files.');
downloadsAllowed=d.downloads_enabled!==false;folderDownload.disabled=!downloadsAllowed;allDownload.disabled=!downloadsAllowed;updateSelectedAction();
items.innerHTML='';
if(!d.items.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='This folder is empty.';items.append(empty)}
for(const [index,x] of d.items.entries()){const row=document.createElement('div');row.className='row';row.style.animationDelay=Math.min(index*35,350)+'ms';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.className='file-select';checkbox.value=x.path;checkbox.setAttribute('aria-label','Select '+(x.type==='directory'?'folder ':'file ')+x.name);checkbox.checked=selectedPaths.has(x.path);checkbox.onchange=()=>{if(checkbox.checked)selectedPaths.add(x.path);else selectedPaths.delete(x.path);updateSelectedAction()};row.append(checkbox);const icon=document.createElement('div');icon.className='icon';if(x.type==='directory'){const folderIcon=document.createElement('img');folderIcon.src='/asset/icon/folder.png';folderIcon.alt='';icon.append(folderIcon)}else{icon.textContent=/\\.(mp4|webm|ogv|ogg|mov|m4v)$/i.test(x.name)?'▶':'📄'}const detail=document.createElement('div');detail.className='details';const name=document.createElement('div');name.className='name';name.textContent=x.name;const meta=document.createElement('div');meta.className='meta';meta.textContent=x.type==='directory'?'Folder · select to download or open to browse':(x.mime||'File')+' · '+sizeLabel(x.size||0);detail.append(name,meta);row.append(icon,detail);
if(x.type==='directory'){const open=document.createElement('a');open.href='#';open.textContent='Open';open.onclick=e=>{e.preventDefault();navigate(x.path)};row.append(open)}else{const dl=document.createElement(downloadsAllowed?'a':'span');if(downloadsAllowed)dl.href='/api/download?path='+encodeURIComponent(x.path);dl.textContent=downloadsAllowed?'Download':'Disabled';row.append(dl);if(/\.(mp4|webm|ogv|ogg|mov|m4v)$/i.test(x.name)){const preview=document.createElement('a');preview.href='#';preview.textContent='Preview';preview.onclick=e=>{e.preventDefault();play(x.path,x.name)};row.append(preview)}}items.append(row)}
}catch(e){items.innerHTML='';const err=document.createElement('div');err.className='error';err.textContent=e.message;items.append(err)}}
function downloadZip(path){const q=path?'?path='+encodeURIComponent(path):'';location.href='/api/download-zip'+q}
function downloadSelected(){if(!selectedPaths.size||!downloadsAllowed)return;selectedForm.replaceChildren();for(const path of selectedPaths){const input=document.createElement('input');input.type='hidden';input.name='path';input.value=path;selectedForm.append(input)}selectedForm.submit()}
function play(path,name){const p=document.getElementById('player');p.innerHTML='';const h=document.createElement('h2');h.textContent=name;const v=document.createElement('video');v.controls=true;v.preload='metadata';v.src='/api/stream?path='+encodeURIComponent(path);p.append(h,v);p.scrollIntoView({behavior:'smooth'})}
function openReceiverNameDialog(){receiverNameInput.value=persistentValue(receiverNameKey)||'';receiverNameError.textContent='';receiverNameDialog.showModal();receiverNameInput.focus()}
async function submitReceiverName(event){event.preventDefault();const displayName=receiverNameInput.value.trim();if(!displayName)return;try{const response=await fetch('/api/receiver/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:receiverDeviceId,name:displayName})}),data=await response.json();if(!response.ok)throw Error(data.error||'Could not save device name.');storePersistentValue(receiverNameKey,displayName);receiverIdentity.textContent=displayName;receiverNameDialog.close();receiverApp.inert=false;load(current)}catch(error){receiverNameError.textContent=error.message}}
async function initializeReceiver(){const savedName=persistentValue(receiverNameKey);if(savedName){receiverNameInput.value=savedName;try{const response=await fetch('/api/receiver/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_id:receiverDeviceId,name:savedName})});if(!response.ok)throw Error('Could not refresh receiver profile.');receiverIdentity.textContent=savedName;receiverApp.inert=false;load(current);return}catch(error){receiverNameError.textContent=error.message}}openReceiverNameDialog()}
initializeReceiver();
</script></body></html>"""

def cfg():
    return load_config()

def is_loopback():
    try:
        return ipaddress.ip_address(
            normalized_client_address(request.remote_addr or "")
        ).is_loopback
    except ValueError:
        return False

def admin_only():
    if not is_loopback():
        abort(403, description="Dashboard administration is available only from this computer.")

def normalized_client_address(address):
    try:
        parsed = ipaddress.ip_address(address)
        return str(getattr(parsed, "ipv4_mapped", None) or parsed)
    except (TypeError, ValueError):
        return address

def expire_pauses(config, now=None):
    now = now or datetime.now(timezone.utc)
    expiries = config.setdefault("pause_expiries", {})
    paused = set(config.get("paused_clients", []))
    changed = False
    for address, expires_at in list(expiries.items()):
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expiry = now
        if expiry <= now:
            expiries.pop(address, None)
            paused.discard(address)
            changed = True
    if changed:
        config["paused_clients"] = sorted(paused)
    return changed

def select_path_dialog(kind):
    import tkinter as tk
    from tkinter import filedialog

    dialog = None
    try:
        dialog = tk.Tk()
        dialog.withdraw()
        dialog.attributes("-topmost", True)
        return (filedialog.askdirectory(parent=dialog, title="Choose a shared folder")
                if kind == "folder"
                else filedialog.askopenfilename(parent=dialog, title="Choose a shared file"))
    except tk.TclError as exc:
        raise RuntimeError(f"Native picker could not start: {exc}") from exc
    finally:
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass

def authorized(config):
    password = config.get("password") or ""
    if not password:
        return True
    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            _, supplied = decoded.split(":", 1)
            return hmac.compare_digest(supplied, password)
        except Exception:
            return False
    return False

PAUSED_RECEIVER_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f4f7f4"><title>{{ 'Access blocked' if blocked else '404 · Sharing unavailable' }} — Web File Sender</title><style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#17211f;background:#f4f7f4;font-synthesis:none}
*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;padding:24px;background:radial-gradient(ellipse at 50% 0,#e8f4e5 0,transparent 55%),#f4f7f4}
.card{width:min(100%,440px);padding:34px 30px;text-align:center;background:#fff;border:1px solid #e2eae3;border-radius:22px;box-shadow:0 18px 55px #1b36220a;animation:arrive .45s ease-out both}
.mark{width:58px;height:58px;margin:0 auto 20px;display:grid;place-items:center;border-radius:18px;background:#e8f4e5;color:#367c39;font-size:25px}
.blocked .mark{background:#fff0ef;color:#bd493e}.blocked .timer strong{color:#bd493e}
h1{margin:0;color:#1d2939;font-size:22px;letter-spacing:-.035em}.copy{margin:10px 0 0;color:#718096;font-size:14px;line-height:1.65}
.timer{margin:24px 0 0;padding:15px;border:1px solid #e2eae3;border-radius:13px;background:#f4f7f4;color:#5d6d64;font-size:12px}
.timer strong{display:block;margin-top:5px;color:#367c39;font-size:20px;font-variant-numeric:tabular-nums;letter-spacing:.02em}
.blocked .timer{border-color:#f2dfdc;background:#fff8f7}
.reason{margin:18px 0 0;padding:13px 15px;text-align:left;border:1px solid #f2dfdc;border-radius:12px;background:#fff8f7;color:#70443f;font-size:13px;line-height:1.55;overflow-wrap:anywhere}
.reason strong{display:block;margin-bottom:4px;color:#8f352e;font-size:11px;letter-spacing:.04em;text-transform:uppercase}
.appeal-form{margin-top:18px;text-align:left}.appeal-form label{display:block;margin-bottom:6px;color:#33443b;font-size:12px;font-weight:650}
.appeal-form textarea{width:100%;min-height:100px;padding:11px 12px;border:1px solid #dbe5dc;border-radius:10px;background:#fbfdfb;font:inherit;font-size:13px;resize:vertical}
.appeal-form textarea:focus{outline:2px solid #9be27c80;border-color:#73ad62}
.appeal-form button{margin-top:9px;padding:10px 14px;border:0;border-radius:9px;background:#367c39;color:#fff;font:inherit;font-size:13px;font-weight:650;cursor:pointer}
.appeal-form button:hover{background:#28652d}.appeal-status{min-height:18px;margin-top:8px;color:#5d6d64;font-size:12px}
.foot{margin-top:24px;color:#9aa7b8;font-size:11px;letter-spacing:.08em;text-transform:uppercase}
@keyframes arrive{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:480px){.card{padding:28px 22px}}
{% if blocked %}</style></head><body><main class="card blocked"><div class="mark" aria-hidden="true">!</div><h1>Access blocked</h1><p class="copy">This device has been blocked by the sender. Contact the sender if you think this was a mistake.</p>{% if block_reason %}<div class="reason"><strong>Reason</strong>{{ block_reason }}</div>{% endif %}<div class="timer">Access will remain blocked until the sender unblocks this device.</div><form class="appeal-form" onsubmit="submitAppeal(event)"><label for="appealMessage">Think this is a mistake? Send an appeal to the sender.</label><textarea id="appealMessage" minlength="10" maxlength="1000" required placeholder="Explain why you are appealing this block"></textarea><button type="submit">Submit appeal</button><div id="appealStatus" class="appeal-status" role="status"></div></form><div class="foot">Web File Sender</div><script>async function submitAppeal(event){event.preventDefault();const status=document.getElementById('appealStatus'),message=document.getElementById('appealMessage').value;status.textContent='Sending appeal…';try{const response=await fetch('/api/appeals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})}),data=await response.json();if(!response.ok)throw Error(data.error||'Could not submit appeal.');document.querySelector('.appeal-form').hidden=true;status.textContent='Your appeal has been sent to the sender.'}catch(error){status.textContent=error.message}}</script></main></body></html>
{% else %}</style></head><body><main class="card"><div class="mark" aria-hidden="true">404</div><h1>404 · Sharing unavailable</h1><p class="copy">The shared files are temporarily unavailable. Please try again later.</p><div class="timer">This page may become available when sharing resumes.</div>
<div class="foot">Web File Sender</div></main></body></html>{% endif %}"""

def restricted_receiver_response(config, address, blocked=False):
    reasons = {
        normalized_client_address(client): reason
        for client, reason in config.get("blocked_client_reasons", {}).items()
    }
    return Response(
        render_template_string(
            PAUSED_RECEIVER_HTML,
            blocked=blocked,
            block_reason=reasons.get(address, ""),
        ),
        status=403 if blocked else 404,
        mimetype="text/html",
    )

def require_auth(config):
    if expire_pauses(config):
        save_config(config)
    address = normalized_client_address(request.remote_addr or "")
    blocked = {
        normalized_client_address(client)
        for client in config.get("blocked_clients", [])
    }
    paused = {
        normalized_client_address(client)
        for client in config.get("paused_clients", [])
    }
    if address in blocked:
        if request.path == "/":
            return restricted_receiver_response(config, address, blocked=True)
        return Response("This device has been blocked by the sender.", 403)
    if address in paused:
        if request.path == "/":
            return restricted_receiver_response(config, address)
        return Response("Sharing is paused for this device.", 403)
    if not authorized(config):
        return Response("Password required. Use browser basic-auth prompt or receiver with credentials.", 401,
                        {"WWW-Authenticate": 'Basic realm="Web File Sender"'})
    return None

def recent_devices(config, events=None):
    latest_by_address = {}
    for event in events if events is not None else recent_events():
        address = normalized_client_address(event.get("client", ""))
        if not address or address in ("127.0.0.1", "::1"):
            continue
        timestamp = event.get("time", "")
        if address not in latest_by_address or timestamp > latest_by_address[address]:
            latest_by_address[address] = timestamp
    registered = config.get("receiver_devices", {})
    names_by_address = {}
    for profile in registered.values() if isinstance(registered, dict) else ():
        if not isinstance(profile, dict) or not isinstance(profile.get("address"), str):
            continue
        address = normalized_client_address(profile["address"])
        timestamp = profile.get("last_seen", "No recent activity")
        current_name = names_by_address.get(address)
        if current_name is None or timestamp > current_name["last_seen"]:
            names_by_address[address] = {
                "name": profile.get("name", "Receiver"),
                "last_seen": timestamp,
            }
        if address not in ("127.0.0.1", "::1"):
            previous = latest_by_address.get(address, "")
            if previous == "" or timestamp > previous:
                latest_by_address[address] = timestamp

    blocked = {
        normalized_client_address(address)
        for address in config.get("blocked_clients", [])
    }
    paused = {
        normalized_client_address(address)
        for address in config.get("paused_clients", [])
    }
    for address in blocked | paused:
        latest_by_address.setdefault(address, "No recent activity")
    expiries = {
        normalized_client_address(address): expiry
        for address, expiry in config.get("pause_expiries", {}).items()
    }
    block_reasons = {
        normalized_client_address(address): reason
        for address, reason in config.get("blocked_client_reasons", {}).items()
    }
    now = datetime.now(timezone.utc)
    devices = []
    for address, last_seen in latest_by_address.items():
        if last_seen == "No recent activity":
            age = 61
        else:
            try:
                seen_at = datetime.fromisoformat(last_seen)
                if seen_at.tzinfo is None:
                    seen_at = seen_at.astimezone()
                age = max(0, (now - seen_at.astimezone(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                age = 61
        state = "Blocked" if address in blocked else "Paused" if address in paused else "Active" if age <= 60 else "Recently active"
        pause_remaining = ""
        pause_remaining_seconds = None
        if address in paused and address in expiries:
            try:
                expiry = datetime.fromisoformat(expiries[address])
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                seconds = max(0, int((expiry - now).total_seconds()))
                pause_remaining_seconds = seconds
                pause_remaining = (f"{seconds // 3600}h {seconds % 3600 // 60}m" if seconds >= 3600
                                   else f"{max(1, seconds // 60)}m")
            except (TypeError, ValueError):
                pause_remaining = ""
        devices.append({
            "address": address,
            "name": names_by_address.get(address, {}).get("name", ""),
            "speed_limit_kbps": config.get("device_speed_limits_kbps", {}).get(address, 0),
            "last_seen": last_seen,
            "status": state,
            "blocked": address in blocked,
            "block_reason": block_reasons.get(address, ""),
            "paused": address in paused,
            "pause_remaining": pause_remaining,
            "pause_remaining_seconds": pause_remaining_seconds,
        })
    return sorted(devices, key=lambda device: device["address"])

def device_name_for_address(config, address):
    profiles = config.get("receiver_devices", {})
    if not isinstance(profiles, dict):
        return ""
    matches = [
        profile for profile in profiles.values()
        if isinstance(profile, dict)
        and normalized_client_address(profile.get("address", "")) == address
        and isinstance(profile.get("name"), str)
    ]
    return max(matches, key=lambda profile: profile.get("last_seen", ""))["name"] if matches else ""

def share_availability(path):
    if not isinstance(path, str) or not path.strip():
        return "Invalid path"
    target = Path(path).expanduser()
    try:
        if target.exists():
            return "Available"
    except OSError:
        return "Path inaccessible"
    if os.name == "nt":
        drive, _tail = os.path.splitdrive(str(target))
        if drive and not Path(drive + "\\").exists():
            return "Drive unavailable"
    elif str(target).startswith(("/mnt/", "/media/", "/Volumes/")):
        return "Drive or mount unavailable"
    return "File or folder not found"

def track_download_response(response, address, path, config):
    if request.method != "GET":
        return response
    try:
        content_range = response.headers.get("Content-Range", "")
        total_bytes = (
            int(content_range.rsplit("/", 1)[1])
            if "/" in content_range else int(response.headers.get("Content-Length", "0"))
        )
    except ValueError:
        total_bytes = 0
    if total_bytes <= 0:
        return response

    transfer_id = start_transfer(
        address, path, total_bytes,
        config.get("device_speed_limits_kbps", {}).get(address, 0),
    )
    original = response.response

    def monitored_body():
        try:
            for chunk in original:
                if not chunk:
                    continue
                offset = 0
                while offset < len(chunk):
                    chunk_size = transfer_chunk_size(transfer_id, len(chunk) - offset)
                    piece = chunk[offset:offset + chunk_size]
                    if not pace_transfer_chunk(transfer_id, len(piece)):
                        return
                    update_transfer(transfer_id, len(piece))
                    yield piece
                    offset += len(piece)
        finally:
            close = getattr(original, "close", None)
            if close:
                close()
            finish_transfer(transfer_id)

    response.response = monitored_body()
    response.direct_passthrough = False
    response.call_on_close(lambda: finish_transfer(transfer_id))
    return response

@app.get("/")
def home():
    if is_loopback():
        return admin()
    config = cfg()
    denied = require_auth(config)
    if denied: return denied
    return render_template_string(RECEIVER_HTML)

@app.post("/api/receiver/profile")
def save_receiver_profile():
    config = cfg()
    denied = require_auth(config)
    if denied:
        return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error":"A device ID and name are required."}), 400
    device_id = data.get("device_id")
    name = data.get("name")
    if not isinstance(device_id, str) or not isinstance(name, str):
        return jsonify({"error":"A device ID and name are required."}), 400
    try:
        device_id = str(uuid.UUID(device_id))
    except ValueError:
        return jsonify({"error":"Receiver device ID is invalid."}), 400
    name = name.strip()
    if not 1 <= len(name) <= 60:
        return jsonify({"error":"Device name must be between 1 and 60 characters."}), 400
    devices = config.setdefault("receiver_devices", {})
    if not isinstance(devices, dict):
        return jsonify({"error":"Receiver device registry is invalid."}), 500
    devices[device_id] = {
        "name": name,
        "address": normalized_client_address(request.remote_addr or ""),
        "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if len(devices) > 500:
        oldest = sorted(
            devices,
            key=lambda key: devices[key].get("last_seen", "")
            if isinstance(devices[key], dict) else "",
        )[:len(devices) - 500]
        for key in oldest:
            devices.pop(key, None)
    save_config(config)
    return jsonify({"ok":True})

@app.get("/asset/icon/<filename>")
def app_icon(filename):
    if filename not in ICON_FILES:
        abort(404)
    return send_from_directory(ICON_DIRECTORY, filename, mimetype="image/png")

@app.get("/admin")
def admin():
    if not is_loopback():
        abort(403, description="Dashboard administration is available only from this computer.")
    return render_template_string(ADMIN_HTML)

@app.post("/api/admin/pick-path")
def admin_pick_path():
    admin_only()
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in ("folder", "file"):
        return jsonify({"error":"Choose either a folder or a file."}), 400
    try:
        selected = select_path_dialog(kind)
        return jsonify({"path":str(Path(selected).resolve()) if selected else ""})
    except (ImportError, OSError, RuntimeError) as exc:
        return jsonify({"error":f"Could not open the native file picker: {exc}"}), 500

@app.get("/api/health")
def health():
    c = cfg()
    return jsonify({
        "service":"web-file-sender",
        "name":c["name"],
        "port":c["port"],
        "receiver_url":f"http://{local_ip()}:{c['port']}/",
    })

@app.get("/api/files")
def api_files():
    c = cfg()
    denied = require_auth(c)
    if denied: return denied
    path = request.args.get("path", "")
    try:
        if not path:
            items = root_items(c)
        else:
            target = resolve_virtual_path(path, c)
            if target is None or not target.is_dir():
                raise PermissionError("Folder not found.")
            items = list_directory(target, c)
        log_event("list", path or "/", request.remote_addr or "")
        return jsonify({"items":items, "downloads_enabled":c.get("downloads_enabled", True)})
    except (PermissionError, OSError, ValueError) as exc:
        return jsonify({"error":str(exc)}), 403

def serve_item(stream=False):
    c = cfg()
    denied = require_auth(c)
    if denied: return denied
    if stream and not c.get("streaming_enabled", True):
        abort(403, description="Video streaming is disabled.")
    if not stream and not c.get("downloads_enabled", True):
        abort(403, description="Downloads are disabled.")
    try:
        path = request.args.get("path", "")
        target = resolve_virtual_path(path, c)
        if target is None or not target.is_file() or not allowed_path(target, c):
            abort(404)
        if stream and target.suffix.lower() not in VIDEO_EXTENSIONS:
            abort(415, description="Inline streaming is supported for common video formats only.")
        log_event("stream" if stream else "download", path, request.remote_addr or "")
        mimetype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if stream:
            response = send_file(target, mimetype=mimetype, conditional=True, as_attachment=False, download_name=target.name)
            response.headers["Accept-Ranges"] = "bytes"
            response.headers["Cache-Control"] = "no-cache"
            return response
        response = send_file(
            target, mimetype=mimetype, conditional=True, as_attachment=True,
            download_name=target.name,
        )
        return track_download_response(
            response, normalized_client_address(request.remote_addr or ""), path, c,
        )
    except PermissionError as exc:
        return jsonify({"error":str(exc)}), 403
    except FileNotFoundError:
        abort(404)

@app.get("/api/download")
def api_download():
    return serve_item(False)

def selected_files(paths, config):
    files = {}
    visited_directories = set()
    pending = list(paths)
    while pending:
        virtual = pending.pop()
        target = resolve_virtual_path(virtual, config)
        if target is None or not (target.is_file() or target.is_dir()):
            raise ValueError("Selected items must be shared files or folders.")
        if not allowed_path(target, config):
            raise PermissionError("A selected item is no longer available.")
        if target.is_dir():
            directory_key = str(target)
            if directory_key in visited_directories:
                continue
            visited_directories.add(directory_key)
            pending.extend(item["path"] for item in list_directory(target, config))
        else:
            archive_name = virtual_path(target, config)
            if not archive_name:
                raise PermissionError("A selected file is no longer shared.")
            files[str(target)] = (archive_name, target)
            if len(files) > 10000:
                raise ValueError("The selection contains more than 10000 files.")
    return list(files.values())

@app.post("/api/download-selected")
def api_download_selected():
    c = cfg()
    denied = require_auth(c)
    if denied:
        return denied
    if not c.get("downloads_enabled", True):
        abort(403, description="Downloads are disabled.")

    paths = list(dict.fromkeys(request.form.getlist("path")))
    if not paths:
        return jsonify({"error":"Select at least one file to download."}), 400
    if len(paths) > 5000:
        return jsonify({"error":"Select no more than 5000 files at a time."}), 400

    archive_path = None
    try:
        files = selected_files(paths, c)
        if not files:
            return jsonify({"error":"The selected folders contain no downloadable files."}), 400
        if len(files) == 1:
            archive_name, target = files[0]
            log_event("download-selected", archive_name, request.remote_addr or "")
            response = send_file(
                target, mimetype=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                as_attachment=True, download_name=target.name, conditional=True,
            )
            return track_download_response(
                response, normalized_client_address(request.remote_addr or ""), archive_name, c,
            )

        fd, archive_path = tempfile.mkstemp(prefix="web-file-sender-selected-", suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for virtual, target in files:
                archive.write(target, arcname=virtual)

        log_event("download-selected", f"{len(files)} file(s)", request.remote_addr or "")
        response = send_file(archive_path, mimetype="application/zip", as_attachment=True,
                             download_name="selected-files.zip", conditional=True)
        response = track_download_response(
            response, normalized_client_address(request.remote_addr or ""),
            f"{len(files)} selected files", c,
        )
        temp_path = archive_path
        response.call_on_close(lambda: os.path.exists(temp_path) and os.unlink(temp_path))
        archive_path = None
        return response
    except PermissionError as exc:
        return jsonify({"error":str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error":str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error":str(exc)}), 403
    except FileNotFoundError:
        abort(404)
    finally:
        if archive_path and os.path.exists(archive_path):
            os.unlink(archive_path)

@app.get("/api/download-zip")
def api_download_zip():
    c = cfg()
    denied = require_auth(c)
    if denied:
        return denied
    if not c.get("downloads_enabled", True):
        abort(403, description="Downloads are disabled.")

    virtual = request.args.get("path", "")
    archive_path = None
    try:
        if virtual:
            target = resolve_virtual_path(virtual, c)
            if target is None or not (target.is_file() or target.is_dir()) or not allowed_path(target, c):
                abort(404)
            items = [{
                "name": target.name,
                "type": "directory" if target.is_dir() else "file",
                "path": virtual,
            }]
        else:
            items = root_items(c)

        fd, archive_path = tempfile.mkstemp(prefix="web-file-sender-", suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            pending = list(items)
            while pending:
                item = pending.pop()
                item_path = resolve_virtual_path(item["path"], c)
                if item["type"] == "directory":
                    pending.extend(list_directory(item_path, c))
                elif item_path.is_file() and allowed_path(item_path, c):
                    archive.write(item_path, arcname=item["path"])

        filename = secure_filename(Path(virtual).name if virtual else "shared-files") or "shared-files"
        log_event("download-zip", virtual or "/", request.remote_addr or "")
        response = send_file(archive_path, mimetype="application/zip", as_attachment=True,
                             download_name=f"{filename}.zip", conditional=True)
        response = track_download_response(
            response, normalized_client_address(request.remote_addr or ""), virtual or "Shared files", c,
        )
        temp_path = archive_path
        response.call_on_close(lambda: os.path.exists(temp_path) and os.unlink(temp_path))
        archive_path = None
        return response
    except PermissionError as exc:
        return jsonify({"error":str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error":str(exc)}), 403
    except FileNotFoundError:
        abort(404)
    finally:
        if archive_path and os.path.exists(archive_path):
            os.unlink(archive_path)

@app.get("/api/stream")
def api_stream():
    return serve_item(True)

@app.get("/api/admin/config")
def admin_config():
    admin_only()
    config = cfg()
    if expire_pauses(config):
        save_config(config)
    events = recent_events()
    safe_config = {
        key: value for key, value in config.items()
        if key not in ("admin_username", "admin_password_hash", "admin_session_key")
    }
    safe_config["shares"] = [
        {**share, "availability": share_availability(share.get("path", ""))}
        for share in config.get("shares", [])
    ]
    named_transfers = active_transfers()
    for transfer in named_transfers:
        transfer["device_name"] = device_name_for_address(
            config, transfer["address"]
        ) or transfer["address"]
    return jsonify({
        "config":safe_config,
        "events":events,
        "devices":recent_devices(config, events),
        "transfers":named_transfers,
        "appeals":config.get("appeals", []),
        "receiver_url":f"http://{local_ip()}:{config['port']}/",
    })

@app.get("/api/admin/receiver-qr.svg")
def admin_receiver_qr():
    admin_only()
    config = cfg()
    receiver_url = f"http://{local_ip()}:{config['port']}/"
    import qrcode
    from qrcode.image.svg import SvgPathImage

    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    code.add_data(receiver_url)
    code.make(fit=True)
    image = code.make_image(image_factory=SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    return Response(output.getvalue(), mimetype="image/svg+xml", headers={"Cache-Control":"no-store"})

@app.get("/api/admin/config/export")
def admin_export_config():
    admin_only()
    config = cfg()
    payload = {
        key: value for key, value in config.items()
        if key in DEFAULT_CONFIG
    }
    response = jsonify(payload)
    response.headers["Content-Disposition"] = 'attachment; filename="web-file-sender-settings.json"'
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/api/admin/config/restore")
def admin_restore_config():
    admin_only()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error":"Choose a valid settings backup JSON file."}), 400

    restored = dict(DEFAULT_CONFIG)
    restored.update({key: value for key, value in data.items() if key in DEFAULT_CONFIG})
    if not isinstance(restored["name"], str) or len(restored["name"]) > 80:
        return jsonify({"error":"Server name must be 80 characters or fewer."}), 400
    if not isinstance(restored["host"], str) or len(restored["host"]) > 253:
        return jsonify({"error":"Backup server host is invalid."}), 400
    if (not isinstance(restored["port"], int) or isinstance(restored["port"], bool)
            or not 1024 <= restored["port"] <= 65535):
        return jsonify({"error":"Backup port must be between 1024 and 65535."}), 400
    device_limits = restored["device_speed_limits_kbps"]
    if not isinstance(device_limits, dict) or len(device_limits) > 1000:
        return jsonify({"error":"Backup device speed limits are invalid."}), 400
    normalized_limits = {}
    try:
        for address, limit in device_limits.items():
            if (not isinstance(address, str) or not isinstance(limit, int)
                    or isinstance(limit, bool) or not 0 <= limit <= 1000000):
                return jsonify({"error":"Backup device speed limits are invalid."}), 400
            normalized_limits[normalized_client_address(
                str(ipaddress.ip_address(address))
            )] = limit
    except ValueError:
        return jsonify({"error":"Backup device speed limits contain an invalid IP address."}), 400
    restored["device_speed_limits_kbps"] = normalized_limits
    receiver_devices = restored["receiver_devices"]
    if not isinstance(receiver_devices, dict) or len(receiver_devices) > 500:
        return jsonify({"error":"Backup receiver device names are invalid."}), 400
    try:
        for device_id, profile in receiver_devices.items():
            if (str(uuid.UUID(device_id)) != device_id or not isinstance(profile, dict)
                    or not isinstance(profile.get("name"), str)
                    or not 1 <= len(profile["name"]) <= 60
                    or not isinstance(profile.get("address"), str)
                    or not isinstance(profile.get("last_seen"), str)):
                return jsonify({"error":"A saved receiver device entry in the backup is invalid."}), 400
            profile["address"] = normalized_client_address(
                str(ipaddress.ip_address(profile["address"]))
            )
            datetime.fromisoformat(profile["last_seen"])
    except (ValueError, TypeError):
        return jsonify({"error":"A saved receiver device entry in the backup is invalid."}), 400
    for key in ("downloads_enabled", "streaming_enabled", "hidden_files"):
        if not isinstance(restored[key], bool):
            return jsonify({"error":f"Backup setting {key} must be true or false."}), 400
    if not isinstance(restored["password"], str) or len(restored["password"]) > 256:
        return jsonify({"error":"Backup receiver password is invalid."}), 400
    if not isinstance(restored["shares"], list) or len(restored["shares"]) > 500:
        return jsonify({"error":"Backup must contain no more than 500 shared paths."}), 400
    for share in restored["shares"]:
        if (not isinstance(share, dict) or not isinstance(share.get("path"), str)
                or not Path(share["path"]).is_absolute()
                or not isinstance(share.get("name"), str) or not share["name"].strip()
                or "/" in share["name"] or "\\" in share["name"]):
            return jsonify({"error":"A shared path entry in the backup is invalid."}), 400
    if not isinstance(restored["rules"], list) or len(restored["rules"]) > 500:
        return jsonify({"error":"Backup must contain no more than 500 exclusion rules."}), 400
    for rule in restored["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("path"), str):
            return jsonify({"error":"An exclusion rule in the backup is invalid."}), 400
        rule["action"] = "deny"
    for key in ("blocked_clients", "paused_clients"):
        if not isinstance(restored[key], list) or len(restored[key]) > 1000:
            return jsonify({"error":f"Backup {key} list is invalid."}), 400
        if any(not isinstance(address, str) for address in restored[key]):
            return jsonify({"error":f"Backup {key} contains a non-text IP address."}), 400
        try:
            restored[key] = sorted({
                normalized_client_address(str(ipaddress.ip_address(address)))
                for address in restored[key]
            })
        except (TypeError, ValueError):
            return jsonify({"error":f"Backup {key} contains an invalid IP address."}), 400
    reasons = restored["blocked_client_reasons"]
    if not isinstance(reasons, dict) or len(reasons) > 1000:
        return jsonify({"error":"Backup block reasons are invalid."}), 400
    try:
        normalized_reasons = {}
        for address, reason in reasons.items():
            if not isinstance(address, str) or not isinstance(reason, str) or len(reason) > 300:
                return jsonify({"error":"Backup block reasons are invalid."}), 400
            normalized_reasons[normalized_client_address(str(ipaddress.ip_address(address)))] = reason
        restored["blocked_client_reasons"] = normalized_reasons
    except (TypeError, ValueError):
        return jsonify({"error":"Backup block reasons contain an invalid IP address."}), 400
    if not isinstance(restored["pause_expiries"], dict) or len(restored["pause_expiries"]) > 1000:
        return jsonify({"error":"Backup pause timers are invalid."}), 400
    expiries = {}
    try:
        for address, expiry in restored["pause_expiries"].items():
            if not isinstance(address, str) or not isinstance(expiry, str):
                return jsonify({"error":"Backup pause timers contain an invalid address or date."}), 400
            normalized = normalized_client_address(str(ipaddress.ip_address(address)))
            datetime.fromisoformat(expiry)
            expiries[normalized] = expiry
    except (TypeError, ValueError):
        return jsonify({"error":"Backup pause timers contain an invalid address or date."}), 400
    restored["pause_expiries"] = expiries
    if not isinstance(restored["appeals"], list) or len(restored["appeals"]) > 200:
        return jsonify({"error":"Backup appeal list is invalid."}), 400
    for appeal in restored["appeals"]:
        if (not isinstance(appeal, dict) or not isinstance(appeal.get("id"), str)
                or not isinstance(appeal.get("message"), str)
                or not isinstance(appeal.get("address"), str)
                or not 10 <= len(appeal["message"].strip()) <= 1000
                or not isinstance(appeal.get("submitted_at"), str)):
            return jsonify({"error":"An appeal entry in the backup is invalid."}), 400
        try:
            ipaddress.ip_address(appeal["address"])
            datetime.fromisoformat(appeal["submitted_at"])
        except ValueError:
            return jsonify({"error":"An appeal entry in the backup is invalid."}), 400

    save_config(restored)
    for address in {
        transfer["address"] for transfer in active_transfers()
    } | set(normalized_limits):
        set_device_speed_limit(address, normalized_limits.get(address, 0))
    return jsonify({"ok":True})

@app.post("/api/appeals")
def submit_device_appeal():
    config = cfg()
    address = normalized_client_address(request.remote_addr or "")
    blocked = {
        normalized_client_address(client)
        for client in config.get("blocked_clients", [])
    }
    if address not in blocked:
        return jsonify({"error":"Appeals are available only to blocked devices."}), 403
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    if not isinstance(message, str) or not 10 <= len(message.strip()) <= 1000:
        return jsonify({"error":"Appeal must be between 10 and 1000 characters."}), 400
    now = time.monotonic()
    with _appeal_lock:
        if len(_appeal_last_submitted) > 2048:
            for client, submitted in tuple(_appeal_last_submitted.items()):
                if now - submitted >= 60:
                    _appeal_last_submitted.pop(client, None)
        last_submitted = _appeal_last_submitted.get(address, 0)
        if now - last_submitted < 60:
            return jsonify({"error":"Please wait a minute before submitting another appeal."}), 429
        _appeal_last_submitted[address] = now
    appeals = config.setdefault("appeals", [])
    appeals.append({
        "id": uuid.uuid4().hex,
        "address": address,
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": message.strip(),
    })
    config["appeals"] = appeals[-200:]
    save_config(config)
    log_event("device-appeal", address, address)
    return jsonify({"ok":True})

@app.delete("/api/admin/appeals/<appeal_id>")
def admin_dismiss_appeal(appeal_id):
    admin_only()
    config = cfg()
    appeals = config.get("appeals", [])
    remaining = [appeal for appeal in appeals if appeal.get("id") != appeal_id]
    if len(remaining) == len(appeals):
        return jsonify({"error":"Appeal not found."}), 404
    config["appeals"] = remaining
    save_config(config)
    return jsonify({"ok":True})

@app.post("/api/admin/appeals/<appeal_id>/unblock")
def admin_unblock_appeal(appeal_id):
    admin_only()
    config = cfg()
    appeal = next(
        (entry for entry in config.get("appeals", []) if entry.get("id") == appeal_id),
        None,
    )
    if appeal is None:
        return jsonify({"error":"Appeal not found."}), 404
    address = normalized_client_address(appeal.get("address", ""))
    blocked = {
        normalized_client_address(client)
        for client in config.get("blocked_clients", [])
    }
    blocked.discard(address)
    config["blocked_clients"] = sorted(blocked)
    reasons = {
        normalized_client_address(client): reason
        for client, reason in config.get("blocked_client_reasons", {}).items()
    }
    reasons.pop(address, None)
    config["blocked_client_reasons"] = reasons
    config["appeals"] = [
        entry for entry in config.get("appeals", [])
        if normalized_client_address(entry.get("address", "")) != address
    ]
    save_config(config)
    log_event("device-unblock", address, request.remote_addr or "")
    return jsonify({"ok":True})

@app.post("/api/admin/transfers/<transfer_id>")
def admin_control_transfer(transfer_id):
    admin_only()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("pause", "resume", "cancel"):
        return jsonify({"error":"Choose pause, resume, or cancel."}), 400
    found = control_transfer_group(transfer_id, action)
    if not found:
        return jsonify({"error":"That download is no longer active."}), 404
    return jsonify({"ok":True})

@app.post("/api/admin/device-speed-limit")
def admin_device_speed_limit():
    admin_only()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error":"Enter a device address and speed limit."}), 400
    try:
        address = normalized_client_address(str(ipaddress.ip_address(
            str(data.get("address", "")).strip()
        )))
    except ValueError:
        return jsonify({"error":"Enter a valid device IP address."}), 400
    limit = data.get("limit_kbps")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 1000000:
        return jsonify({"error":"Device speed limit must be a whole number between 0 and 1000000 KB/s."}), 400
    config = cfg()
    limits = config.setdefault("device_speed_limits_kbps", {})
    if limit:
        limits[address] = limit
    else:
        limits.pop(address, None)
    save_config(config)
    set_device_speed_limit(address, limit)
    return jsonify({"ok":True})

@app.post("/api/admin/device-control")
def admin_device_control():
    admin_only()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    config = cfg()

    if action in ("unblock-all", "resume-all"):
        key = "blocked_clients" if action == "unblock-all" else "paused_clients"
        config[key] = []
        if action == "unblock-all":
            config["blocked_client_reasons"] = {}
            config["appeals"] = []
        if action == "resume-all":
            config["pause_expiries"] = {}
    elif action in ("block", "unblock", "pause", "resume"):
        try:
            address = str(ipaddress.ip_address(str(data.get("address", "")).strip()))
        except ValueError:
            return jsonify({"error":"Enter a valid device IP address."}), 400
        key = "blocked_clients" if action in ("block", "unblock") else "paused_clients"
        addresses = {
            normalized_client_address(client) for client in config.get(key, [])
        }
        if action == "pause":
            try:
                raw_duration = data.get("duration_seconds", 900)
                duration = int(raw_duration)
            except (TypeError, ValueError):
                return jsonify({"error":"Choose a valid pause duration."}), 400
            if isinstance(raw_duration, bool) or (
                isinstance(raw_duration, float) and not raw_duration.is_integer()
            ):
                return jsonify({"error":"Choose a whole number of minutes."}), 400
            if not 60 <= duration <= 2592000 or duration % 60:
                return jsonify({"error":"Pause duration must be a whole number of minutes between 1 minute and 30 days."}), 400
            addresses.add(address)
            config.setdefault("pause_expiries", {})[address] = (
                datetime.now(timezone.utc) + timedelta(seconds=duration)
            ).isoformat(timespec="seconds")
        elif action in ("block", "unblock"):
            if action == "block":
                reason = data.get("reason", "")
                if not isinstance(reason, str):
                    return jsonify({"error":"Block reason must be text."}), 400
                reason = reason.strip()
                if len(reason) > 300:
                    return jsonify({"error":"Block reason must be 300 characters or fewer."}), 400
                addresses.add(address)
                reasons = {
                    normalized_client_address(client): reason
                    for client, reason in config.get("blocked_client_reasons", {}).items()
                }
                config["blocked_client_reasons"] = reasons
                if reason:
                    reasons[address] = reason
                else:
                    reasons.pop(address, None)
            else:
                addresses.discard(address)
                reasons = {
                    normalized_client_address(client): reason
                    for client, reason in config.get("blocked_client_reasons", {}).items()
                }
                reasons.pop(address, None)
                config["blocked_client_reasons"] = reasons
                config["appeals"] = [
                    appeal for appeal in config.get("appeals", [])
                    if normalized_client_address(appeal.get("address", "")) != address
                ]
            config[key] = sorted(addresses)
        elif action == "resume":
            addresses.discard(address)
            config.setdefault("pause_expiries", {}).pop(address, None)
        else:
            return jsonify({"error":"Choose a valid device action."}), 400
        if action in ("pause", "resume"):
            config[key] = sorted(addresses)
    else:
        return jsonify({"error":"Choose a valid device action."}), 400

    save_config(config)
    log_event("device-" + action, data.get("address", ""), request.remote_addr or "")
    return jsonify({"ok":True})

@app.post("/api/admin/settings")
def admin_settings():
    admin_only()
    data = request.get_json(silent=True) or {}
    c = cfg()
    if "name" in data: c["name"] = str(data["name"])[:80] or "Web File Sender"
    if "port" in data:
        port = int(data["port"])
        if not 1024 <= port <= 65535: return jsonify({"error":"Port must be between 1024 and 65535."}), 400
        c["port"] = port
    for key in ("downloads_enabled","streaming_enabled","hidden_files"):
        if key in data: c[key] = bool(data[key])
    if "password" in data: c["password"] = str(data["password"])[:256]
    save_config(c)
    return jsonify({"ok":True})

@app.post("/api/admin/shares")
def admin_add_share():
    admin_only()
    data = request.get_json(silent=True) or {}
    raw = str(data.get("path", "")).strip()
    if not raw: return jsonify({"error":"Enter an absolute file or folder path."}), 400
    try:
        path = canonical(raw)
    except (OSError, RuntimeError):
        return jsonify({"error":"Path does not exist or cannot be accessed."}), 400
    if not (path.is_dir() or path.is_file()):
        return jsonify({"error":"Only files and folders can be shared."}), 400
    c = cfg()
    name = str(data.get("name") or path.name or "Shared").strip()
    if not name or "/" in name or "\\" in name or name in (".",".."):
        return jsonify({"error":"Virtual name must be a single folder/file label."}), 400
    if any(s.get("name") == name for s in c["shares"]):
        return jsonify({"error":"That virtual name is already used. Choose another."}), 400
    c["shares"].append({"path":str(path),"name":name})
    save_config(c)
    log_event("share-added", str(path), request.remote_addr or "")
    return jsonify({"ok":True})

@app.delete("/api/admin/shares/<int:index>")
def admin_remove_share(index):
    admin_only()
    c = cfg()
    if index < 0 or index >= len(c["shares"]): return jsonify({"error":"Share not found."}), 404
    removed = c["shares"].pop(index)
    save_config(c)
    log_event("share-removed", removed.get("path",""), request.remote_addr or "")
    return jsonify({"ok":True})

@app.post("/api/admin/rules")
def admin_add_rule():
    admin_only()
    data = request.get_json(silent=True) or {}
    raw = str(data.get("path","")).strip()
    if not raw: return jsonify({"error":"Enter an absolute path to exclude."}), 400
    # Deny rules can refer to paths that do not currently exist.
    path = str(Path(raw).expanduser().resolve(strict=False))
    c = cfg()
    c["rules"].append({"path":path,"action":"deny"})
    save_config(c)
    return jsonify({"ok":True})

@app.delete("/api/admin/rules/<int:index>")
def admin_remove_rule(index):
    admin_only()
    c = cfg()
    if index < 0 or index >= len(c["rules"]): return jsonify({"error":"Rule not found."}), 404
    c["rules"].pop(index)
    save_config(c)
    return jsonify({"ok":True})

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"

def discovery_broadcaster(port, stop):
    message = json.dumps({"service":"web-file-sender","name":cfg().get("name","Web File Sender"),"port":port}).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while not stop.is_set():
        try:
            sock.sendto(message, ("255.255.255.255", 6790))
        except OSError:
            pass
        stop.wait(3)
    sock.close()

def main():
    parser = argparse.ArgumentParser(description="Run the Web File Sender server.")
    parser.add_argument("--host", help="Bind address (default 0.0.0.0 for LAN sharing)")
    parser.add_argument("--port", type=int, help="Port (default 6789)")
    parser.add_argument("--share", action="append", help="Share a folder/file at startup; may be repeated")
    parser.add_argument("--name", default=None, help="Sender name shown to receivers")
    args = parser.parse_args()
    c = cfg()
    if args.host: c["host"] = args.host
    if args.port: c["port"] = args.port
    if args.name: c["name"] = args.name
    for raw in args.share or []:
        try:
            p = canonical(raw)
            if p.is_file() or p.is_dir():
                label = p.name or "Shared"
                if not any(s.get("name") == label for s in c["shares"]):
                    c["shares"].append({"path":str(p),"name":label})
        except OSError:
            print(f"Skipping unavailable path: {raw}")
    save_config(c)
    print(f"\n{c['name']} is starting.")
    print(f"Local dashboard: http://127.0.0.1:{c['port']}/")
    print(f"Receiver webpage: http://{local_ip()}:{c['port']}/")
    print("Keep this terminal open while sharing. Press Ctrl+C to stop.")
    print("Security: share only folders/files you intend to expose; LAN clients may access them.")
    stop = threading.Event()
    threading.Thread(target=discovery_broadcaster, args=(c["port"], stop), daemon=True).start()
    try:
        app.run(host=c.get("host","0.0.0.0"), port=c["port"], threaded=True, use_reloader=False)
    finally:
        stop.set()

if __name__ == "__main__":
    main()
