
"""LEGO Education Web IDE — device picker + motor control.

All device interaction happens in Python; buttons just trigger the calls.
Web Bluetooth's requestDevice() picker appears when search() runs because
the call chain stays within the user-gesture context (thanks to stack-switching).
"""

from pyscript import web, when, display
from datetime import datetime
import sys
import io
import re
import asyncio
import legoeducation as le

# ── Globals ──────────────────────────────────────────────────────────────────

_editor_globals = None  # persisted across editor runs
_panel_devices = {}  # panel_id -> device instance (managed by Device Panel)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _el(id):
	"""Return the raw JS DOM element by id."""
	from js import document
	return document.getElementById(id)

def log(msg, cls="log-info"):
	ts = datetime.now().strftime("%H:%M:%S")
	el = _el("log")
	el.innerHTML += f'<span class="{cls}">[{ts}] {msg}\n</span>'
	el.scrollTop = el.scrollHeight

def set_status(text, cls):
	bar = _el("status-bar")
	bar.innerText = text
	bar.className = f"status-bar status-{cls}"

def enable(*ids):
	for id in ids:
		_el(id).disabled = False

def disable(*ids):
	for id in ids:
		_el(id).disabled = True

def _setup_interrupt_buffer():
	"""Create a SharedArrayBuffer and register it with Pyodide so that
	writing 2 (SIGINT) into it raises KeyboardInterrupt between bytecodes."""
	global _interrupt_flag
	try:
		from js import crossOriginIsolated, SharedArrayBuffer, Int32Array
		import pyodide_js
		if not crossOriginIsolated:
			log("⚠ Not cross-origin isolated — Stop button won't work", "log-warn")
			return
		buf = SharedArrayBuffer.new(4)
		_interrupt_flag = Int32Array.new(buf)
		pyodide_js.setInterruptBuffer(_interrupt_flag)
		from js import globalThis
		globalThis._interruptFlag = _interrupt_flag
		log("Interrupt buffer configured ✓", "log-ok")
	except Exception as e:
		log(f"⚠ Could not set up interrupt buffer: {e}", "log-warn")

_interrupt_flag = None

def _reset_interrupt_buffer():
	"""Clear the interrupt flag so the next run isn't immediately interrupted."""
	if _interrupt_flag is not None:
		try:
			from js import Atomics
			Atomics.store(_interrupt_flag, 0, 0)
		except Exception:
			pass

def _disconnect_all_devices():
	"""Disconnect all connected LEGO Education devices except panel devices."""
	from legoeducation.basic_device import _BasicDevice
	panel_dev_ids = set(id(d) for d in _panel_devices.values())
	disconnected = []
	# Check the editor globals for any connected device instances
	if _editor_globals:
		for name, obj in list(_editor_globals.items()):
			if isinstance(obj, _BasicDevice) and obj.connected and id(obj) not in panel_dev_ids:
				try:
					obj.disconnect()
					disconnected.append(name)
				except Exception as e:
					log(f"⚠ Error disconnecting '{name}': {e}", "log-warn")
	if disconnected:
		log(f"Disconnected: {', '.join(disconnected)}", "log-ok")

def _disconnect_code_devices():
	"""Silently disconnect devices created via code (not panel devices)."""
	from legoeducation.basic_device import _BasicDevice
	panel_dev_ids = set(id(d) for d in _panel_devices.values())
	if _editor_globals:
		for name, obj in list(_editor_globals.items()):
			if isinstance(obj, _BasicDevice) and obj.connected and id(obj) not in panel_dev_ids:
				try:
					obj.disconnect()
				except Exception:
					pass

# ── Stdout capture ───────────────────────────────────────────────────────────

class _StdoutPanel(io.TextIOBase):
	"""Write stream that appends text to the #stdout DOM element."""
	def write(self, text):
		if text:
			from js import document
			el = document.getElementById("stdout")
			el.textContent += text
			el.scrollTop = el.scrollHeight
		return len(text) if text else 0

_stdout_panel = _StdoutPanel()

# ── CodeMirror integration ───────────────────────────────────────────────────

def _get_editor_code():
	"""Read code from the CodeMirror editor via globalThis._cmEditor."""
	from js import globalThis
	cm = globalThis._cmEditor
	if cm:
		return str(cm.state.doc.toString())
	return ""

# ── Expose helpers to JS ─────────────────────────────────────────────────────

from js import globalThis
globalThis._pyDisconnectAll = _disconnect_all_devices

