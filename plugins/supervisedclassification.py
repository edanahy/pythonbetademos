## supervisedclassification.py — Nearest-Neighbor Supervised Classification
## ========================================================================
##
## Educational demo: collect (input, output) hardware data pairs, then deploy
## a nearest-neighbor model that reads input continuously and drives the output
## motor to the value of the closest training example.
##
## Rules from INSTRUCTIONS.md:
##   Rule 1 — No window.__name inside class methods (name-mangling)
##   Rule 2 — update() must be async def
##   Rule 3 — State committed before handler
##   Rule 4 — setTimeout poll pattern
##   Rule 5 — _demo at module level
##   Rule 6 — _inject_script before Logger
##   Rule 7 — Only safe stdlib imports
##   Rule 8 — No JsProxy DOM subscript []
##   Rule 9 — sys.modules for panel devices and le

from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy
import sys as _sys
import json

# =============================================================
# SECTION 1 — EVENT HANDLERS
# =============================================================
# No StateMachine used — mode managed manually on NearestNeighborDemo.


# =============================================================
# SECTION 2 — CONFIGURATION
# =============================================================

POLL_INTERVAL_MS  = 150    # update() period (ms)
PANEL_BG          = "#1a1a2e"
RIGHT_COL_WIDTH   = 400
CHART_WIDTH       = 380
CHART_HEIGHT      = 330
LEFT_COL_WIDTH    = 285

# ── Input metrics per device type ──────────────────────────────────────────
# Each entry: (metric_key, display_label)
# metric_key encodes the property path; used in _read_metric_value().

INPUT_METRICS = {
	"SingleMotor": [
		("position",         "Position (°)"),
		("absolutePosition", "Abs Position (°)"),
		("speed",            "Speed (%)"),
		("power",            "Power (%)"),
	],
	"DoubleMotor": [
		("left:position",          "Left Position (°)"),
		("left:absolutePosition",  "Left Abs Position (°)"),
		("left:speed",             "Left Speed (%)"),
		("left:power",             "Left Power (%)"),
		("right:position",         "Right Position (°)"),
		("right:absolutePosition", "Right Abs Position (°)"),
		("right:speed",            "Right Speed (%)"),
		("right:power",            "Right Power (%)"),
		("imu:yaw",                "IMU Yaw (°)"),
		("imu:pitch",              "IMU Pitch (°)"),
		("imu:roll",               "IMU Roll (°)"),
		("imu:accelX",             "IMU Accel X"),
		("imu:accelY",             "IMU Accel Y"),
		("imu:accelZ",             "IMU Accel Z"),
		("imu:gyroX",              "IMU Gyro X"),
		("imu:gyroY",              "IMU Gyro Y"),
		("imu:gyroZ",              "IMU Gyro Z"),
	],
	"ColorSensor": [
		("reflection", "Reflection (0–100)"),
		("rawRed",     "Raw Red"),
		("rawGreen",   "Raw Green"),
		("rawBlue",    "Raw Blue"),
		("hue",        "Hue"),
		("saturation", "Saturation"),
		("value",      "Brightness Value"),
	],
	"Controller": [
		("leftPercent",  "Left Lever (%)"),
		("rightPercent", "Right Lever (%)"),
		("leftAngle",    "Left Angle (°)"),
		("rightAngle",   "Right Angle (°)"),
	],
}

# ── Output metrics — motors only ───────────────────────────────────────────
# Each entry: (metric_key, display_label)
# metric_key encodes BOTH what property to read during training AND which
# motor command to issue during deploy.  See _read_output_train_val() and
# _drive_output() for the full dispatch.
#
#   pos_rel  → reads .position         → drives motor_run_to_relative_position(val, speed=100)
#   pos_abs  → reads .absolutePosition → drives motor_run_to_absolute_position(val, speed=100)
#   speed    → reads .speed            → drives run(direction, abs(val)) / motor_set_speed(val)

OUTPUT_METRICS = {
	"SingleMotor": [
		("pos_rel", "Position — relative move (°)"),
		("pos_abs", "Abs Position — seek (°)"),
		("speed",   "Speed (%) — continuous"),
	],
	"DoubleMotor": [
		("left:pos_rel", "Left Position — relative move (°)"),
		("left:pos_abs", "Left Abs Position — seek (°)"),
		("left:speed",   "Left Speed (%) — continuous"),
		("right:pos_rel","Right Position — relative move (°)"),
		("right:pos_abs","Right Abs Position — seek (°)"),
		("right:speed",  "Right Speed (%) — continuous"),
	],
}


# =============================================================
# INFRASTRUCTURE — verbatim from template; do not modify
# =============================================================

def _inject_script(js_text: str):
	s = document.createElement("script")
	s.text = js_text
	document.body.appendChild(s)


class Logger:
	"""Timestamped writer for #log; injects window.jsLog() for JS snippets."""

	def __init__(self):
		window.pyLog = create_proxy(self)
		_inject_script("""
window.jsLog = function(msg, cls) {
  cls = cls || "log-info";
  var ts = new Date().toLocaleTimeString("en-GB");
  var el = document.getElementById("log");
  if (!el) return;
  el.innerHTML += '<span class="' + cls + '">[' + ts + '] ' + msg + '</span>\\n';
  el.scrollTop = el.scrollHeight;
};
""")

	def __call__(self, msg: str, cls: str = "log-info"):
		ts = datetime.now().strftime("%H:%M:%S")
		el = document.getElementById("log")
		if el is None:
			return
		el.innerHTML += f'<span class="{cls}">[{ts}] {msg}</span>\n'
		el.scrollTop = el.scrollHeight


