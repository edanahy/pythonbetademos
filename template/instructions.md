# Demo Creation Instructions
### For LLMs building new PyScript / LEGO Education browser demos

---

## What You're Building

These demos run as **PyScript** files loaded by a LEGO Education webpage.
The Python code executes in the browser via Pyodide (a WebAssembly Python runtime).
Your job is to inject a custom interactive UI into a specific region of that page,
wire it to LEGO hardware, and keep everything running in a self-contained `.py` file.

**The user gives you:**
- `template.py` — complete infrastructure + skeleton demo class
- `INSTRUCTIONS.md` — this file
- A description of what the new demo should do

**You produce:**
- A single `.py` file the user downloads and loads in the browser

---

## Workflow: What to Edit vs. What to Leave Alone

```
template.py
├── SECTION 1 — EVENT HANDLERS     ← EDIT: write your callbacks here
├── SECTION 2 — CONFIGURATION      ← EDIT: adjust constants
├── INFRASTRUCTURE                 ← DO NOT MODIFY (Logger, StateMachine, etc.)
└── MyDemo                         ← EDIT: rename + fill in the 4 TODO blocks
	├── _build_layout()            ← TODO: left-column display content
	├── _setup_controls()          ← TODO: which buttons to add
	├── update()                   ← TODO: async per-tick logic
	└── _on_state_enter()          ← keep if using StateMachine; remove otherwise
```

**Rename** `MyDemo` to something descriptive (e.g. `SensorGraphDemo`, `RLTableDemo`).
**Do not** rename or restructure the infrastructure classes — they are copy-portable and tested.

---

## Critical Technical Rules

These rules exist because PyScript/Pyodide has unusual constraints.
Violating any of them causes silent failures or cryptic errors.

### Rule 1 — No `window.__name` inside class methods ⚠ MOST COMMON BUG

Python **silently mangles** identifiers starting with `__` inside class bodies.
`window.__startCamera` inside a method becomes `window._ClassName__startCamera` at
compile time, so the JS attribute is never found.

```python
# ✗ WRONG — causes AttributeError at runtime
class MyDemo:
	def start(self):
		window.__startCamera()   # mangled to window._MyDemo__startCamera

# ✓ CORRECT — single underscore is never mangled
class MyDemo:
	def start(self):
		window._startCamera()
```

**Rule:** Any JS global you define and call from inside a class must use
`_singleUnderscore` naming, both in the injected JS string and in the Python call site.

### Rule 2 — `update()` must be `async def`

The poll loop calls `update()` via `await window._demoUpdate()`.
This is what enables Pyodide's **stack switching**, which in turn allows
hardware calls (motors, sensors) that use `run_sync()` internally.

If you make `update()` a plain `def`, every hardware call after the first one
inside a session will silently fail or raise "Cannot stack switch."

```python
# ✓ CORRECT — async even if all reads are synchronous properties
async def update(self, *_args):
	val = hw.motor.speed   # property read is sync, but update() must still be async

# ✗ WRONG — breaks hardware calls
def update(self, *_args):
	...
```

### Rule 3 — Commit state before calling handlers

`StateMachine.update()` sets `self._current = candidate` **before** calling
`self._on_enter(candidate)`. Do not change this order.
If the handler raises, the state is already committed so the next tick
won't re-enter the same transition and re-fire the handler.

### Rule 4 — Use the `setTimeout` poll pattern, not `setInterval`

`setInterval` calls Python callbacks synchronously (non-promising), disabling
stack switching. The template's `_start_loop()` uses a self-rescheduling
`async function` with `setTimeout` — keep it exactly as written.

### Rule 5 — Keep `_demo` at module level

```python
_demo = None

def main():
	global _demo
	_demo = MyDemo()   # module-level reference prevents GC

main()
```

If `_demo` is only a local variable, Python may garbage-collect the demo object,
destroying the `create_proxy` callbacks and silently killing the poll loop.

