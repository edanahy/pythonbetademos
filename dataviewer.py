## live_data_viewer.py — Live Device Data Viewer Demo (PyScript / browser)
## =====================================================================
##
## Streams live data from a connected LEGO device into a Chart.js line graph.
## Left column:  device selector drop-down + metric picker + Refresh button.
## Right column: live streaming Chart.js graph (last 50 samples, auto y-scale).
##
## No LEGO devices need to be pre-configured.  Click "Refresh Devices" at
## runtime to discover whatever is connected, then pick a device + metric.
##
## Hardware APIs (all synchronous property reads — no await needed):
##   SingleMotor  →  hw.motor.<prop>
##   DoubleMotor  →  hw.motor[le.MOTOR_LEFT/RIGHT].<prop>  or  hw.imu_device.<prop>
##   ColorSensor  →  hw.sensor.<prop>
##   Controller   →  hw.sensor.<prop>
##
## To add more metrics: add (key, label) entries to DEVICE_METRICS in SECTION 2,
## following the "source.property" key convention.  No other code needs changing.

from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy


# =========================================================
# SECTION 1 — CALLBACKS / EVENT HANDLERS   (edit this)
# =========================================================

def on_stream_start():
	"""Called when the user clicks Start Streaming."""
	log("Streaming started")


def on_stream_stop():
	"""Called when the user clicks Stop Streaming."""
	log("Streaming stopped")


# No StateMachine used in this demo — kept for template compatibility.
STATE_HANDLERS = {}


# =========================================================
# SECTION 2 — CONFIGURATION   (edit this)
# =========================================================

# --- Layout -------------------------------------------------
FRAME_W          = 300    # left column panel width  (px)

# --- Timing -------------------------------------------------
POLL_INTERVAL_MS = 200    # update() period in ms  (5 Hz)

# --- Appearance ---------------------------------------------
PANEL_BG         = "#1a1a2e"
RIGHT_COL_WIDTH  = 440
BAR_COLOR        = "#4A90D9"

# --- Graph --------------------------------------------------
MAX_SAMPLES      = 50     # rolling window (x-axis length)

# --- Available metrics per device type ----------------------
#
# Key convention:  "<source>.<property>"
#   SingleMotor:    source = "motor"
#   DoubleMotor:    source = "motor_left" | "motor_right" | "imu"
#   ColorSensor:    source = "sensor"
#   Controller:     source = "sensor"
#
# To add a metric: append a (key, label) tuple here, then make sure
# LiveDataViewerDemo._read_value() handles it (it parses the key automatically
# for all existing sources — new properties within a known source just work).

DEVICE_METRICS = {
	"SingleMotor": [
		("motor.position",    "Position (°)"),
		("motor.absolutePos", "Absolute Position (°)"),
		("motor.speed",       "Speed (%)"),
		("motor.power",       "Power (%)"),
	],
	"DoubleMotor": [
		("motor_left.position",    "Left — Position (°)"),
		("motor_left.absolutePos", "Left — Absolute Position (°)"),
		("motor_left.speed",       "Left — Speed (%)"),
		("motor_left.power",       "Left — Power (%)"),
		("motor_right.position",   "Right — Position (°)"),
		("motor_right.absolutePos","Right — Absolute Position (°)"),
		("motor_right.speed",      "Right — Speed (%)"),
		("motor_right.power",      "Right — Power (%)"),
		("imu.yaw",                "IMU — Yaw (°)"),
		("imu.pitch",              "IMU — Pitch (°)"),
		("imu.roll",               "IMU — Roll (°)"),
		("imu.accelerometerX",     "IMU — Accel X"),
		("imu.accelerometerY",     "IMU — Accel Y"),
		("imu.accelerometerZ",     "IMU — Accel Z"),
		("imu.gyroscopeX",         "IMU — Gyro X"),
		("imu.gyroscopeY",         "IMU — Gyro Y"),
		("imu.gyroscopeZ",         "IMU — Gyro Z"),
	],
	"ColorSensor": [
		("sensor.reflection",  "Reflection"),
		("sensor.rawRed",      "Raw Red"),
		("sensor.rawGreen",    "Raw Green"),
		("sensor.rawBlue",     "Raw Blue"),
		("sensor.hue",         "Hue"),
		("sensor.saturation",  "Saturation"),
		("sensor.value",       "Value (brightness)"),
	],
	"Controller": [
		("sensor.leftPercent",  "Left Lever (%)"),
		("sensor.rightPercent", "Right Lever (%)"),
		("sensor.leftAngle",    "Left Angle (°)"),
		("sensor.rightAngle",   "Right Angle (°)"),
	],
}