log = Logger()


class StateMachine:
	"""Edge-triggered FSM — kept verbatim; not used in this demo."""

	def __init__(self, initial_state: str, debounce_ticks: int, on_enter):
		self._current  = initial_state
		self._pending  = None
		self._count    = 0
		self._debounce = debounce_ticks
		self._on_enter = on_enter

	@property
	def current(self) -> str:
		return self._current

	def update(self, candidate: str):
		if candidate == self._pending:
			self._count += 1
		else:
			self._pending = candidate
			self._count   = 1
		if self._count < self._debounce:
			return
		if candidate != self._current:
			self._current = candidate
			self._on_enter(candidate)


class ToggleButton:
	"""Two-state button; fires on_on / on_off on each flip."""

	def __init__(self, label_on: str, label_off: str,
				 on_on, on_off, guard_on=None):
		self._label_on = label_on
		self._active   = False
		self.element   = document.createElement("button")
		self.element.setAttribute("type", "button")
		self.element.textContent   = label_on
		self.element.style.padding = "6px 12px"

		def _click(event):
			if not self._active:
				if guard_on is not None and not guard_on():
					return
				self._active = True
				self.element.textContent = label_off
				on_on()
			else:
				self._active = False
				self.element.textContent = label_on
				on_off()
		self.element.addEventListener("click", create_proxy(_click))

	def reset(self):
		self._active = False
		self.element.textContent = self._label_on


class ControlsRow:
	"""Horizontal strip of ToggleButtons."""

	def __init__(self):
		self.element = document.createElement("div")
		self.element.style.display        = "flex"
		self.element.style.gap            = "8px"
		self.element.style.justifyContent = "center"
		self.element.style.margin         = "8px auto"
		self._buttons = {}

	def add(self, key: str, label_on: str, label_off: str,
			on_on, on_off, guard_on=None) -> ToggleButton:
		btn = ToggleButton(label_on, label_off, on_on, on_off, guard_on)
		self._buttons[key] = btn
		self.element.appendChild(btn.element)
		return btn

	def reset(self, key: str):
		if key in self._buttons:
			self._buttons[key].reset()


class BarChartPanel:
	"""Verbatim from template — not used as the main panel in this demo."""

	def __init__(self, width_px: int = 260,
				 default_color: str = "#4A90D9",
				 font_size: str = "12px"):
		self.element = document.createElement("div")
		self.element.style.width      = f"{width_px}px"
		self.element.style.fontFamily = "sans-serif"
		self.element.style.fontSize   = font_size
		self._fills         = {}
		self._default_color = default_color

	def add(self, key: str, label: str, color: str = None) -> "BarChartPanel":
		color = color or self._default_color
		row   = document.createElement("div")
		row.style.marginBottom = "10px"
		lbl   = document.createElement("div")
		lbl.textContent = label
		lbl.style.marginBottom = "2px"
		track = document.createElement("div")
		track.style.cssText = ("width:100%;height:14px;background:#eee;"
							   "border-radius:4px;overflow:hidden;")
		fill  = document.createElement("div")
		fill.style.cssText = (f"height:100%;width:0%;background:{color};"
							  "transition:width 0.08s linear;")
		track.appendChild(fill)
		row.appendChild(lbl)
		row.appendChild(track)
		self.element.appendChild(row)
		self._fills[key] = fill
		return self

	def update(self, values: dict):
		for key, fill in self._fills.items():
			pct = max(0.0, min(1.0, values.get(key, 0.0))) * 100
			fill.style.width = f"{pct:.1f}%"


# =============================================================
# NEAREST-NEIGHBOR CLASSIFIER DEMO
# =============================================================