# Async wrapper for JS to call when no code is running (needs stack-switching context)
async def _disconnect_all_devices_async():
	_disconnect_all_devices()

from pyodide.ffi import create_proxy
globalThis._pyDisconnectAllAsync = create_proxy(_disconnect_all_devices_async)

def _cancel_all_pending_futures():
	"""Cancel all pending BLE response futures on all known devices.

	Called as a fallback after a timeout when Stop is pressed, in case the
	BLE device never responds (powered off, out of range, etc.). This
	unblocks any `await future` so the interrupt can propagate.
	"""
	from legoeducation.basic_device import _BasicDevice
	devices = set()
	if _editor_globals:
		devices.update(obj for obj in _editor_globals.values() if isinstance(obj, _BasicDevice))
	devices.update(dev for dev in _panel_devices.values() if isinstance(dev, _BasicDevice))
	for dev in devices:
		pending = getattr(dev, '_pending_responses', {})
		for key in list(pending.keys()):
			dq = pending.pop(key, None)
			if dq:
				for fut in dq:
					if not fut.done():
						fut.cancel()

globalThis._pyCancelPendingFutures = create_proxy(_cancel_all_pending_futures)

# ── Monkey-patch to suppress BLE callbacks during stop ────────────────────────
#
# We patch _device_callback (the entry point for ALL BLE notifications) so that
# during stop it only fulfills pending response futures (unblocking awaiting
# code) but skips user notification dispatch. This prevents most Python
# bytecodes from running in the callback context during stop, reducing the
# chance that the interrupt buffer flag is consumed before user code can see it.

from legoeducation.basic_device import _BasicDevice

_original_device_callback = _BasicDevice._device_callback

async def _patched_device_callback(self, characteristic, data):
	"""During stop: still fulfill pending response futures to unblock awaiting
	code, but skip user notification dispatch. This allows the interrupt buffer
	to trigger KeyboardInterrupt once Python resumes from the awaited future."""
	from js import globalThis
	if getattr(globalThis, '_pyStopRequested', False):
		try:
			if len(data) < 1:
				return
			message_id = data[0]
			payload = data[1:] if len(data) > 1 else b''
			# Determine lookup key (same logic as _device_callback)
			lookup_key = message_id
			if message_id in self._multi_by_motor and len(payload) >= 1:
				lookup_key = (message_id, payload[0])
			# Fulfill the pending future so `await f` can complete
			if lookup_key in self._pending_responses:
				dq = self._pending_responses.get(lookup_key)
				if dq:
					try:
						future = dq.popleft()
					except IndexError:
						future = None
					if not dq:
						self._pending_responses.pop(lookup_key, None)
					if future is not None and not future.done():
						future.set_result(payload)
		except Exception:
			pass
		return
	await _original_device_callback(self, characteristic, data)

_BasicDevice._device_callback = _patched_device_callback

# ── Trace-based stop hook for tight CPU loops ────────────────────────────────
#
# For loops without async yield points (e.g. `while True: pass`), the main
# thread is blocked and the interrupt buffer can't be set via click handler.
# sys.settrace runs a Python callback on every line/call, letting us check
# the stop flag and raise KeyboardInterrupt even in pure-CPU loops.
# Note: This has a performance cost. For our application it should be negligible,  
# but if we notice slowdowns you can disable it by commenting out.

def _stop_trace(frame, event, arg):
	"""Trace function that raises KeyboardInterrupt when stop is requested.
	Only fires for user code (<editor> frames) to avoid poisoning event loop
	callbacks like BLE notification handlers that continue running after stop."""
	from js import globalThis
	if event == "call" and frame.f_code.co_filename != "<editor>":
		# Don't trace into non-editor frames (library/asyncio code)
		return None
	if getattr(globalThis, '_pyStopRequested', False):
		# Walk up the call stack — only interrupt if we're inside user code
		f = frame
		while f is not None:
			if f.f_code.co_filename == "<editor>":
				raise KeyboardInterrupt()
			f = f.f_back
	return _stop_trace
#
# Web Bluetooth's requestDevice() requires a user gesture. If the gesture has
# expired (e.g. code ran for several seconds before calling .connect()), we
# show an inline button in the output panel to get a fresh gesture.

from legoeducation.web_bluetooth import WebBluetoothTransport

_original_scan_devices = WebBluetoothTransport.scan_devices