# Unused by this demo but kept as a config hook for frame sizing parity.
FRAME_H = 320


# =========================================================
# INFRASTRUCTURE — do not modify; copy-portable across demos
# =========================================================

# ---------------------------------------------------------
# _inject_script — append a <script> block to document.body
# ---------------------------------------------------------

def _inject_script(js_text: str):
	s = document.createElement("script")
	s.text = js_text
	document.body.appendChild(s)


# ---------------------------------------------------------
# Logger — writes to the on-page #log panel
# ---------------------------------------------------------

class Logger:
	"""
	Timestamped writer for #log.  Also injects window.jsLog() so
	injected JS snippets can write to the same panel.
	CSS classes: "log-info" (default) | "log-warn" | "log-error"
	"""

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


# Module-level log() — callable from SECTION 1 handlers above.
log = Logger()


# ---------------------------------------------------------
# StateMachine — debounced, edge-triggered FSM
# Remove this class if your demo has no discrete state transitions.
# ---------------------------------------------------------

class StateMachine:
	"""
	update(candidate) each tick; on_enter(state) fires once per
	accepted transition after `debounce_ticks` consecutive votes.
	State is committed BEFORE the handler is called — this is
	intentional: a handler exception won't cause repeated re-fires.
	"""

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
			self._current = candidate          # commit first — see docstring
			self._on_enter(candidate)


# ---------------------------------------------------------
# CameraComponent — webcam + MediaPipe PoseLandmarker
# Remove this class if your demo does not use body-pose detection.
# ---------------------------------------------------------

class CameraComponent:
	"""
	Manages webcam stream and MediaPipe pose detection.
	.video  — hidden <video> (raw stream source)
	.canvas — visible <canvas> (cropped frame + skeleton overlay)
	Publishes window.poseData / window.poseReady each detected frame.
	"""

	def __init__(self, frame_w: int, frame_h: int):
		self._w = frame_w
		self._h = frame_h
		self._build_elements()
		self._inject_js()

	@property
	def video(self):  return self._video
	@property
	def canvas(self): return self._canvas

	def start(self): window._startCamera()   # single underscore — see ⚠ above
	def stop(self):  window._stopCamera()

	def _build_elements(self):
		self._video             = document.createElement("video")
		self._video.autoplay    = True
		self._video.playsInline = True
		self._video.muted       = True
		self._video.style.display = "none"

		self._canvas              = document.createElement("canvas")
		self._canvas.width        = self._w
		self._canvas.height       = self._h
		self._canvas.style.width  = f"{self._w}px"
		self._canvas.style.height = f"{self._h}px"

		window.videoElement   = self._video
		window.canvasElement  = self._canvas
		window.frameW         = self._w
		window.frameH         = self._h
		window.poseData       = {"leftWristY": None, "leftShoulderY": None,
								  "rightWristY": None, "rightShoulderY": None}
		window.poseReady      = False
		window.cameraActive   = False
		window.trackingActive = False

	def _inject_js(self):
		_inject_script("""
window._poseApp = window._poseApp || {};
window._startCamera = async function() {
  var app = window._poseApp;
  if (app.stream) { window.jsLog("Camera already active","log-warn"); return; }
  var W = window.frameW, H = window.frameH;
  var vis = await import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest");
  var PL = vis.PoseLandmarker, FR = vis.FilesetResolver, DU = vis.DrawingUtils;
  var video = window.videoElement, canvas = window.canvasElement, ctx = canvas.getContext("2d");
  var stream = await navigator.mediaDevices.getUserMedia({video:{width:W,height:H},audio:false});
  video.srcObject = stream; await video.play();
  if (video.readyState < 1)
	await new Promise(function(r){video.addEventListener("loadedmetadata",r,{once:true});});
  var fs = await FR.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm");
  var lm = await PL.createFromOptions(fs, {baseOptions:{modelAssetPath:
	"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"},
	runningMode:"VIDEO"});
  var du = new DU(ctx);
  var vw=video.videoWidth, vh=video.videoHeight, tA=W/H, sA=vw/vh, sx,sy,sw,sh;
  if(sA>tA){sh=vh;sw=vh*tA;sx=(vw-sw)/2;sy=0;}else{sw=vw;sh=vw/tA;sx=0;sy=(vh-sh)/2;}
  app.stream=stream; app.landmarker=lm; app.du=du;
  app.crop={sx:sx,sy:sy,sw:sw,sh:sh}; app.lastTime=-1; app.rafId=null;
  window.cameraActive=true; window.jsLog("Camera started");
  function loop(){
	if(!window.cameraActive)return;
	if(video.currentTime!==app.lastTime){
	  app.lastTime=video.currentTime; var c=app.crop;
	  ctx.drawImage(video,c.sx,c.sy,c.sw,c.sh,0,0,W,H);
	  if(window.trackingActive){
		var r=app.landmarker.detectForVideo(canvas,performance.now());
		if(r.landmarks&&r.landmarks.length>0){
		  var p=r.landmarks[0];
		  app.du.drawLandmarks(p,{radius:3}); app.du.drawConnectors(p,PL.POSE_CONNECTIONS);
		  window.poseData={leftShoulderY:p[11].y,rightShoulderY:p[12].y,
						   leftWristY:p[15].y,rightWristY:p[16].y};
		  window.poseReady=true;
		}
	  }
	}
	app.rafId=requestAnimationFrame(loop);
  }
  loop();
};
window._stopCamera=function(){
  var app=window._poseApp;
  window.cameraActive=false; window.trackingActive=false; window.poseReady=false;
  if(app.rafId){cancelAnimationFrame(app.rafId);app.rafId=null;}
  if(app.stream){app.stream.getTracks().forEach(function(t){t.stop();});app.stream=null;}
  var v=window.videoElement,c=window.canvasElement;
  if(v)v.srcObject=null;
  if(c)c.getContext("2d").clearRect(0,0,c.width,c.height);
  window.jsLog("Camera stopped");
};
""")