class NearestNeighborDemo:
	"""
	Collect (input_value, output_value) hardware pairs, then deploy a
	nearest-neighbor model: continuously read input → find closest training
	sample → drive output motor to that sample's stored value.

	Collect mode:  live crosshairs on chart show where the next sample will land.
	Deploy mode:   vertical line = current input; star = nearest neighbor;
				   dashed line = output value being commanded to the motor.
	"""

	def __init__(self):
		# Cache refs from main_pyodide via sys.modules (Rule 9)
		self._panel_devices_ref = None
		self._le_module         = None
		self._cache_main_refs()

		# Training data: list of [input_val, output_val]
		self._samples = []

		# Selected device / metric
		self._input_panel_id  = None
		self._input_metric    = None   # e.g. "position", "left:speed"
		self._output_panel_id = None
		self._output_metric   = None   # e.g. "speed", "left:pos_rel"

		# Device info cache: {panel_id: {"type": str, "name": str}}
		self._device_info = {}

		# Label lookup dicts (metric_key → display_label)
		self._input_met_labels  = {}
		self._output_met_labels = {}

		# Mode: "collect" or "deploy"
		self._mode = "collect"

		# Deploy: track last commanded output to avoid re-issuing position cmds
		self._last_drive_val = None

		# DOM refs set during _build_layout
		self._input_dev_sel     = None
		self._input_met_sel     = None
		self._output_dev_sel    = None
		self._output_met_sel    = None
		self._status_span       = None
		self._sample_count_span = None
		self._mode_label        = None
		self._record_btn        = None
		self._clear_btn         = None

		self.controls = ControlsRow()

		self._build_layout()
		self._setup_controls()
		self._inject_chart()
		self._start_loop()

		log("NearestNeighborDemo ready — click ↻ Refresh Devices to begin")

	# ----------------------------------------------------------
	# sys.modules helpers (Rule 9)
	# ----------------------------------------------------------

	def _cache_main_refs(self):
		for mod_name in ("main_pyodide", "__main__"):
			mod = _sys.modules.get(mod_name)
			if mod is not None:
				d = getattr(mod, "_panel_devices", None)
				if isinstance(d, dict):
					self._panel_devices_ref = d
					self._le_module = getattr(mod, "le", None)
					return

	def _find_hw(self, panel_id):
		if self._panel_devices_ref is None:
			self._cache_main_refs()
		if self._panel_devices_ref is None:
			return None
		return self._panel_devices_ref.get(panel_id)

	# ----------------------------------------------------------
	# Device discovery
	# ----------------------------------------------------------

	def _get_connected_devices(self):
		try:
			raw   = window._getDevicePanelState()
			items = json.loads(str(raw))
			return [i for i in items if isinstance(i, dict)]
		except Exception as e:
			log(f"Device scan error: {e}", "log-warn")
			return []

	def _refresh_devices(self):
		devices = self._get_connected_devices()
		self._device_info = {}
		for d in devices:
			pid   = str(d.get("id") or "")
			dtype = str(d.get("type") or "")
			dname = str(d.get("varName") or d.get("name") or pid)
			if pid:
				self._device_info[pid] = {"type": dtype, "name": dname}
		self._populate_input_devices()
		self._populate_output_devices()
		log(f"Found {len(self._device_info)} connected device(s)")

	def _populate_input_devices(self):
		self._input_dev_sel.innerHTML = '<option value="">— pick device —</option>'
		for pid, info in self._device_info.items():
			if info["type"] in INPUT_METRICS:
				opt = document.createElement("option")
				opt.value = pid
				opt.textContent = f"{info['name']} ({info['type']})"
				self._input_dev_sel.appendChild(opt)
		self._input_met_sel.innerHTML = '<option value="">— pick metric —</option>'
		self._input_metric = None

	def _populate_output_devices(self):
		self._output_dev_sel.innerHTML = '<option value="">— pick device —</option>'
		for pid, info in self._device_info.items():
			if info["type"] in OUTPUT_METRICS:
				opt = document.createElement("option")
				opt.value = pid
				opt.textContent = f"{info['name']} ({info['type']})"
				self._output_dev_sel.appendChild(opt)
		self._output_met_sel.innerHTML = '<option value="">— pick metric —</option>'
		self._output_metric = None

	def _populate_input_metrics(self):
		self._input_met_sel.innerHTML = '<option value="">— pick metric —</option>'
		self._input_met_labels = {}
		self._input_metric = None
		if not self._input_panel_id:
			return
		info = self._device_info.get(self._input_panel_id)
		if not info:
			return
		for key, label in INPUT_METRICS.get(info["type"], []):
			self._input_met_labels[key] = label
			opt = document.createElement("option")
			opt.value = key
			opt.textContent = label
			self._input_met_sel.appendChild(opt)

	def _populate_output_metrics(self):
		self._output_met_sel.innerHTML = '<option value="">— pick metric —</option>'
		self._output_met_labels = {}
		self._output_metric = None
		if not self._output_panel_id:
			return
		info = self._device_info.get(self._output_panel_id)
		if not info:
			return
		for key, label in OUTPUT_METRICS.get(info["type"], []):
			self._output_met_labels[key] = label
			opt = document.createElement("option")
			opt.value = key
			opt.textContent = label
			self._output_met_sel.appendChild(opt)

	# ----------------------------------------------------------
	# Layout
	# ----------------------------------------------------------

	def _build_layout(self):
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		header = document.createElement("div")
		header.className = "panel-header"
		h2 = document.createElement("h2")
		h2.textContent = "Nearest-Neighbor Classifier"
		header.appendChild(h2)

		columns = document.createElement("div")
		columns.className = "columns-panel"
		columns.style.cssText = (
			"display:flex;flex-direction:row;gap:16px;"
			"align-items:flex-start;justify-content:center;"
		)

		left_col = document.createElement("div")
		left_col.style.cssText = (
			f"display:flex;flex-direction:column;"
			f"align-items:stretch;width:{LEFT_COL_WIDTH}px;"
		)
		right_col = document.createElement("div")
		right_col.style.cssText = (
			f"display:flex;flex-direction:column;width:{RIGHT_COL_WIDTH}px;"
		)

		left_col.appendChild(self._build_left_panel())
		left_col.appendChild(self.controls.element)

		# Chart canvas
		chart_wrap = document.createElement("div")
		chart_wrap.style.cssText = (
			f"background:#111122;border-radius:6px;overflow:hidden;"
			f"width:{CHART_WIDTH}px;height:{CHART_HEIGHT}px;position:relative;"
		)
		nn_canvas = document.createElement("canvas")
		nn_canvas.id     = "nn-chart-canvas"
		nn_canvas.width  = CHART_WIDTH
		nn_canvas.height = CHART_HEIGHT
		nn_canvas.style.cssText = f"width:{CHART_WIDTH}px;height:{CHART_HEIGHT}px;"
		chart_wrap.appendChild(nn_canvas)

		# Status line below chart
		self._status_span = document.createElement("div")
		self._status_span.style.cssText = (
			"font-family:monospace;font-size:12px;color:#aaa;"
			"margin-top:8px;min-height:44px;text-align:center;"
			"white-space:pre-line;"
		)
		self._status_span.textContent = "Select devices and click ▶ Start."

		right_col.appendChild(chart_wrap)
		right_col.appendChild(self._status_span)

		columns.appendChild(left_col)
		columns.appendChild(right_col)

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(columns, header.nextSibling)

	def _build_left_panel(self):
		panel = document.createElement("div")
		panel.style.cssText = (
			f"background:{PANEL_BG};border-radius:6px;padding:12px;"
			"font-family:sans-serif;font-size:13px;color:#ccc;"
			"display:flex;flex-direction:column;gap:8px;"
		)

		# Refresh button
		refresh_btn = self._mk_btn("↻ Refresh Devices", "#2d2d4e", "#4a4a7a")
		def _on_refresh(evt):
			self._refresh_devices()
		refresh_btn.addEventListener("click", create_proxy(_on_refresh))

		# ── Input section ──
		in_hdr = document.createElement("div")
		in_hdr.style.cssText = "font-weight:bold;color:#88aaff;margin-top:4px;"
		in_hdr.textContent = "Input Device"

		self._input_dev_sel = self._mk_select()
		self._input_dev_sel.innerHTML = '<option value="">— pick device —</option>'
		def _on_in_dev(evt):
			v = str(self._input_dev_sel.value or "")
			self._input_panel_id = v if v else None
			self._populate_input_metrics()
		self._input_dev_sel.addEventListener("change", create_proxy(_on_in_dev))

		self._input_met_sel = self._mk_select()
		self._input_met_sel.innerHTML = '<option value="">— pick metric —</option>'
		def _on_in_met(evt):
			v = str(self._input_met_sel.value or "")
			self._input_metric = v if v else None
		self._input_met_sel.addEventListener("change", create_proxy(_on_in_met))

		# ── Output section ──
		out_hdr = document.createElement("div")
		out_hdr.style.cssText = "font-weight:bold;color:#ff8844;margin-top:4px;"
		out_hdr.textContent = "Output Device (motors only)"

		self._output_dev_sel = self._mk_select()
		self._output_dev_sel.innerHTML = '<option value="">— pick device —</option>'
		def _on_out_dev(evt):
			v = str(self._output_dev_sel.value or "")
			self._output_panel_id = v if v else None
			self._populate_output_metrics()
		self._output_dev_sel.addEventListener("change", create_proxy(_on_out_dev))

		self._output_met_sel = self._mk_select()
		self._output_met_sel.innerHTML = '<option value="">— pick metric —</option>'
		def _on_out_met(evt):
			v = str(self._output_met_sel.value or "")
			self._output_metric = v if v else None
		self._output_met_sel.addEventListener("change", create_proxy(_on_out_met))

		# ── Sample count ──
		self._sample_count_span = document.createElement("div")
		self._sample_count_span.style.cssText = (
			"color:#aaa;font-size:12px;text-align:center;"
		)
		self._sample_count_span.textContent = "Samples: 0"

		# ── Mode indicator ──
		self._mode_label = document.createElement("div")
		self._mode_label.style.cssText = "font-weight:bold;color:#88ff88;margin-top:4px;"
		self._mode_label.textContent = "Mode: Collect Data"

		# ── Record / Clear buttons ──
		btn_row = document.createElement("div")
		btn_row.style.cssText = "display:flex;gap:6px;"

		self._record_btn = self._mk_btn("⊕ Record Sample", "#1e441e", "#3a7a3a")
		def _on_record(evt):
			self._record_sample()
		self._record_btn.addEventListener("click", create_proxy(_on_record))
		self._record_btn.style.flex = "1"

		self._clear_btn = self._mk_btn("✕ Clear Data", "#441e1e", "#7a3a3a")
		def _on_clear(evt):
			self._clear_data()
		self._clear_btn.addEventListener("click", create_proxy(_on_clear))
		self._clear_btn.style.flex = "1"

		btn_row.appendChild(self._record_btn)
		btn_row.appendChild(self._clear_btn)

		for el in [
			refresh_btn,
			in_hdr,  self._input_dev_sel,  self._input_met_sel,
			out_hdr, self._output_dev_sel, self._output_met_sel,
			self._sample_count_span,
			self._mode_label,
			btn_row,
		]:
			panel.appendChild(el)
		return panel

	def _mk_select(self):
		sel = document.createElement("select")
		sel.style.cssText = (
			"width:100%;padding:4px;"
			"background:#2d2d4e;color:#fff;"
			"border:1px solid #4a4a7a;border-radius:4px;"
		)
		return sel

	def _mk_btn(self, text, bg, border):
		btn = document.createElement("button")
		btn.setAttribute("type", "button")
		btn.textContent = text
		btn.style.cssText = (
			f"padding:6px 10px;background:{bg};color:#fff;"
			f"border:1px solid {border};border-radius:4px;cursor:pointer;"
			"font-size:12px;"
		)
		return btn

	# ----------------------------------------------------------
	# Controls (Start/Stop + Collect↔Deploy toggle)
	# ----------------------------------------------------------

	def _setup_controls(self):
		window.demoActive = False

		def _start():
			if self._mode == "deploy" and len(self._samples) == 0:
				log("No training data — switch to Collect mode first.", "log-warn")
				self.controls.reset("run")
				return
			self._last_drive_val = None   # reset so first deploy tick fires
			window.demoActive = True
			log(f"Started in {self._mode} mode")

		def _stop():
			window.demoActive = False
			self._stop_output()
			window._nnClearDeploy()
			log("Demo stopped")

		def _deploy_on():
			self._mode = "deploy"
			self._last_drive_val = None
			self._mode_label.textContent = "Mode: Deploy Model"
			self._mode_label.style.color = "#ffaa44"
			self._record_btn.style.display = "none"
			self._clear_btn.style.display  = "none"
			log("Switched to Deploy mode")

		def _collect_on():
			self._mode = "collect"
			self._mode_label.textContent = "Mode: Collect Data"
			self._mode_label.style.color = "#88ff88"
			self._record_btn.style.display = ""
			self._clear_btn.style.display  = ""
			self._stop_output()
			window._nnClearDeploy()
			log("Switched to Collect mode")

		self.controls.add("run",  "▶ Start", "■ Stop",
						  on_on=_start, on_off=_stop)
		self.controls.add("mode", "→ Deploy Model", "← Collect Data",
						  on_on=_deploy_on, on_off=_collect_on)

	# ----------------------------------------------------------
	# Input hardware reads  (Rule 8 — no JsProxy [] on DOM)
	# ----------------------------------------------------------

	def _read_metric_value(self, panel_id, metric):
		"""Synchronous property read from a panel device. Returns float or None."""
		hw = self._find_hw(panel_id)
		if hw is None or not getattr(hw, "connected", True):
			return None
		try:
			info  = self._device_info.get(panel_id, {})
			dtype = info.get("type", "")
			le    = self._le_module

			if dtype == "SingleMotor":
				if metric == "position":         return float(hw.motor.position)
				if metric == "absolutePosition": return float(hw.motor.absolutePosition)
				if metric == "speed":            return float(hw.motor.speed)
				if metric == "power":            return float(hw.motor.power)

			elif dtype == "DoubleMotor" and le is not None:
				if metric == "left:position":          return float(hw.motor[le.MOTOR_LEFT].position)
				if metric == "left:absolutePosition":  return float(hw.motor[le.MOTOR_LEFT].absolutePosition)
				if metric == "left:speed":             return float(hw.motor[le.MOTOR_LEFT].speed)
				if metric == "left:power":             return float(hw.motor[le.MOTOR_LEFT].power)
				if metric == "right:position":         return float(hw.motor[le.MOTOR_RIGHT].position)
				if metric == "right:absolutePosition": return float(hw.motor[le.MOTOR_RIGHT].absolutePosition)
				if metric == "right:speed":            return float(hw.motor[le.MOTOR_RIGHT].speed)
				if metric == "right:power":            return float(hw.motor[le.MOTOR_RIGHT].power)
				if metric == "imu:yaw":    return float(hw.imu_device.yaw)
				if metric == "imu:pitch":  return float(hw.imu_device.pitch)
				if metric == "imu:roll":   return float(hw.imu_device.roll)
				if metric == "imu:accelX": return float(hw.imu_device.accelerometerX)
				if metric == "imu:accelY": return float(hw.imu_device.accelerometerY)
				if metric == "imu:accelZ": return float(hw.imu_device.accelerometerZ)
				if metric == "imu:gyroX":  return float(hw.imu_device.gyroscopeX)
				if metric == "imu:gyroY":  return float(hw.imu_device.gyroscopeY)
				if metric == "imu:gyroZ":  return float(hw.imu_device.gyroscopeZ)

			elif dtype == "ColorSensor":
				if metric == "reflection": return float(hw.sensor.reflection)
				if metric == "rawRed":     return float(hw.sensor.rawRed)
				if metric == "rawGreen":   return float(hw.sensor.rawGreen)
				if metric == "rawBlue":    return float(hw.sensor.rawBlue)
				if metric == "hue":        return float(hw.sensor.hue)
				if metric == "saturation": return float(hw.sensor.saturation)
				if metric == "value":      return float(hw.sensor.value)

			elif dtype == "Controller":
				if metric == "leftPercent":  return float(hw.sensor.leftPercent)
				if metric == "rightPercent": return float(hw.sensor.rightPercent)
				if metric == "leftAngle":    return float(hw.sensor.leftAngle)
				if metric == "rightAngle":   return float(hw.sensor.rightAngle)

		except Exception as e:
			log(f"Read error [{metric}]: {e}", "log-warn")
		return None

	def _read_input_val(self):
		if not self._input_panel_id or not self._input_metric:
			return None
		return self._read_metric_value(self._input_panel_id, self._input_metric)

	# ----------------------------------------------------------
	# Output hardware: read (training) vs. drive (deploy)
	# ----------------------------------------------------------

	def _read_output_train_val(self):
		"""
		Read the CURRENT value of the output device for the chosen output metric.
		Called during training to snapshot what will be replayed during deploy.

		The output metric key encodes what to read:
		  pos_rel  → .position          (relative degrees currently)
		  pos_abs  → .absolutePosition  (absolute degrees currently)
		  speed    → .speed             (current speed %)
		"""
		if not self._output_panel_id or not self._output_metric:
			return None
		hw = self._find_hw(self._output_panel_id)
		if hw is None or not getattr(hw, "connected", True):
			return None
		info  = self._device_info.get(self._output_panel_id, {})
		dtype = info.get("type", "")
		le    = self._le_module
		try:
			if dtype == "SingleMotor":
				if self._output_metric == "pos_rel":  return float(hw.motor.position)
				if self._output_metric == "pos_abs":  return float(hw.motor.absolutePosition)
				if self._output_metric == "speed":    return float(hw.motor.speed)

			elif dtype == "DoubleMotor" and le is not None:
				if self._output_metric == "left:pos_rel":  return float(hw.motor[le.MOTOR_LEFT].position)
				if self._output_metric == "left:pos_abs":  return float(hw.motor[le.MOTOR_LEFT].absolutePosition)
				if self._output_metric == "left:speed":    return float(hw.motor[le.MOTOR_LEFT].speed)
				if self._output_metric == "right:pos_rel": return float(hw.motor[le.MOTOR_RIGHT].position)
				if self._output_metric == "right:pos_abs": return float(hw.motor[le.MOTOR_RIGHT].absolutePosition)
				if self._output_metric == "right:speed":   return float(hw.motor[le.MOTOR_RIGHT].speed)

		except Exception as e:
			log(f"Output read error: {e}", "log-warn")
		return None

	def _drive_output(self, target_val):
		"""
		Issue the appropriate motor command for the chosen output metric.

		pos_rel  → motor_run_to_relative_position(val, speed=100)
				   Only fires when target_val changes (accumulated relative
				   moves would drift if repeated every tick).
		pos_abs  → motor_run_to_absolute_position(val, speed=100)
				   Safe to repeat; motor holds the position.
		speed    → SingleMotor: hw.run(direction, abs(val))
				   DoubleMotor: hw.motor_set_speed(val, motor=...)
				   Issues every tick for continuous control.
		"""
		if not self._output_panel_id or not self._output_metric:
			return
		hw = self._find_hw(self._output_panel_id)
		if hw is None or not getattr(hw, "connected", True):
			log("Output device disconnected", "log-warn")
			window.demoActive = False
			self.controls.reset("run")
			return
		info  = self._device_info.get(self._output_panel_id, {})
		dtype = info.get("type", "")
		le    = self._le_module
		met   = self._output_metric or ""

		try:
			val = float(target_val)
			ival = int(round(val))

			# ── SingleMotor ──────────────────────────────────────
			if dtype == "SingleMotor":
				if met == "pos_rel":
					# only fire on change to avoid accumulated drift
					if val == self._last_drive_val:
						return
					hw.motor_run_to_relative_position(position=ival, speed=100)
				elif met == "pos_abs":
					hw.motor_run_to_absolute_position(position=ival, speed=100)
				elif met == "speed" and le is not None:
					if abs(val) < 1:
						hw.motor_stop()
					elif val > 0:
						hw.run(direction=le.MOTOR_MOVE_DIRECTION_CLOCKWISE,
							   speed=abs(ival))
					else:
						hw.run(direction=le.MOTOR_MOVE_DIRECTION_COUNTERCLOCKWISE,
							   speed=abs(ival))

			# ── DoubleMotor ──────────────────────────────────────
			elif dtype == "DoubleMotor" and le is not None:
				if met == "left:pos_rel":
					if val == self._last_drive_val:
						return
					hw.motor_run_to_relative_position(
						position=ival, motor=le.MOTOR_LEFT, speed=100)
				elif met == "left:pos_abs":
					hw.motor_run_to_absolute_position(
						position=ival, motor=le.MOTOR_LEFT, speed=100)
				elif met == "left:speed":
					hw.motor_set_speed(ival, motor=le.MOTOR_LEFT)
				elif met == "right:pos_rel":
					if val == self._last_drive_val:
						return
					hw.motor_run_to_relative_position(
						position=ival, motor=le.MOTOR_RIGHT, speed=100)
				elif met == "right:pos_abs":
					hw.motor_run_to_absolute_position(
						position=ival, motor=le.MOTOR_RIGHT, speed=100)
				elif met == "right:speed":
					hw.motor_set_speed(ival, motor=le.MOTOR_RIGHT)

			self._last_drive_val = val

		except Exception as e:
			log(f"Drive error: {e}", "log-warn")

	def _stop_output(self):
		"""Stop any running motor output safely."""
		if not self._output_panel_id:
			return
		hw = self._find_hw(self._output_panel_id)
		if hw is None:
			return
		info  = self._device_info.get(self._output_panel_id, {})
		dtype = info.get("type", "")
		le    = self._le_module
		met   = self._output_metric or ""
		try:
			if dtype == "SingleMotor":
				hw.motor_stop()
			elif dtype == "DoubleMotor" and le is not None:
				if "right" in met:
					hw.motor_stop(motor=le.MOTOR_RIGHT)
				else:
					hw.motor_stop(motor=le.MOTOR_LEFT)
		except Exception:
			pass
		self._last_drive_val = None

	# ----------------------------------------------------------
	# Training data
	# ----------------------------------------------------------

	def _record_sample(self):
		"""Snapshot current input + output values as a training pair."""
		in_val = self._read_input_val()
		if in_val is None:
			log("No input reading — check Input Device / metric.", "log-warn")
			return
		out_val = self._read_output_train_val()
		if out_val is None:
			log("No output reading — check Output Device / metric.", "log-warn")
			return
		self._samples.append([in_val, out_val])
		n = len(self._samples)
		self._sample_count_span.textContent = f"Samples: {n}"
		log(f"Sample {n}: in={in_val:.2f}, out={out_val:.2f}")
		window._nnAddPoint(in_val, out_val)

	def _clear_data(self):
		self._samples = []
		self._last_drive_val = None
		self._sample_count_span.textContent = "Samples: 0"
		self._status_span.textContent = "No data collected yet."
		window._nnClearChart()
		log("Training data cleared")

	# ----------------------------------------------------------
	# Nearest-neighbor inference
	# ----------------------------------------------------------

	def _nearest_neighbor(self, input_val):
		"""Return (nearest_in, nearest_out, idx) for the closest sample by input."""
		if not self._samples:
			return None
		best_idx  = 0
		best_dist = abs(self._samples[0][0] - input_val)
		for i in range(1, len(self._samples)):
			d = abs(self._samples[i][0] - input_val)
			if d < best_dist:
				best_dist = d
				best_idx  = i
		sx, sy = self._samples[best_idx]
		return sx, sy, best_idx

	# ----------------------------------------------------------
	# Chart bounds helper
	# ----------------------------------------------------------

	def _chart_bounds(self, extra_x=None, extra_y=None):
		"""
		Compute (xmin, xmax, ymin, ymax) for chart axis endpoints.
		Includes all training samples plus any extra scalar values passed in.
		Falls back to ±50 around the extra value when no samples exist.
		"""
		xs = [s[0] for s in self._samples]
		ys = [s[1] for s in self._samples]
		if extra_x is not None:
			xs.append(extra_x)
		if extra_y is not None:
			ys.append(extra_y)

		if not xs or not ys:
			cx = extra_x if extra_x is not None else 0
			cy = extra_y if extra_y is not None else 0
			return cx - 50, cx + 50, cy - 50, cy + 50

		xspan = max(xs) - min(xs)
		yspan = max(ys) - min(ys)
		xpad  = max(xspan * 0.12, 10.0)
		ypad  = max(yspan * 0.12, 10.0)
		return min(xs) - xpad, max(xs) + xpad, min(ys) - ypad, max(ys) + ypad

	# ----------------------------------------------------------
	# Chart.js via CDN  (Pattern from INSTRUCTIONS.md)
	# ----------------------------------------------------------

	def _inject_chart(self):
		"""
		Load Chart.js and set up a 4-dataset scatter chart:
		  0 — training points       (blue circles)
		  1 — vertical line         (yellow) — current input value
		  2 — horizontal dashed line(green)  — output value
		  3 — nearest-neighbor star (red)    — deploy mode only

		All JS helper names use _singleUnderscore to avoid Rule-1 mangling.

		_nnUpdateLive(curX, curY, xMin, xMax, yMin, yMax)
			→ datasets 1 + 2 only, clears dataset 3 (used in collect mode)
		_nnUpdateDeploy(curX, nearX, nearY, xMin, xMax, yMin, yMax)
			→ all four datasets (used in deploy mode)
		"""
		_inject_script("""
window._nnChartReady = false;
window._nnChart      = null;

/* No-ops while Chart.js is loading — become live on s.onload */
window._nnAddPoint = function(x, y) {
  if (!window._nnChartReady || !window._nnChart) return;
  window._nnChart.data.datasets[0].data.push({x: x, y: y});
  window._nnChart.update('none');
};

window._nnClearChart = function() {
  if (!window._nnChartReady || !window._nnChart) return;
  var ds = window._nnChart.data.datasets;
  for (var i = 0; i < ds.length; i++) ds[i].data = [];
  window._nnChart.update('none');
};

/* Collect mode: live crosshairs only (no nearest-neighbor star) */
window._nnUpdateLive = function(curX, curY, xMin, xMax, yMin, yMax) {
  if (!window._nnChartReady || !window._nnChart) return;
  var ds = window._nnChart.data.datasets;
  ds[1].data = [{x: curX, y: yMin}, {x: curX, y: yMax}];
  ds[2].data = [{x: xMin, y: curY}, {x: xMax, y: curY}];
  ds[3].data = [];
  window._nnChart.update('none');
};

/* Deploy mode: crosshairs + nearest-neighbor star */
window._nnUpdateDeploy = function(curX, nearX, nearY, xMin, xMax, yMin, yMax) {
  if (!window._nnChartReady || !window._nnChart) return;
  var ds = window._nnChart.data.datasets;
  ds[1].data = [{x: curX, y: yMin}, {x: curX, y: yMax}];
  ds[2].data = [{x: xMin, y: nearY}, {x: xMax, y: nearY}];
  ds[3].data = [{x: nearX, y: nearY}];
  window._nnChart.update('none');
};

window._nnClearDeploy = function() {
  if (!window._nnChartReady || !window._nnChart) return;
  var ds = window._nnChart.data.datasets;
  ds[1].data = [];
  ds[2].data = [];
  ds[3].data = [];
  window._nnChart.update('none');
};

(function() {
  var s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
  s.onload = function() {
	var canvas = document.getElementById('nn-chart-canvas');
	if (!canvas) { window.jsLog('NN chart canvas not found','log-error'); return; }
	window._nnChart = new Chart(canvas.getContext('2d'), {
	  type: 'scatter',
	  data: {
		datasets: [
		  {
			label: 'Training Data',
			data: [],
			backgroundColor: 'rgba(74,144,217,0.85)',
			borderColor:     'rgba(74,144,217,1)',
			pointRadius: 6,
			pointHoverRadius: 9,
			order: 4
		  },
		  {
			label: 'Current Input',
			data: [],
			type: 'line',
			showLine: true,
			fill: false,
			borderColor: 'rgba(255,220,50,0.9)',
			borderWidth: 2,
			pointRadius: 0,
			order: 1
		  },
		  {
			label: 'Output Value',
			data: [],
			type: 'line',
			showLine: true,
			fill: false,
			borderColor: 'rgba(100,220,100,0.9)',
			borderWidth: 2,
			borderDash: [6, 4],
			pointRadius: 0,
			order: 2
		  },
		  {
			label: 'Nearest Neighbor',
			data: [],
			backgroundColor: 'rgba(255,90,60,0.95)',
			borderColor:     'rgba(255,200,180,1)',
			borderWidth: 2,
			pointRadius: 10,
			pointStyle: 'star',
			order: 3
		  }
		]
	  },
	  options: {
		responsive: false,
		animation: false,
		plugins: {
		  legend: {
			position: 'bottom',
			labels: { color: '#bbb', font: { size: 11 }, boxWidth: 14 }
		  }
		},
		scales: {
		  x: {
			type: 'linear',
			title: { display: true, text: 'Input Value', color: '#9ab' },
			ticks: { color: '#9ab' },
			grid:  { color: 'rgba(255,255,255,0.08)' }
		  },
		  y: {
			type: 'linear',
			title: { display: true, text: 'Output Value', color: '#9ab' },
			ticks: { color: '#9ab' },
			grid:  { color: 'rgba(255,255,255,0.08)' }
		  }
		}
	  }
	});
	window._nnChartReady = true;
	window.jsLog('NN chart ready');
  };
  s.onerror = function() {
	window.jsLog('Failed to load Chart.js from CDN','log-error');
  };
  document.head.appendChild(s);
})();
""")

	# ----------------------------------------------------------
	# Poll loop — verbatim setTimeout pattern (Rule 4)
	# ----------------------------------------------------------

	def _start_loop(self):
		window._demoUpdate    = create_proxy(self.update)
		window.pollIntervalMs = POLL_INTERVAL_MS

		_inject_script("""
(function() {
  window._demoPollGen = (window._demoPollGen || 0) + 1;
  var gen = window._demoPollGen, ms = window.pollIntervalMs;
  async function loop() {
	if (gen !== window._demoPollGen) return;
	try { await window._demoUpdate(); }
	catch(e) { window.jsLog("update error: " + e, "log-error"); }
	setTimeout(loop, ms);
  }
  loop();
})();
""")

	# ----------------------------------------------------------
	# Per-tick update — MUST be async def (Rule 2)
	# ----------------------------------------------------------

	async def update(self, *_args):
		"""
		Called every POLL_INTERVAL_MS ms.

		Collect mode:
		  • Reads live input + output values each tick.
		  • Draws yellow vertical line (current input) and green dashed horizontal
			line (current output) so you can see exactly where a new sample will
			land before pressing ⊕ Record.

		Deploy mode:
		  • Finds nearest training sample by input distance.
		  • Issues the motor command for that sample's output value.
		  • pos_rel commands only fire when the nearest neighbor changes to avoid
			accumulated drift; speed and pos_abs commands fire every tick.
		  • Updates the chart: vertical line = current input, dashed line =
			output being commanded, star = matched training sample.
		"""
		if not window.demoActive:
			return

		try:
			in_val = self._read_input_val()
		except Exception as e:
			log(f"Input read failed: {e}", "log-error")
			return

		in_lbl  = self._input_met_labels.get(self._input_metric or "",  "input")
		out_lbl = self._output_met_labels.get(self._output_metric or "", "output")

		# ══ COLLECT MODE ══════════════════════════════════════════════════════
		if self._mode == "collect":
			out_val = None
			try:
				out_val = self._read_output_train_val()
			except Exception:
				pass

			in_str  = f"{in_val:.1f}"  if in_val  is not None else "—"
			out_str = f"{out_val:.1f}" if out_val is not None else "—"
			n = len(self._samples)
			self._status_span.textContent = (
				f"{in_lbl}: {in_str}  |  {out_lbl}: {out_str}\n"
				f"Samples: {n}  — press ⊕ Record to capture"
			)

			# Show live crosshairs on the chart so the user can see where
			# the next sample will land before committing.
			if in_val is not None and out_val is not None:
				xmin, xmax, ymin, ymax = self._chart_bounds(in_val, out_val)
				window._nnUpdateLive(in_val, out_val, xmin, xmax, ymin, ymax)
			else:
				window._nnClearDeploy()
			return

		# ══ DEPLOY MODE ═══════════════════════════════════════════════════════
		if in_val is None:
			self._status_span.textContent = "Waiting for input reading…"
			return

		if not self._samples:
			self._status_span.textContent = (
				"No training data.\n"
				"Switch to Collect mode and record some samples first."
			)
			window._nnClearDeploy()
			return

		result = self._nearest_neighbor(in_val)
		if result is None:
			return
		near_x, near_y, near_idx = result

		# Drive output motor
		try:
			self._drive_output(near_y)
		except Exception as e:
			log(f"Drive error: {e}", "log-error")

		# Update chart
		xmin, xmax, ymin, ymax = self._chart_bounds(in_val, near_y)
		window._nnUpdateDeploy(in_val, near_x, near_y, xmin, xmax, ymin, ymax)

		self._status_span.textContent = (
			f"{in_lbl}: {in_val:.1f}  →  nearest at {near_x:.1f}\n"
			f"{out_lbl} output: {near_y:.1f}  "
			f"(sample {near_idx + 1} of {len(self._samples)})"
		)


# =============================================================
# ENTRY POINT — do not modify
# =============================================================

_demo = None   # module-level ref prevents GC of demo object + create_proxy callbacks

def main():
	global _demo
	_demo = NearestNeighborDemo()

main()