### Rule 6 — `_inject_script` must be defined before `Logger`

`Logger.__init__` calls `_inject_script` immediately when `log = Logger()` runs.
Always keep `_inject_script` above `Logger` in the file.

### Rule 7 — No third-party Python package imports

PyScript's Pyodide environment has the standard library and a limited set of
pre-installed packages. You **cannot** `import pandas`, `import numpy`, etc.
unless the page explicitly loads them via `<py-config>`.
Safe imports: `math`, `datetime`, `json`, `collections`, `itertools`, `random`, `sys`.

`legoeducation` (as `le`) is pre-loaded by the page and accessible from `sys.modules`
(see Rule 9). Do not `import legoeducation` at the top of your file — get it from the
already-loaded module (see Panel Devices section below).

### Rule 8 — Never subscript a JsProxy DOM collection with `[]` ⚠ COMMON BUG

Pyodide wraps DOM objects in `JsProxy`. Python's `[]` operator does **not** work on
`HTMLOptionsCollection`, `NodeList`, or similar DOM list types — it raises
`TypeError: 'pyodide.ffi.JsProxy' object is not subscriptable`.

```python
# ✗ WRONG — raises TypeError on HTMLOptionsCollection / NodeList
opt = self._my_select.options[0]
text = self._my_select.options[self._my_select.selectedIndex].text

# ✓ CORRECT — read the .value property directly
selected_value = str(self._my_select.value or "")
```

For `<select>` elements, always read `.value` directly.
If you need to map a selected value to a label, maintain a parallel Python dict
built when you populate the dropdown — never try to read back from `options[n]`.

### Rule 9 — Access shared Python state via `sys.modules`, not `window`

The demo file and `main_pyodide.py` run in the same Pyodide interpreter and share
`sys.modules`. Python objects defined in `main_pyodide.py` (e.g. `_panel_devices`,
`le`) are **not** on the JS `window` object — `getattr(window, "name", None)` will
return `None` for them. Reach them through `sys.modules` instead:

```python
import sys

def _get_main_attr(name, default=None):
	for mod_name in ("main_pyodide", "__main__"):
		mod = sys.modules.get(mod_name)
		if mod is not None:
			val = getattr(mod, name, None)
			if val is not None:
				return val
	return default
```

The two module names to try, in order:
1. `"main_pyodide"` — modern PyScript (2024+) registers each `<script type="py" src="…">` file as a named module
2. `"__main__"` — older PyScript puts all scripts in the shared `__main__` namespace

### Rule 10 — Any element appended to `document.body` must have an `.id` ⚠ MODAL / OVERLAY BUG