# ---------------------------------------------------------
# PoseClassifier — wrist/shoulder Y → state confidences
# Remove if your demo doesn't use body-pose classification.
# ---------------------------------------------------------

class PoseClassifier:
	"""
	Sigmoid-based arm-pose scorer. Returns dict[label → float] summing to ~1.
	Replace compute() with a trained model when ready.
	"""
	LABELS = ["left_up", "right_up", "both_up", "both_down"]

	@staticmethod
	def compute(lw_y, ls_y, rw_y, rs_y) -> dict:
		import math
		def sig(m): return 1.0 / (1.0 + math.exp(-12.0 * m))
		l = sig(ls_y - lw_y);  r = sig(rs_y - rw_y)
		raw = {"left_up": l*(1-r), "right_up": r*(1-l),
			   "both_up": l*r,     "both_down": (1-l)*(1-r)}
		t = sum(raw.values()) or 1.0
		return {k: v/t for k, v in raw.items()}


# ---------------------------------------------------------
# ToggleButton — two-state button
# ---------------------------------------------------------

class ToggleButton:
	"""
	Alternates between two labels; fires on_on / on_off on each flip.
	guard_on (optional callable → bool): only checked on OFF → ON.
	ON → OFF is never guarded.
	"""

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
		"""Force back to inactive state without firing callbacks."""
		self._active = False
		self.element.textContent = self._label_on


# ---------------------------------------------------------
# ControlsRow — horizontal strip of ToggleButtons
# ---------------------------------------------------------

class ControlsRow:
	"""
	row = ControlsRow()
	row.add("key", "Label On", "Label Off", on_on=fn, on_off=fn)
	row.add("key2", ..., guard_on=lambda: bool(some_condition))
	parent_el.appendChild(row.element)
	row.reset("key")   # reset button to inactive state, no callbacks fired
	"""

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


# ---------------------------------------------------------
# BarChartPanel — vertical stack of labelled progress bars
# A good default for confidence/signal displays.
# Replace with your own panel class for other visualizations.
# ---------------------------------------------------------

class BarChartPanel:
	"""
	bars = BarChartPanel(width_px=260, default_color="#4A90D9")
	bars.add("key", "Label")       # add a row; chainable
	bars.update({"key": 0.75})     # value in [0, 1]
	"""

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
		lbl.textContent = label; lbl.style.marginBottom = "2px"
		track = document.createElement("div")
		track.style.cssText = ("width:100%;height:14px;background:#eee;"
							   "border-radius:4px;overflow:hidden;")
		fill  = document.createElement("div")
		fill.style.cssText = (f"height:100%;width:0%;background:{color};"
							  "transition:width 0.08s linear;")
		track.appendChild(fill); row.appendChild(lbl); row.appendChild(track)
		self.element.appendChild(row)
		self._fills[key] = fill
		return self

	def update(self, values: dict):
		for key, fill in self._fills.items():
			pct = max(0.0, min(1.0, values.get(key, 0.0))) * 100
			fill.style.width = f"{pct:.1f}%"


# =========================================================
# LIVE DATA VIEWER DEMO
# =========================================================