async def _patched_scan_devices(self, timeout, filters=None):
	"""Wrap scan_devices to retry with a gesture prompt on SecurityError."""
	try:
		return await _original_scan_devices(self, timeout, filters)
	except Exception as exc:
		err_msg = str(exc)
		err_name = getattr(exc, "name", "") or type(exc).__name__
		if "user gesture" in err_msg.lower() or err_name == "SecurityError":
			return await _request_with_gesture(self, timeout, filters)
		raise

async def _request_with_gesture(transport, timeout, filters):
	"""Show an inline button in the output panel to get a fresh user gesture."""
	from js import document, navigator, Object, globalThis
	from pyodide.ffi import to_js, create_proxy as _cp

	stdout_el = document.getElementById("stdout")

	prompt = document.createElement("div")
	prompt.style.cssText = (
		"margin:0.4rem 0;padding:0.5rem 0.75rem;background:#2a2a4a;"
		"border:1px solid #f5c518;border-radius:6px;display:inline-block;"
	)
	btn = document.createElement("button")
	btn.textContent = "🔗 Click to select BLE device"
	btn.style.cssText = (
		"padding:0.4rem 0.8rem;font-size:0.85rem;font-weight:600;border:none;"
		"border-radius:4px;background:#f5c518;color:#1a1a2e;cursor:pointer;"
	)
	prompt.appendChild(btn)
	stdout_el.appendChild(prompt)
	stdout_el.scrollTop = stdout_el.scrollHeight

	loop = asyncio.get_event_loop()
	future = loop.create_future()

	def on_click(event):
		async def do_request():
			try:
				# Re-run the original scan_devices inside the fresh gesture
				result = await _original_scan_devices(transport, timeout, filters)
				future.set_result(result)
			except Exception:
				if not future.done():
					future.set_result(None)
			finally:
				prompt.remove()

		asyncio.ensure_future(do_request())

	click_proxy = _cp(on_click)
	btn.addEventListener("click", click_proxy)

	# Poll for stop request so Stop button works while waiting
	while not future.done():
		if getattr(globalThis, '_pyStopRequested', False):
			prompt.remove()
			click_proxy.destroy()
			raise KeyboardInterrupt()
		await asyncio.sleep(0.05)

	result = future.result()
	click_proxy.destroy()
	return result

WebBluetoothTransport.scan_devices = _patched_scan_devices

# ── Monkey-patch connect() to skip if already connected ───────────────────────
# Panel devices are already connected; user code calling .connect() should be a no-op.

_original_connect = _BasicDevice.connect

def _patched_connect(self, *args, **kwargs):
	"""Skip connect if device is already connected (e.g. via device panel)."""
	if self.connected:
		return
	return _original_connect(self, *args, **kwargs)

_BasicDevice.connect = _patched_connect

# ── Interruptible time.sleep ──────────────────────────────────────────────────

import time as _time_module

class _InterruptibleTimeModule:
	"""A wrapper around the time module that replaces sleep() with a version
	that checks the stop flag every 10ms, enabling fast Stop response.

	Key insight: We cannot rely solely on Pyodide's interrupt buffer because
	BLE notification callbacks (running as asyncio tasks during sleep suspension)
	consume the flag before the main code can see it. Instead, we check the
	JS-level _pyStopRequested flag directly and raise KeyboardInterrupt ourselves.
	"""

	def __getattr__(self, name):
		return getattr(_time_module, name)

	def sleep(self, seconds):
		"""Sleep in short intervals, checking stop flag for fast response."""
		from js import globalThis
		end = _time_module.time() + seconds
		while True:
			if getattr(globalThis, '_pyStopRequested', False):
				raise KeyboardInterrupt()
			remaining = end - _time_module.time()
			if remaining <= 0:
				break
			_time_module.sleep(min(remaining, 0.01))

_interruptible_time = _InterruptibleTimeModule()

# Replace time in sys.modules so that `import time` in user code
# returns our interruptible wrapper (not the real module).
sys.modules['time'] = _interruptible_time

# ── Device Panel (Python side) ────────────────────────────────────────────────
# Manages devices created via the UI device panel. Each device is stored by its
# panel ID and injected into user code namespace on Run.