The page registers a `MutationObserver` that immediately removes any child
element added to `document.body` if it has no `id` attribute (and its class
doesn't contain "split", "toolbar", or "status"). It was added to clean up
stray PyScript error nodes, but it silently deletes any overlay, modal, or
floating panel you try to inject — with no console error.

```python
# ✗ WRONG — observer removes this before the user sees it
overlay = document.createElement("div")
overlay.style.cssText = "position:fixed;top:0;..."
document.body.appendChild(overlay)   # silently deleted

# ✓ CORRECT — set id before appendChild
overlay = document.createElement("div")
overlay.id = "myDemoModal"           # any non-empty string works
overlay.style.cssText = "position:fixed;top:0;..."
document.body.appendChild(overlay)
```

Rule: always set a non-empty .id on any element before appending it to body.

---

## Infrastructure Reference

### `log(msg, cls="log-info")`
Write a timestamped message to the on-page `#log` panel.
`cls` options: `"log-info"` (default) | `"log-warn"` | `"log-error"`

### `_inject_script(js_text)`
Appends a `<script>` element to `document.body`.
Use this to define JS helper functions that your Python code calls via `window.*`.

### `StateMachine(initial_state, debounce_ticks, on_enter)`
Edge-triggered FSM. Call `sm.update(candidate_label)` each tick.
`on_enter(new_state)` fires once per accepted transition.
`sm.current` gives the current committed state.

### `CameraComponent(frame_w, frame_h)`
Manages the webcam + MediaPipe PoseLandmarker.
Call `camera.start()` / `camera.stop()`.
After each detected frame, JS publishes:
- `window.poseData` — `{leftWristY, leftShoulderY, rightWristY, rightShoulderY}` (normalised 0–1)
- `window.poseReady` — `True` once first landmarks are available
Read in `update()`: `pose = window.poseData.to_py()`

### `PoseClassifier.compute(lw_y, ls_y, rw_y, rs_y)`
Returns `dict[str → float]` with keys `"left_up"`, `"right_up"`, `"both_up"`, `"both_down"`.
Values sum to ~1. Feed to `StateMachine.update(max(conf, key=conf.get))`.

### `ControlsRow`
```python
row = ControlsRow()
row.add("key", "Start X", "Stop X", on_on=fn_start, on_off=fn_stop)
row.add("key2", ..., guard_on=lambda: bool(some_condition))  # blocks OFF→ON only
row.reset("key")   # programmatically reset to inactive state
```

### `BarChartPanel`
```python
bars = BarChartPanel(width_px=260, default_color="#4A90D9")
bars.add("motor",  "Motor Position")   # chainable
bars.add("sensor", "Color Sensor",  color="#E05C5C")
bars.update({"motor": 0.72, "sensor": 0.35})   # values in [0, 1]
```

---

## LEGO Hardware API

### Two Device Access Models

There are two ways a LEGO device ends up accessible to your demo, and they have
**completely different APIs**. Identify which model applies before writing any hardware code.

#### Model A — Panel Devices (Device Panel UI)

The user connects devices via the Device Panel on the left of the page before running
the demo. These devices are managed by `main_pyodide.py`, stored as Python objects in
`_panel_devices` (a dict keyed by panel_id), and are **not** injected onto `window`.

**All reads are synchronous property accesses — never use `await` on them.**

```python
# Panel device objects live in main_pyodide._panel_devices[panel_id]
# Reach them via sys.modules (see Rule 9):
import sys
def _find_hw(panel_id):
	for mod_name in ("main_pyodide", "__main__"):
		mod = sys.modules.get(mod_name)
		if mod is not None:
			d = getattr(mod, "_panel_devices", None)
			if isinstance(d, dict) and panel_id in d:
				return d[panel_id]
	return None

hw = _find_hw(self._device_id)
if hw is None or not getattr(hw, "connected", True):
	# device is gone — stop streaming
	...
```

Property read API by device type (all synchronous — no `await`):

```python
# SingleMotor — hw = le.SingleMotor() instance
hw.motor.position         # relative degrees
hw.motor.absolutePosition # absolute position (degrees)
hw.motor.speed            # speed (%)
hw.motor.power            # power (%)

# DoubleMotor — hw = le.DoubleMotor() instance
# le must be fetched from sys.modules (see Rule 9)
hw.motor[le.MOTOR_LEFT].absolutePosition # left motor absolute position (degrees)
hw.motor[le.MOTOR_LEFT].position     # left motor position
hw.motor[le.MOTOR_LEFT].speed        # left motor speed
hw.motor[le.MOTOR_LEFT].power       # left motor power (%)
hw.motor[le.MOTOR_RIGHT].absolutePosition # right motor absolute position (degrees)
hw.motor[le.MOTOR_RIGHT].position    # right motor position
hw.motor[le.MOTOR_RIGHT].speed		 # right motor speed (%)
hw.motor[le.MOTOR_RIGHT].power       # right motor power (%)
hw.imu_device.yaw                    # IMU yaw (°)
hw.imu_device.pitch                  # IMU pitch (°)
hw.imu_device.roll                   # IMU roll (°)
hw.imu_device.accelerometerX         # IMU accel X
hw.imu_device.accelerometerY         # IMU accel Y
hw.imu_device.accelerometerZ         # IMU accel Z
hw.imu_device.gyroscopeX             # IMU gyro X
hw.imu_device.gyroscopeY             # IMU gyro Y
hw.imu_device.gyroscopeZ             # IMU gyro Z

# ColorSensor — hw = le.ColorSensor() instance
hw.sensor.reflection     # reflected light (0–100)
hw.sensor.rawRed         # raw red channel
hw.sensor.rawGreen       # raw green channel
hw.sensor.rawBlue        # raw blue channel
hw.sensor.hue            # hue
hw.sensor.saturation     # saturation
hw.sensor.value          # brightness value

# Controller — hw = le.Controller() instance
hw.sensor.leftPercent    # left lever (%)
hw.sensor.rightPercent   # right lever (%)
hw.sensor.leftAngle      # left angle (°)
hw.sensor.rightAngle     # right angle (°)
```

NOTE on acceleration and gyroscope: degrees is DECI-DEGREES (e.g. 900 = 90°); divide by 10.0 everywhere, including in origin resets.

Getting the `le` constants module for DoubleMotor subscript (`le.MOTOR_LEFT` etc.):

```python
import sys
def _get_le():
	for mod_name in ("main_pyodide", "__main__"):
		mod = sys.modules.get(mod_name)
		if mod is not None:
			le = getattr(mod, "le", None)
			if le is not None:
				return le
	return None

le = _get_le()
motor_obj = hw.motor[le.MOTOR_LEFT]  # subscript with Python constant, not JS
```

Note: `hw.motor[le.MOTOR_LEFT]` uses Python `[]` subscript on a Python device object,
which works fine. The JsProxy subscript prohibition (Rule 8) applies only to DOM types.

Checking connectivity:

```python
# Device objects expose a .connected bool — always check before reading
if hw is None or not getattr(hw, "connected", True):
	log("Device disconnected — stopping", "log-warn")
	window.demoActive = False
	self.controls.reset("run")
	return None
```

#### Model B — Editor Global Variables

When the user writes code in the on-page editor and clicks Run, `main_pyodide.py`
injects panel devices into the editor's `exec()` namespace under the user-given
variable name (e.g., `doublemotor`, `singlemotor`). This model applies only to
**editor code**, not to demo `.py` files loaded via `<script type="py">`.

In this model, device objects are effectively page-level globals in the editor's scope.
Method calls may be async:

```python
# Motor control (may be blocking or non-blocking method calls)
doublemotor.motor_run_for_degrees(
	degrees=360, motor=le.MOTOR_LEFT,
	direction=le.MOVEMENT_TURN_DIRECTION_LEFT)

doublemotor.motor_stop(motor=le.MOTOR_LEFT)

# Sensor reads (async in editor model)
val = await color_sensor.get_reflected_light()   # 0–100
dist = await distance_sensor.get_distance_cm()
force = await force_sensor.get_force()
```

**Demo files should use Model A (Panel Devices).** Model B is for user-written
editor scripts and is shown here for context only.

### Constants (both models)

```python
le.MOTOR_LEFT                        # left motor port
le.MOTOR_RIGHT                       # right motor port
le.MOVEMENT_TURN_DIRECTION_LEFT      # clockwise (from motor's perspective)
le.MOVEMENT_TURN_DIRECTION_RIGHT     # counter-clockwise
```

---

## Device Discovery at Runtime

If your demo needs to enumerate what devices are currently connected (rather than
assuming fixed variable names), inject a `getConnectedDevices()` helper that scans
the Device Panel DOM. The page stores device rows in `#device-rows`; each `.device-row`
element has a `.status-dot` that gets the class `"connected"` when active.

```python
def _inject_device_helpers(self):
	_inject_script("""
function getConnectedDevices() {
  var container = document.getElementById('device-rows');
  if (!container) return [];
  var connected = [];
  container.querySelectorAll('.device-row').forEach(function(row) {
	var dot = row.querySelector('.status-dot');
	if (!dot || !dot.classList.contains('connected')) return;
	var sel = row.querySelector('select');
	var inp = row.querySelector('input[type="text"]');
	connected.push({
	  id:   row.dataset.deviceId || null,   // panel_id — key in _panel_devices
	  type: sel ? sel.value : null,          // "SingleMotor", "DoubleMotor", etc.
	  name: inp ? inp.value.trim() : ''      // user-given variable name
	});
  });
  return connected;
}
window.getConnectedDevices = getConnectedDevices;
""")

def _refresh_devices(self):
	raw = window.getConnectedDevices().to_py()
	for item in raw:
		if hasattr(item, "to_py"):
			item = item.to_py()
		panel_id = str(item.get("id") or "")
		dev_type = str(item.get("type") or "")
		var_name = str(item.get("name") or "")
		# Use panel_id to look up the Python device object from _panel_devices
		hw = _find_hw(panel_id)
```

The page also exposes a JS function for device state:

```javascript
// Returns JSON string: [{id, type, varName}, ...]  (connected devices only)
globalThis._getDevicePanelState()

// Returns JSON string including disconnected/connecting devices
globalThis._getDevicePanelAllState()
```

Call these from Python via `window._getDevicePanelState()` and parse with `json.loads(str(...))`.

---

## Common Patterns

### Pattern: Read panel device property → push to chart
```python
async def update(self, *_args):
	if not window.demoActive:
		return
	hw = self._find_hw_device()   # looks up _panel_devices by self._device_id
	if hw is None or not getattr(hw, "connected", True):
		log("Device disconnected — stopping", "log-warn")
		window.demoActive = False
		self.controls.reset("run")
		return
	val = hw.sensor.reflection   # synchronous property — no await
	self.right_panel.update({"light": val / 100.0})
```

### Pattern: Look up panel device and cache module refs (recommended)
Cache `_panel_devices` and `le` once in `__init__` so `update()` stays fast:

```python
def __init__(self):
	import sys
	self._panel_devices_ref = None
	self._le_module = None
	for mod_name in ("main_pyodide", "__main__"):
		mod = sys.modules.get(mod_name)
		if mod is not None:
			d = getattr(mod, "_panel_devices", None)
			if isinstance(d, dict):
				self._panel_devices_ref = d
				self._le_module = getattr(mod, "le", None)
				break

def _find_hw_device(self, panel_id):
	if self._panel_devices_ref is None:
		return None
	return self._panel_devices_ref.get(panel_id)
```

### Pattern: `<select>` dropdown without subscript errors
```python
# Build label lookup in Python when populating dropdown
self._labels = {}   # value → display label
for key, label in options:
	self._labels[key] = label
	opt = document.createElement("option")
	opt.value = key
	opt.textContent = label
	self._my_select.appendChild(opt)

# Read selection: always use .value, never options[n]
def _on_change(self):
	selected = str(self._my_select.value or "")
	label = self._labels.get(selected, selected)
```

### Pattern: Classify sensor input → drive state machine
```python
def _classify(self, val):
	if val > 70:   return "bright"
	if val < 30:   return "dark"
	return "mid"

async def update(self, *_args):
	if not window.demoActive: return
	val = hw.sensor.reflection   # sync property read
	self.right_panel.update({"light": val / 100.0})
	self.sm.update(self._classify(val))
```

### Pattern: Inject a custom JS drawing function
```python
# In __init__, after building canvas:
window.myCanvas = my_canvas_element
_inject_script("""
window.drawBar = function(x, y, w, h, color) {
  var ctx = window.myCanvas.getContext("2d");
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
};
""")

# In update():
window.drawBar(10, 10, int(val * 200), 30, "#4A90D9")
```

### Pattern: Live time-series graph (Chart.js via CDN)
Load Chart.js dynamically; expose no-op helpers that become real once it loads:

```python
def _inject_chart_helpers(self):
	_inject_script("""
window._chartReady = false;
window._pushLiveChartValue = function(value, label) {
  if (!window._chartReady || !window._liveChart) return;
  /* ... append to chart.data.datasets[0].data, call chart.update('none') ... */
};
window._resetLiveChart = function(label) { /* ... */ };

(function() {
  var s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
  s.onload = function() {
	var canvas = document.getElementById('liveChartCanvas');
	window._liveChart = new Chart(canvas.getContext('2d'), { /* config */ });
	window._chartReady = true;
  };
  document.head.appendChild(s);
})();
""")
```

The no-op pattern means callers don't need to guard on chart readiness — they
just call `window._pushLiveChartValue(val, label)` every tick and it becomes
live automatically once Chart.js finishes loading.

### Pattern: Collect labelled training data
```python
self._samples = []

def _record_sample(self, label: str):
	"""Called from a button's on_on callback."""
	async def _do():
		val = hw.sensor.reflection   # sync read
		self._samples.append((val, label))
		log(f"Recorded {label}: {val}")
	window._recordSample = create_proxy(_do)
	_inject_script("window._recordSample().catch(e => window.jsLog(e,'log-error'));")
```

### Pattern: Camera + pose → state machine (from betahack_posedetection.py)
```python
self.camera = CameraComponent(FRAME_W, FRAME_H)
self.sm = StateMachine("both_down", 2, self._on_state_enter)

async def update(self, *_args):
	if not (window.cameraActive and window.trackingActive and window.poseReady):
		return
	pose = window.poseData.to_py()
	lw, ls = pose["leftWristY"],  pose["leftShoulderY"]
	rw, rs = pose["rightWristY"], pose["rightShoulderY"]
	if None in (lw, ls, rw, rs): return
	conf = PoseClassifier.compute(lw, ls, rw, rs)
	self.bars.update(conf)
	self.sm.update(max(conf, key=conf.get))
```

### Pattern: Inject large JS blocks without f-string brace-escaping

Split into two _inject_script calls: a small f-string that publishes Python
constants as a JS object, then a plain string for the full engine body.

```python
def _inject_constants(self):
	_inject_script(
		"window._MYAPP = {"
		f" CW:{CANVAS_W}, CH:{CANVAS_H},"
		f" BALL_R:{BALL_RADIUS},"
		f" WIN:{WIN_SCORE}"
		" };"
	)

def _inject_engine(self):
	_inject_script("""
(function () {
  var C = window._MYAPP;       // read constants — no escaping needed
  var CW = C.CW, CH = C.CH;
  function loop() { /* { } braces work normally here */ }
  requestAnimationFrame(loop);
})();
""")
```

Call _inject_constants() before _inject_engine(). Inline scripts execute
synchronously on appendChild, so constants are available immediately.

### Pattern: DoubleMotor direction inversion

The two barrels face opposite directions, so the same physical twist may
produce opposite-sign speed readings on MOTOR_LEFT vs MOTOR_RIGHT. Add
invert constants to Section 2:

```python
INVERT_LEFT  = 1    # 1 = normal, −1 = inverted
INVERT_RIGHT = -1   # default: right barrel faces the other way

# In update():
left_vy  = float(hw.motor[le.MOTOR_LEFT].speed  or 0) * SPEED_SCALE * INVERT_LEFT
right_vy = float(hw.motor[le.MOTOR_RIGHT].speed or 0) * SPEED_SCALE * INVERT_RIGHT
```

Users who find a control running backwards flip the constant from 1 to -1
without needing to understand the rest of the code.

---

## Building a Custom Right-Panel Component

If `BarChartPanel` doesn't fit your demo, write a replacement class.
It needs:
- `.element` — a DOM node to append to `right_col`
- `.update(data: dict)` — called each tick with your data dict

```python
class MyReadoutPanel:
	def __init__(self, width_px: int = 260):
		self.element = document.createElement("div")
		self.element.style.width      = f"{width_px}px"
		self.element.style.fontFamily = "monospace"
		self.element.style.fontSize   = "14px"
		self._rows = {}

	def add_row(self, key: str, label: str) -> "MyReadoutPanel":
		row  = document.createElement("div")
		row.style.marginBottom = "6px"
		lbl  = document.createElement("span")
		lbl.textContent = label + ": "
		val  = document.createElement("span")
		val.textContent = "—"
		row.appendChild(lbl); row.appendChild(val)
		self.element.appendChild(row)
		self._rows[key] = val
		return self

	def update(self, data: dict):
		for key, span in self._rows.items():
			if key in data:
				span.textContent = str(data[key])
```

Attach it in `MyDemo.__init__`:
```python
self.right_panel = MyReadoutPanel(width_px=RIGHT_COL_WIDTH)
self.right_panel.add_row("pos",   "Motor Position")
self.right_panel.add_row("light", "Light Sensor")
```

---

## Pre-Delivery Checklist

Before handing the file to the user, verify:

- [ ] `MyDemo` has been renamed to something descriptive
- [ ] SECTION 1 handlers match the demo's actual state/event labels
- [ ] `STATE_HANDLERS` keys match what `StateMachine` / `update()` produce
- [ ] `h2.textContent` in `_build_layout()` is updated (not "TODO: Demo Title")
- [ ] No `window.__doubleUnderscore` used inside any class method
- [ ] `update()` is `async def`
- [ ] Any new JS globals use `_singleUnderscore` naming
- [ ] `window._demoUpdate` / `window._demoPollGen` are unique names if multiple demos
	  could run on the same page simultaneously (append a suffix if needed)
- [ ] Hardware calls are only made when `window.demoActive` (or equivalent guard) is `True`
- [ ] All `<select>` reads use `.value`, never `options[n]` subscript (Rule 8)
- [ ] Panel device lookups go through `sys.modules`, not `getattr(window, name)` (Rule 9)
- [ ] Per-tick `_read_value()` (or equivalent) is wrapped in `try/except` in `update()`
	  so exceptions never escape to the JS `catch` block as "update error"
- [ ] Device connectivity is checked (`hw is None or not hw.connected`) before each read
- [ ] If building a device selector UI, the metric/option label lookup uses a Python dict
	  (not DOM readback) — populate `self._labels = {}` when building the dropdown
- [ ] Infrastructure classes are verbatim from the template (no modifications)
- [ ] Any element appended to `document.body` has `.id` set before appendChild
  (the MutationObserver silently removes id-less body children — no error thrown)

---

## Page Context Reference

| Thing                  | Details                                                              |
|------------------------|----------------------------------------------------------------------|
| Page element           | `#device-panel` — anchor for injecting the two-column layout         |
| Log panel              | `#log` — written to by `log()` and `window.jsLog()`                 |
| CSS classes injected   | `"panel-header"`, `"columns-panel"` — styled by page CSS            |
| PyScript environment   | Pyodide (Python 3.12 in WebAssembly)                                 |
| Available builtins     | Standard library; `pyscript`, `js`, `pyodide.ffi`                   |
| JS bridge primitives   | `window.*` (read/write), `create_proxy(fn)`, `document.*`           |
| Panel device store     | `main_pyodide._panel_devices` — `dict[panel_id → device_instance]`  |
| Device state (JS)      | `globalThis._getDevicePanelState()` → JSON `[{id, type, varName}]`  |
| Device rows DOM        | `#device-rows > .device-row` — each has `.status-dot.connected`     |
| `le` constants module  | In `sys.modules["main_pyodide"]` or `sys.modules["__main__"]` as `le` |
| Hardware access        | Via `sys.modules` (panel devices) — NOT via `window.*` globals       |