class LiveDataViewerDemo:
	"""
	Streams live sensor / motor data into a Chart.js line graph.

	Left column:  device selector + metric picker + Refresh button.
	Right column: live Chart.js graph (last MAX_SAMPLES readings, auto y-scale).

	Hardware reads are synchronous property accesses (not async calls):
	  SingleMotor  →  hw.motor.<prop>
	  DoubleMotor  →  hw.motor[le.MOTOR_LEFT].<prop>  or  hw.imu_device.<prop>
	  ColorSensor  →  hw.sensor.<prop>
	  Controller   →  hw.sensor.<prop>
	"""

	def __init__(self):
		self.controls = ControlsRow()

		# ── Runtime state ──────────────────────────────────────
		self._devices       = {}    # id → {id, type, name}
		self._metric_labels = {}    # metric key → display label (Python lookup)
		self._device_id    = None
		self._device_type  = None
		self._device_name  = None
		self._metric_key   = None
		self._metric_label = None

		# ── DOM refs (populated in _build_layout) ──────────────
		self._device_select = None
		self._metric_select = None
		self._metric_row    = None
		self._status_txt    = None
		self._chart_info    = None
		self._run_btn       = None  # ref to the Start/Stop ToggleButton

		# ── Cached cross-module references ─────────────────────
		# Both scripts share the same Pyodide runtime; main_pyodide.py is
		# registered in sys.modules under 'main_pyodide' (PyScript file-based
		# modules).  We resolve these once so _read_value() stays cheap.
		self._panel_devices_ref = None   # ref to main_pyodide._panel_devices dict
		self._le_module         = None   # ref to legoeducation module (for le.MOTOR_LEFT)
		self._resolve_main_refs()

		# ── Initialise in dependency order ─────────────────────
		self._inject_device_helpers()   # defines window.getConnectedDevices
		self._inject_chart_helpers()    # loads Chart.js + push/reset helpers
		self._build_layout()
		self._setup_controls()
		self._start_loop()

		self._refresh_devices()         # auto-populate device list on load
		log("Live Data Viewer ready — select a device and metric, then Start Streaming")

	# ── Cross-module device lookup ──────────────────────────────────────────

	def _resolve_main_refs(self):
		"""
		Resolve references to main_pyodide's _panel_devices dict and the
		legoeducation module (le), caching them for fast per-tick access.

		Strategy (tried in order):
		  1. sys.modules['main_pyodide']   — PyScript 2024+ file-based modules
		  2. sys.modules['__main__']       — older PyScript (shared __main__)
		  3. scan all sys.modules          — last resort; finds it regardless of name
		"""
		import sys

		for mod_name in ("main_pyodide", "__main__"):
			mod = sys.modules.get(mod_name)
			if mod is None:
				continue
			d = getattr(mod, "_panel_devices", None)
			if isinstance(d, dict):
				self._panel_devices_ref = d
				le = getattr(mod, "le", None)
				if le is not None:
					self._le_module = le
				return   # found — no need to keep scanning

		# Fallback: scan every loaded module
		import sys as _sys
		for mod in list(_sys.modules.values()):
			if mod is None:
				continue
			d = getattr(mod, "_panel_devices", None)
			if isinstance(d, dict):
				self._panel_devices_ref = d
				if self._le_module is None:
					self._le_module = getattr(mod, "le", None)
				return

		log("Could not locate _panel_devices in sys.modules — "
			"hardware reads will fail", "log-warn")

	def _find_hw_device(self):
		"""
		Return the Python device object for the currently selected device,
		or None if it is not found / not connected.

		Devices live in main_pyodide._panel_devices keyed by panel_id.
		self._device_id holds that same panel_id (from row.dataset.deviceId).
		"""
		if self._panel_devices_ref is None:
			self._resolve_main_refs()    # retry once in case init was too early
		if self._panel_devices_ref is None:
			return None
		return self._panel_devices_ref.get(self._device_id)

	# ── JS injection ────────────────────────────────────────────────────────

	def _inject_device_helpers(self):
		"""
		Inject getConnectedDevices() per the page contract.
		Scans #device-rows for rows whose .status-dot has class 'connected'.
		Returns Array<{id: string, type: string, name: string}>.
		"""
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
	  id:   row.dataset.deviceId || null,
	  type: sel ? sel.value        : null,
	  name: inp ? inp.value.trim() : ''
	});
  });
  return connected;
}
window.getConnectedDevices = getConnectedDevices;
""")

	def _inject_chart_helpers(self):
		"""
		Load Chart.js 4 from CDN, then create the chart on #liveChartCanvas.

		Safe-to-call-before-load helpers exposed on window:
		  _pushLiveChartValue(value, label) — append one sample, trim to MAX_SAMPLES
		  _resetLiveChart(label)            — clear history, rename series
		"""
		max_s = str(MAX_SAMPLES)
		_inject_script("""