async def _connect_panel_device(device_type, panel_id):
	"""Create and connect a device of the given type. Called from JS panel."""
	device_type = str(device_type)
	panel_id = str(panel_id)
	cls_map = {
		"SingleMotor": le.SingleMotor,
		"DoubleMotor": le.DoubleMotor,
		"ColorSensor": le.ColorSensor,
		"Controller": le.Controller,
	}
	cls = cls_map.get(device_type)
	if cls is None:
		log(f"Unknown device type: {device_type}", "log-err")
		return False
	dev = cls()
	try:
		dev.connect()
		if dev.connected:
			_panel_devices[panel_id] = dev
			log(f"Panel device connected: {device_type} ({panel_id})", "log-ok")
			return True
		else:
			log(f"Failed to connect {device_type}", "log-warn")
			return False
	except Exception as e:
		log(f"Error connecting {device_type}: {e}", "log-err")
		return False

async def _disconnect_panel_device(panel_id):
	"""Disconnect a panel device by its ID."""
	panel_id = str(panel_id)
	dev = _panel_devices.pop(panel_id, None)
	if dev and dev.connected:
		try:
			dev.disconnect()
			log(f"Panel device disconnected: {panel_id}", "log-ok")
		except Exception as e:
			log(f"Error disconnecting panel device: {e}", "log-warn")

def _get_panel_injections():
	"""Return a dict of {varName: device} for all connected panel devices."""
	import json
	from js import globalThis
	state_json = str(globalThis._getDevicePanelState())
	if not state_json:
		return {}
	entries = json.loads(state_json)
	injections = {}
	for entry in entries:
		panel_id = entry["id"]
		var_name = entry["varName"]
		dev = _panel_devices.get(panel_id)
		if dev and dev.connected:
			injections[var_name] = dev
	return injections

def _get_panel_completions():
	"""Return a dict of {varName: instance} for ALL panel devices (connected or not).
	For unconnected devices, creates a stub instance for completion purposes."""
	import json
	from js import globalThis
	state_json = str(globalThis._getDevicePanelAllState())
	if not state_json:
		return {}
	entries = json.loads(state_json)
	cls_map = {
		"SingleMotor": le.SingleMotor,
		"DoubleMotor": le.DoubleMotor,
		"ColorSensor": le.ColorSensor,
		"Controller": le.Controller,
	}
	result = {}
	for entry in entries:
		panel_id = entry["id"]
		var_name = entry["varName"]
		device_type = entry["type"]
		# Use actual connected device if available, otherwise create stub for completions
		dev = _panel_devices.get(panel_id)
		if dev:
			result[var_name] = dev
		else:
			cls = cls_map.get(device_type)
			if cls:
				try:
					result[var_name] = object.__new__(cls)
				except TypeError:
					result[var_name] = cls()
	return result

globalThis._pyConnectPanelDevice = create_proxy(_connect_panel_device)
globalThis._pyDisconnectPanelDevice = create_proxy(_disconnect_panel_device)

def _check_panel_device_connected(panel_id):
	"""Check if a panel device is still connected (called from JS polling)."""
	panel_id = str(panel_id)
	dev = _panel_devices.get(panel_id)
	if dev is None:
		return False
	return bool(dev.connected)

globalThis._pyCheckPanelDeviceConnected = create_proxy(_check_panel_device_connected)

# ── Button handlers ──────────────────────────────────────────────────────────

@when("click", "#btn-clear")
def on_clear(event):
	_el("log").innerHTML = ""

@when("click", "#btn-clear-stdout")
def on_clear_stdout(event):
	_el("stdout").textContent = ""

# ── Python Editor ────────────────────────────────────────────────────────────

def _strip_panel_device_lines(code, panel_vars):
	"""Remove lines that create or connect devices already provided by the panel.

	Strips lines like:
		singlemotor = le.SingleMotor()
		singlemotor.connect()
	when 'singlemotor' is a connected panel variable.
	Prints an info message to stdout about skipped lines.
	"""
	if not panel_vars:
		return code
	import re
	var_names = set(panel_vars.keys())
	filtered = []
	skipped = {}  # var_name -> list of line numbers
	for i, line in enumerate(code.splitlines(), 1):
		stripped = line.strip()
		skip = False
		for var in var_names:
			if re.match(rf'^{re.escape(var)}\s*=\s*le\.\w+\(', stripped):
				skip = True
				skipped.setdefault(var, []).append(i)
				break
			if re.match(rf'^{re.escape(var)}\.connect\s*\(', stripped):
				skip = True
				skipped.setdefault(var, []).append(i)
				break
		filtered.append(line if not skip else "")
	# Print info about skipped lines
	if skipped:
		for var, lines in skipped.items():
			line_str = ", ".join(str(n) for n in lines)
			print(f"ℹ️ '{var}' is already connected via Device Panel — line {line_str} skipped.")
	return "\n".join(filtered)

@when("click", "#btn-run")
async def on_run(event):
	from js import globalThis as _gs
	_gs._pyStopRequested = False  # reset stop flag for new run
	code = _get_editor_code().strip()
	if not code:
		return
	# Bail out if the button is disabled (e.g. device mid-connection)
	if _el("btn-run").disabled:
		return
	status = _el("editor-status")
	disable("btn-run")
	_gs.isCodeRunning = True
	status.textContent = "⏳ Running…"
	status.style.color = "#8888ff"
	log("--- Running editor code ---")
	try:
		global _editor_globals
		if _editor_globals is None:
			_editor_globals = {"le": le, "__builtins__": __builtins__}
		_editor_globals.update({"le": le, "time": _interruptible_time})
		# Inject connected panel devices into namespace
		panel_vars = _get_panel_injections()
		_editor_globals.update(panel_vars)
		# Redirect stdout to output panel
		old_stdout = sys.stdout
		sys.stdout = _stdout_panel
		# Strip device creation/connect for panel-connected variables
		code = _strip_panel_device_lines(code, panel_vars)
		# Share editor globals with completion engine
		_completion_globals.update(_editor_globals)
		try:
			# Compile first so we don't send START if there's a SyntaxError
			compiled = compile(code, "<editor>", "exec")
			# Notify panel devices that a program is starting
			for dev in _panel_devices.values():
				try:
					dev.program_flow_notification(le.PROGRAM_ACTION_START, blocking=False)
				except Exception:
					pass
			sys.settrace(_stop_trace)
			exec(compiled, _editor_globals)
		finally:
			sys.settrace(None)
			sys.stdout = old_stdout
		status.textContent = "✅ Done"
		status.style.color = "#44cc44"
		log("--- Editor code finished ✓ ---", "log-ok")
	except (KeyboardInterrupt, asyncio.CancelledError):
		_reset_interrupt_buffer()
		# Notify panel devices that the program has stopped (stops motors etc.)
		for dev in _panel_devices.values():
			try:
				dev.program_flow_notification(le.PROGRAM_ACTION_STOP, blocking=False)
			except Exception:
				pass
		_disconnect_all_devices()
		status.textContent = "🛑 Stopped"
		status.style.color = "#ccaa44"
		log("--- Execution interrupted by user ---", "log-warn")
	except SystemExit as e:
		_reset_interrupt_buffer()
		code = e.code if e.code is not None else 0
		if code == 0:
			status.textContent = "✅ Done"
			status.style.color = "#44cc44"
			log(f"--- Editor code exited (code {code}) ✓ ---", "log-ok")
		else:
			status.textContent = "⚠ Exit"
			status.style.color = "#ccaa44"
			log(f"--- Editor code exited (code {code}) ---", "log-warn")
	except Exception as e:
		_reset_interrupt_buffer()
		import traceback
		status.textContent = "❌ Error"
		status.style.color = "#cc4444"
		# Extract traceback lines, filter to user code ("<editor>")
		tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
		# Build a concise message with line number
		user_frames = []
		for line in traceback.format_tb(e.__traceback__):
			if "<editor>" in line:
				user_frames.append(line.strip())
		if user_frames:
			frame_info = user_frames[-1]  # most recent user frame
			log(frame_info, "log-err")
		log(f"{type(e).__name__}: {e}", "log-err")
	finally:
		_reset_interrupt_buffer()
		_disconnect_code_devices()
		_gs.isCodeRunning = False
		enable("btn-run")

# ── Editor Completion ─────────────────────────────────────────────────────────

from pyodide.console import PyodideConsole

_completion_globals = {"le": le, "__builtins__": __builtins__}
_panel_var_names = set()  # track names injected from the device panel

_console = PyodideConsole(
	globals=_completion_globals,
	persistent_stream_redirection=False,
)

def _editor_complete(text, editor_code):
	"""TAB completion with static type inference from editor code.
	Parses editor_code for imports and assignments, infers types,
	then completes 'text' using those inferred globals."""
	import json as _json
	global _panel_var_names
	# Remove old panel vars, then inject current ones
	for old_name in _panel_var_names:
		_completion_globals.pop(old_name, None)
	panel_vars = _get_panel_completions()
	_panel_var_names = set(panel_vars.keys())
	_completion_globals.update(panel_vars)
	lines = editor_code.splitlines()
	_infer_editor_types_safe(lines)
	try:
		completions, start = _console.complete(text)
		return _json.dumps({"completions": completions, "start": start})
	except Exception:
		return None