/* Helpers defined immediately; no-ops until Chart.js is ready */
window._chartReady   = false;
window._chartSampleN = 0;

window._pushLiveChartValue = function(value, label) {
  if (!window._chartReady || !window._liveChart) return;
  var chart = window._liveChart;
  window._chartSampleN += 1;
  chart.data.labels.push(window._chartSampleN);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > """ + max_s + """) {
	chart.data.labels.shift();
	chart.data.datasets[0].data.shift();
  }
  if (label != null) chart.data.datasets[0].label = label;
  chart.update('none');
};

window._resetLiveChart = function(label) {
  window._chartSampleN = 0;
  if (!window._liveChart) return;
  window._liveChart.data.labels = [];
  window._liveChart.data.datasets[0].data = [];
  if (label != null) window._liveChart.data.datasets[0].label = label;
  window._liveChart.update('none');
};

/* Load Chart.js 4 from CDN, then wire up the canvas */
(function() {
  var s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
  s.onload = function() {
	var canvas = document.getElementById('liveChartCanvas');
	if (!canvas) {
	  window.jsLog && window.jsLog('Chart canvas not found — reload page', 'log-error');
	  return;
	}
	window._liveChart = new Chart(canvas.getContext('2d'), {
	  type: 'line',
	  data: {
		labels: [],
		datasets: [{
		  label: 'Value',
		  data: [],
		  borderColor: '#4A90D9',
		  backgroundColor: 'rgba(74,144,217,0.08)',
		  borderWidth: 2,
		  tension: 0.35,
		  pointRadius: 2,
		  pointHoverRadius: 5,
		  fill: true
		}]
	  },
	  options: {
		animation: false,
		responsive: false,
		interaction: { mode: 'index', intersect: false },
		plugins: {
		  legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
		  tooltip: { enabled: true }
		},
		scales: {
		  x: {
			display: true,
			title: { display: true, text: 'Sample #', font: { size: 11 } },
			ticks: { maxTicksLimit: 8, font: { size: 10 }, color: '#555' }
		  },
		  y: {
			display: true,
			title: { display: true, text: 'Value', font: { size: 11 } },
			ticks: { font: { size: 10 }, color: '#555' }
			/* no min/max: Chart.js auto-scales to the data range */
		  }
		}
	  }
	});
	window._chartReady = true;
	window.jsLog && window.jsLog('Chart.js ready');
  };
  s.onerror = function() {
	window.jsLog && window.jsLog('Failed to load Chart.js from CDN', 'log-error');
  };
  document.head.appendChild(s);
})();
""")

	# ── Layout ──────────────────────────────────────────────────────────────

	def _build_layout(self):
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		header = document.createElement("div")
		header.className = "panel-header"
		h2 = document.createElement("h2")
		h2.textContent = "Live Device Data Viewer"
		header.appendChild(h2)

		columns = document.createElement("div")
		columns.className            = "columns-panel"
		columns.style.display        = "flex"
		columns.style.flexDirection  = "row"
		columns.style.gap            = "20px"
		columns.style.alignItems     = "flex-start"
		columns.style.justifyContent = "center"

		columns.appendChild(self._build_left_col())
		columns.appendChild(self._build_right_col())

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(columns, header.nextSibling)

	def _build_left_col(self):
		"""Device selector + metric picker panel (left column)."""
		col = document.createElement("div")
		col.style.display       = "flex"
		col.style.flexDirection = "column"
		col.style.alignItems    = "stretch"

		panel = document.createElement("div")
		panel.style.cssText = (
			f"width:{FRAME_W}px;padding:14px;box-sizing:border-box;"
			f"background:{PANEL_BG};color:#ddd;"
			"font-family:sans-serif;font-size:13px;border-radius:6px;"
		)

		def micro_label(text):
			el = document.createElement("p")
			el.textContent = text
			el.style.cssText = (
				"margin:0 0 5px;"
				"font-size:10px;text-transform:uppercase;"
				"letter-spacing:.07em;color:#aaa;"
			)
			return el

		# ── Device dropdown ──────────────────────────────────
		panel.appendChild(micro_label("Connected Device"))

		self._device_select = document.createElement("select")
		self._device_select.style.cssText = (
			"width:100%;padding:6px 8px;margin-bottom:6px;"
			"background:#2a2a4a;color:#ddd;"
			"border:1px solid #555;border-radius:4px;font-size:13px;"
		)
		opt0 = document.createElement("option")
		opt0.value = ""
		opt0.textContent = "— click Refresh to load —"
		self._device_select.appendChild(opt0)

		def _dev_change(ev):
			self._on_device_selected()
		self._device_select.addEventListener("change", create_proxy(_dev_change))
		panel.appendChild(self._device_select)

		# Refresh button
		refresh_btn = document.createElement("button")
		refresh_btn.setAttribute("type", "button")
		refresh_btn.textContent = "⟳  Refresh Devices"
		refresh_btn.style.cssText = (
			"width:100%;padding:6px 8px;margin-bottom:14px;"
			"background:#3a3a6a;color:#ddd;"
			"border:1px solid #666;border-radius:4px;"
			"cursor:pointer;font-size:12px;"
		)
		def _do_refresh(ev):
			self._refresh_devices()
		refresh_btn.addEventListener("click", create_proxy(_do_refresh))
		panel.appendChild(refresh_btn)

		# ── Metric dropdown (hidden until device chosen) ──────
		self._metric_row = document.createElement("div")
		self._metric_row.style.display = "none"
		self._metric_row.appendChild(micro_label("Data to Stream"))

		self._metric_select = document.createElement("select")
		self._metric_select.style.cssText = (
			"width:100%;padding:6px 8px;margin-bottom:14px;"
			"background:#2a2a4a;color:#ddd;"
			"border:1px solid #555;border-radius:4px;font-size:13px;"
		)
		def _metric_change(ev):
			self._on_metric_selected()
		self._metric_select.addEventListener("change", create_proxy(_metric_change))
		self._metric_row.appendChild(self._metric_select)
		panel.appendChild(self._metric_row)

		# Status line
		self._status_txt = document.createElement("p")
		self._status_txt.textContent = "No device selected."
		self._status_txt.style.cssText = "margin:0;font-size:11px;color:#888;"
		panel.appendChild(self._status_txt)

		col.appendChild(panel)
		col.appendChild(self.controls.element)
		return col

	def _build_right_col(self):
		"""Info header + Chart.js canvas (right column)."""
		col = document.createElement("div")
		col.style.display       = "flex"
		col.style.flexDirection = "column"
		col.style.alignItems    = "stretch"

		self._chart_info = document.createElement("div")
		self._chart_info.style.cssText = (
			f"width:{RIGHT_COL_WIDTH}px;"
			"padding:6px 2px 8px;"
			"font-family:sans-serif;font-size:13px;color:#555;"
		)
		self._chart_info.textContent = "Select a device and metric to begin."
		col.appendChild(self._chart_info)

		canvas = document.createElement("canvas")
		canvas.id     = "liveChartCanvas"
		canvas.width  = RIGHT_COL_WIDTH
		canvas.height = 280
		canvas.style.cssText = (
			f"width:{RIGHT_COL_WIDTH}px;height:280px;"
			"border:1px solid #ddd;border-radius:4px;"
			"background:#fff;display:block;"
		)
		col.appendChild(canvas)
		return col

	# ── Controls ────────────────────────────────────────────────────────────

	def _setup_controls(self):
		window.demoActive = False

		def _can_start():
			return self._device_name is not None and self._metric_key is not None

		def _start():
			# guard_on already blocks the click when not ready, but keep the
			# log message as a belt-and-suspenders safety net
			if not _can_start():
				log("Select a device and metric first.", "log-warn")
				self.controls.reset("run")
				return
			window.demoActive = True
			window._resetLiveChart(self._metric_label)
			on_stream_start()

		def _stop():
			window.demoActive = False
			on_stream_stop()

		self._run_btn = self.controls.add("run",
										  "▶  Start Streaming",
										  "■  Stop Streaming",
										  on_on=_start, on_off=_stop,
										  guard_on=_can_start)
		self._update_start_btn()   # initial state: visually disabled

	def _update_start_btn(self):
		"""Visually enable/disable the Start button based on selection state."""
		if self._run_btn is None:
			return
		ready = self._device_name is not None and self._metric_key is not None
		el = self._run_btn.element
		if ready:
			el.style.opacity = "1"
			el.style.cursor  = "pointer"
			el.title         = ""
		else:
			el.style.opacity = "0.4"
			el.style.cursor  = "not-allowed"
			el.title         = "Select a device and metric first"

	# ── Device / metric selection ────────────────────────────────────────────

	def _refresh_devices(self):
		"""
		Call window.getConnectedDevices() and rebuild the device dropdown.
		Diffs against self._devices (by id) so a re-refresh preserves any
		current selection if that device id is still present.
		"""
		get_fn = getattr(window, "getConnectedDevices", None)
		if get_fn is None:
			log("getConnectedDevices not yet ready — try again", "log-warn")
			return

		try:
			raw_list = get_fn().to_py()
		except Exception as exc:
			log(f"getConnectedDevices error: {exc}", "log-error")
			raw_list = []

		# Normalise each entry to a plain Python dict
		devices = []
		for item in raw_list:
			if hasattr(item, "to_py"):
				item = item.to_py()
			dev_id = str(item.get("id", "") or "")
			if not dev_id:
				continue
			devices.append({
				"id":   dev_id,
				"type": str(item.get("type", "") or ""),
				"name": str(item.get("name", "") or ""),
			})

		self._devices = {d["id"]: d for d in devices}

		# Rebuild dropdown
		prev_id = self._device_id
		while self._device_select.options.length > 0:
			self._device_select.remove(0)

		if not devices:
			opt = document.createElement("option")
			opt.value = ""
			opt.textContent = "— no connected devices found —"
			self._device_select.appendChild(opt)
			self._metric_row.style.display = "none"
			self._status_txt.textContent   = "No connected devices."
			log("No connected devices found.", "log-warn")
			return

		n = len(devices)
		hdr = document.createElement("option")
		hdr.value = ""
		hdr.textContent = f"— select a device ({n} found) —"
		self._device_select.appendChild(hdr)

		restore_idx = 0
		for i, dev in enumerate(devices):
			opt = document.createElement("option")
			opt.value = dev["id"]
			display = dev["name"] if dev["name"] else dev["id"]
			opt.textContent = f"{display}  [{dev['type']}]"
			self._device_select.appendChild(opt)
			if dev["id"] == prev_id:
				restore_idx = i + 1   # +1 for the header option

		self._status_txt.textContent = f"{n} device{'s' if n != 1 else ''} found."
		log(f"Refreshed: {n} connected device(s)")

		# Restore previous selection if still connected
		if restore_idx > 0:
			self._device_select.selectedIndex = restore_idx
			new_dev  = self._devices.get(prev_id, {})
			new_type = new_dev.get("type", "")
			new_name = new_dev.get("name") or prev_id

			if new_type != self._device_type:
				# Device type changed at the same list position — repopulate metrics
				log(f"Device type changed ({self._device_type} → {new_type}); "
					"reloading metrics", "log-info")
				self._on_device_selected()
			elif new_name != self._device_name:
				# Name only changed — update display without disturbing metric selection
				self._device_name = new_name
				if self._metric_label and self._device_type:
					self._chart_info.textContent = (
						f"{self._device_type}  ·  {new_name}  ·  {self._metric_label}"
					)
					self._status_txt.textContent = f"Metric: {self._metric_label}"
				log(f"Device renamed → '{new_name}'")
		else:
			self._device_select.selectedIndex = 0
			self._metric_row.style.display = "none"
			self._device_id = None

	def _on_device_selected(self):
		"""Handle change event on the device <select>."""
		# Read .value directly — never subscript options[n] on a JsProxy
		dev_id = str(self._device_select.value or "")
		if not dev_id:
			self._metric_row.style.display = "none"
			self._device_id    = None
			self._device_type  = None
			self._device_name  = None
			self._metric_key   = None
			self._metric_label = None
			self._metric_labels = {}
			self._update_start_btn()
			return

		dev = self._devices.get(dev_id, {})
		self._device_id   = dev_id
		self._device_type = dev.get("type", "")
		self._device_name = dev.get("name") or dev_id

		log(f"Device: {self._device_name}  ({self._device_type})")

		metrics = DEVICE_METRICS.get(self._device_type, [])
		while self._metric_select.options.length > 0:
			self._metric_select.remove(0)

		# Build label lookup in Python so _on_metric_selected doesn't
		# need to subscript the options collection
		self._metric_labels = {}
		if not metrics:
			opt_none = document.createElement("option")
			opt_none.value = ""
			opt_none.textContent = "— no metrics defined for this device type —"
			self._metric_select.appendChild(opt_none)
		else:
			for key, label in metrics:
				self._metric_labels[key] = label
				o = document.createElement("option")
				o.value = key
				o.textContent = label
				self._metric_select.appendChild(o)

		self._metric_row.style.display = "block"
		self._on_metric_selected()   # auto-pick first entry

	def _on_metric_selected(self):
		"""Handle change event on the metric <select>."""
		if self._metric_select is None:
			return
		# Read .value directly — never subscript options[n] on a JsProxy
		metric_key = str(self._metric_select.value or "")
		if not metric_key:
			self._metric_key   = None
			self._metric_label = None
			self._update_start_btn()
			return

		self._metric_key   = metric_key
		# Label comes from the Python dict built in _on_device_selected
		self._metric_label = self._metric_labels.get(metric_key, metric_key)

		name  = self._device_name or self._device_id or "?"
		dtype = self._device_type or "?"
		self._chart_info.textContent = f"{dtype}  ·  {name}  ·  {self._metric_label}"
		self._status_txt.textContent = f"Metric: {self._metric_label}"
		log(f"Metric: {self._metric_label}")
		self._update_start_btn()

		if window.demoActive:
			window._resetLiveChart(self._metric_label)

	# ── Poll loop ────────────────────────────────────────────────────────────

	def _start_loop(self):
		"""
		Register update() as a JS-driven async poll loop.
		Do NOT change this method — the setTimeout pattern is required
		to keep Pyodide stack switching enabled for hardware calls.
		"""
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

	async def update(self, *_args):
		"""
		Per-tick: read the selected hardware property and push to the chart.
		Called every POLL_INTERVAL_MS by the JS loop.
		Must stay async def — required for Pyodide stack switching.
		Hardware reads are synchronous property accesses (no await inside).
		"""
		if not window.demoActive:
			return
		if self._device_name is None or self._metric_key is None:
			return

		# _read_value() is sync (no await); wrap to catch any unexpected Python
		# exception before it bubbles up to JS and shows as "update error".
		try:
			value = self._read_value()
		except Exception as exc:
			log(f"Unexpected read error ({type(exc).__name__}): {exc}", "log-error")
			window.demoActive = False
			self.controls.reset("run")
			return

		if value is None:
			return

		window._pushLiveChartValue(float(value), self._metric_label)

	def _read_value(self):
		"""
		Read the currently selected hardware property synchronously.

		Metric key convention:  "<source>.<property>"
		  SingleMotor:  source = "motor"
		  DoubleMotor:  source = "motor_left" | "motor_right" | "imu"
		  ColorSensor:  source = "sensor"
		  Controller:   source = "sensor"

		Returns a numeric value, or None on error.
		Adding a new property within an existing source needs no code change here —
		the key is parsed and getattr() resolves it automatically.
		"""
		dtype  = self._device_type
		metric = self._metric_key

		# ── Locate the hardware Python object ───────────────────────────────
		# Devices are stored in main_pyodide._panel_devices (keyed by panel_id).
		# self._device_id is that same panel_id; _find_hw_device() looks it up
		# via sys.modules so we never touch the JS window object.
		#
		# Two disconnection scenarios:
		#   (a) Device was explicitly removed → _panel_devices.get() returns None
		#   (b) BLE link dropped but entry lingers → hw.connected is False
		try:
			hw = self._find_hw_device()
			hw_ok = hw is not None and bool(getattr(hw, "connected", True))
		except Exception:
			hw_ok = False

		if not hw_ok:
			log(f"Device '{self._device_name}' disconnected — stopping stream",
				"log-warn")
			window.demoActive = False
			self.controls.reset("run")
			return None

		try:
			parts  = metric.split(".", 1)
			source = parts[0] if len(parts) > 1 else ""
			prop   = parts[1] if len(parts) > 1 else metric

			# ── SingleMotor  →  hw.motor.<prop> ─────────────────────────────
			if dtype == "SingleMotor":
				return getattr(hw.motor, prop)

			# ── DoubleMotor  →  hw.motor[side].<prop>
			#                 or hw.imu_device.<prop> ──────────────────────
			elif dtype == "DoubleMotor":
				le = self._le_module        # cached legoeducation module ref
				if source == "imu":
					return getattr(hw.imu_device, prop)
				if le is None:
					log("legoeducation module (le) not found — cannot read DoubleMotor",
						"log-warn")
					return None
				if source == "motor_left":
					return getattr(hw.motor[le.MOTOR_LEFT], prop)
				if source == "motor_right":
					return getattr(hw.motor[le.MOTOR_RIGHT], prop)

			# ── ColorSensor  →  hw.sensor.<prop> ────────────────────────────
			elif dtype == "ColorSensor":
				return getattr(hw.sensor, prop)

			# ── Controller   →  hw.sensor.<prop> ────────────────────────────
			elif dtype == "Controller":
				return getattr(hw.sensor, prop)

		except Exception as exc:
			log(f"Read error [{dtype}/{metric}]: {exc}", "log-error")
			return None

		log(f"No handler for device type '{dtype}'", "log-warn")
		return None


# =========================================================
# ENTRY POINT — do not modify
# =========================================================

_demo = None   # module-level ref prevents GC of the demo object

def main():
	global _demo
	_demo = LiveDataViewerDemo()

main()