_inferred_cache = {}  # {hash: code_hash}
_inferred_vars = set()  # track which vars were inferred (not from actual execution)

def _infer_editor_types_safe(lines):
	"""Try parsing editor lines, stripping trailing incomplete lines if needed."""
	import hashlib
	code = "\n".join(lines)
	code_hash = hashlib.md5(code.encode()).hexdigest()
	if _inferred_cache.get("hash") == code_hash:
		return
	_inferred_cache["hash"] = code_hash

	# Remove previously inferred vars so stale names don't linger
	for old_var in _inferred_vars:
		_completion_globals.pop(old_var, None)
	_inferred_vars.clear()

	# Execute import lines safely (works even if code has syntax errors)
	for line in lines:
		stripped = line.strip()
		if stripped.startswith("import ") or stripped.startswith("from "):
			try:
				exec(stripped, _completion_globals)
			except Exception:
				pass

	# Try parsing, removing trailing lines until it works
	import ast
	tree = None
	parse_lines = list(lines)
	while parse_lines:
		try:
			tree = ast.parse("\n".join(parse_lines))
			break
		except SyntaxError:
			parse_lines.pop()

	if tree is None:
		return

	# Infer types from assignments
	for node in ast.walk(tree):
		if not isinstance(node, ast.Assign):
			continue
		if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
			continue
		var_name = node.targets[0].id
		if var_name in _completion_globals and var_name not in _inferred_vars:
			continue
		if not isinstance(node.value, ast.Call):
			continue
		call_expr = ast.unparse(node.value.func)
		try:
			cls = eval(call_expr, _completion_globals)
			if isinstance(cls, type):
				try:
					obj = object.__new__(cls)
				except TypeError:
					obj = cls
				_completion_globals[var_name] = obj
				_inferred_vars.add(var_name)
		except Exception:
			pass

# Expose to JS
from pyodide.ffi import create_proxy
globalThis._pyEditorComplete = create_proxy(_editor_complete)

def _get_signature(func_expr, editor_code):
	"""Get function signature and docstring for signature help tooltip."""
	import json as _json
	import inspect
	global _panel_var_names
	# Remove old panel vars, then inject current ones
	for old_name in _panel_var_names:
		_completion_globals.pop(old_name, None)
	panel_vars = _get_panel_completions()
	_panel_var_names = set(panel_vars.keys())
	_completion_globals.update(panel_vars)
	lines = editor_code.splitlines()
	_infer_editor_types_safe(lines)
	try:
		obj = eval(func_expr, _completion_globals)
		sig = inspect.signature(obj)
		params = []
		for name, param in sig.parameters.items():
			if name == "self":
				continue
			p = {"name": name}
			if param.default is not inspect.Parameter.empty:
				p["default"] = repr(param.default)
			if param.kind == inspect.Parameter.KEYWORD_ONLY:
				p["kwonly"] = True
			elif param.kind == inspect.Parameter.VAR_POSITIONAL:
				p["name"] = "*" + name
			elif param.kind == inspect.Parameter.VAR_KEYWORD:
				p["name"] = "**" + name
			params.append(p)
		doc = inspect.getdoc(obj) or ""
		# Take first paragraph of docstring
		first_para = doc.split("\n\n")[0].strip() if doc else ""
		return _json.dumps({"params": params, "doc": first_para})
	except Exception:
		return None

globalThis._pyGetSignature = create_proxy(_get_signature)

# Initialize completion with default import
_console.push("import legoeducation as le")

# ── Startup ──────────────────────────────────────────────────────────────────

log(f"legoeducation v{le.__version__} loaded ✓", "log-ok")
log(f"Motor constants: LEFT={le.MOTOR_BITS_LEFT}, RIGHT={le.MOTOR_BITS_RIGHT}", "log-info")

_setup_interrupt_buffer()

if sys.platform == "emscripten":
	if hasattr(__import__('js'), 'crossOriginIsolated') and __import__('js').crossOriginIsolated:
		log("Cross-origin isolated ✓ (stack-switching enabled)", "log-ok")
	else:
		log("⚠ NOT cross-origin isolated — search/connect may fail!", "log-err")
		log("  Serve with COOP+COEP headers or use mini-coi-fd.js", "log-warn")

set_status("✅ Ready — write Python and click Run", "ready")