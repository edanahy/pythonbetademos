## template.py — Demo Template (PyScript / browser)
## ===================================================
##
## HOW TO USE THIS FILE
## ─────────────────────
## 1. Read INSTRUCTIONS.md first — it explains every constraint.
## 2. Edit SECTION 1: write your callbacks / event handlers.
## 3. Edit SECTION 2: adjust configuration constants.
## 4. Rename `MyDemo` and fill in the four TODO blocks inside it:
##      _build_layout()  — what goes in the left column display
##      _setup_controls() — which buttons to add
##      update()          — the async per-tick logic
## 5. Keep all INFRASTRUCTURE code exactly as written. Do not modify it.
##
## ⚠ CRITICAL: Never write `window.__name` inside a class method.
##   Python silently renames `__name` to `_ClassName__name` (name
##   mangling), so the JS attribute is never found.  Use `window._name`
##   (single underscore) for any JS globals you define and call.

from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy


# =========================================================
# SECTION 1 — CALLBACKS / EVENT HANDLERS   (edit this)
# =========================================================
# Put the Python functions that respond to events here.
# These are called from your demo class at the right moment.
# They can: call log(), issue LEGO hardware commands, update
# UI elements — whatever the demo needs.
#
# `log` is always available here at call time (defined below).
# Hardware globals (doublemotor, le, color_sensor, etc.) are
# provided by the page; use them directly.

def on_event_a():
	"""TODO: replace with your first event/state handler."""
	log("EVENT A triggered")
	# Example hardware call:
	# doublemotor.motor_run_for_degrees(
	#     degrees=360, motor=le.MOTOR_LEFT,
	#     direction=le.MOVEMENT_TURN_DIRECTION_LEFT)


def on_event_b():
	"""TODO: replace with your second event/state handler."""
	log("EVENT B triggered")


# If your demo uses a StateMachine, map state labels → handlers here.
# Keys must match whatever labels your classifier / logic produces.
STATE_HANDLERS = {
	"event_a": on_event_a,
	"event_b": on_event_b,
	# "idle": on_idle,     # add as many states as needed
}


# =========================================================
# SECTION 2 — CONFIGURATION   (edit this)
# =========================================================

# --- Layout -------------------------------------------------
FRAME_W          = 320   # left-column display width  (px)
FRAME_H          = 240   # left-column display height (px)

# --- Timing -------------------------------------------------
POLL_INTERVAL_MS = 100   # async update() period (ms); 33 ≈ 30 fps, 100 ≈ 10 fps

# --- Appearance ---------------------------------------------
PANEL_BG         = "#1a1a2e"   # left display panel background color
RIGHT_COL_WIDTH  = 260          # right column width (px)
BAR_COLOR        = "#4A90D9"   # default bar fill color


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
# YOUR DEMO CLASS   (rename and fill in the TODO sections)
# =========================================================

class MyDemo:
	"""
	TODO: Rename this class to describe your demo (e.g. SensorGraphDemo).

	The four extension points are:
	  _build_layout()   — what elements go in the left display panel
	  _setup_controls() — which toggle buttons to add
	  update()          — async per-tick: read hardware, update UI
	  SECTION 1 above   — what happens on each event / state transition
	"""

	def __init__(self):

		# ── Optional components — uncomment what you need ─────
		# self.camera = CameraComponent(FRAME_W, FRAME_H)
		# self.sm = StateMachine("idle", 2, self._on_state_enter)

		# ── Controls (always keep) ────────────────────────────
		self.controls = ControlsRow()

		# ── Right-column panel ────────────────────────────────
		# TODO: Keep BarChartPanel or replace with your own class.
		# Your panel class needs: .element (DOM node) + .update(dict)
		self.right_panel = BarChartPanel(width_px=RIGHT_COL_WIDTH,
										 default_color=BAR_COLOR)
		# TODO: add bars for each signal you want to display, e.g.:
		# self.right_panel.add("motor_pos", "Motor Position")
		# self.right_panel.add("sensor",    "Color Sensor")

		# ── Assemble ──────────────────────────────────────────
		self._build_layout()
		self._setup_controls()
		self._start_loop()

		log("MyDemo initialised")    # TODO: update message

	# -- Layout -----------------------------------------------

	def _build_layout(self):
		"""
		Inject the two-column panel next to #device-panel.

		Left column:  your main display area (canvas, readouts, etc.)
		Right column: self.right_panel

		TODO: Replace the placeholder left_panel div with your actual
		display widget.  Examples:
		  • Webcam + skeleton overlay:
			  viewport = document.createElement("div")
			  viewport.style.cssText = "position:relative;width:{W}px;height:{H}px;background:#000;overflow:hidden;"
			  viewport.appendChild(self.camera.video)
			  viewport.appendChild(self.camera.canvas)
		  • A <canvas> you draw on from Python via JS:
			  canvas = document.createElement("canvas")
			  canvas.width = FRAME_W; canvas.height = FRAME_H
			  window.myCanvas = canvas   # expose to JS for drawing
		  • A styled readout div with sensor values.
		"""
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		header       = document.createElement("div")
		header.className = "panel-header"
		h2 = document.createElement("h2")
		h2.textContent = "TODO: Demo Title"     # ← change this
		header.appendChild(h2)

		columns = document.createElement("div")
		columns.className            = "columns-panel"
		columns.style.display        = "flex"
		columns.style.flexDirection  = "row"
		columns.style.gap            = "16px"
		columns.style.alignItems     = "flex-start"
		columns.style.justifyContent = "center"

		left_col = document.createElement("div")
		left_col.style.display       = "flex"
		left_col.style.flexDirection = "column"
		left_col.style.alignItems    = "center"

		right_col = document.createElement("div")
		right_col.style.display       = "flex"
		right_col.style.flexDirection = "column"

		# TODO: Replace this placeholder with your real left-panel content.
		left_panel = document.createElement("div")
		left_panel.style.cssText = (
			f"width:{FRAME_W}px;height:{FRAME_H}px;"
			f"background:{PANEL_BG};display:flex;"
			"align-items:center;justify-content:center;overflow:hidden;"
		)
		note = document.createElement("p")
		note.textContent = "TODO: replace with display widget"
		note.style.color = "#888"
		left_panel.appendChild(note)

		left_col.appendChild(left_panel)
		left_col.appendChild(self.controls.element)
		right_col.appendChild(self.right_panel.element)
		columns.appendChild(left_col)
		columns.appendChild(right_col)

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(columns, header.nextSibling)

	# -- Controls ---------------------------------------------

	def _setup_controls(self):
		"""
		TODO: Add toggle buttons for your demo's actions.

		Pattern:
			def _on():  ...  # what happens when user turns feature ON
			def _off(): ...  # what happens when user turns feature OFF
			self.controls.add("key", "Start X", "Stop X",
							  on_on=_on, on_off=_off)

		Optional guard — only blocks the OFF→ON transition:
			self.controls.add("key", ..., guard_on=lambda: bool(window.cameraActive))

		If stopping one feature should reset another button:
			def _cam_off():
				self.camera.stop()
				self.controls.reset("tracking")   # reset tracking button too
		"""
		window.demoActive = False

		def _start():
			window.demoActive = True
			log("Demo started")

		def _stop():
			window.demoActive = False
			log("Demo stopped")

		self.controls.add("run", "Start Demo", "Stop Demo",
						  on_on=_start, on_off=_stop)

	# -- Update loop ------------------------------------------

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
		TODO: Your per-tick logic goes here.
		Called every POLL_INTERVAL_MS by the JS loop.

		MUST remain `async def` — this enables Pyodide stack switching,
		which is required for hardware calls (motor commands, sensor reads)
		that use run_sync() internally.  Making it a plain def breaks all
		hardware calls after the first one.

		Common patterns:
		  Guard on active flag:   if not window.demoActive: return
		  Read a motor position:  pos = await doublemotor.get_position(le.MOTOR_LEFT)
		  Read a color sensor:    val = await color_sensor.get_reflected_light()
		  Update a bar:           self.right_panel.update({"key": val / 100.0})
		  Drive a state machine:  self.sm.update(self._classify(val))
		  Trigger an event:       on_event_a()
		"""
		if not window.demoActive:
			return

		# TODO: implement your per-tick logic here.
		pass

	# -- Optional: state machine callback ---------------------

	def _on_state_enter(self, new_state: str):
		"""
		Called by StateMachine once per accepted transition.
		Dispatches to STATE_HANDLERS.  Include only if using StateMachine.
		"""
		handler = STATE_HANDLERS.get(new_state)
		if handler is None:
			log(f"state → {new_state} (no handler)", "log-warn")
			return
		log(f"state → {new_state}", "log-info")
		try:
			handler()
		except Exception as exc:
			log(f"ERROR in handler for {new_state}: {exc}", "log-error")


# =========================================================
# ENTRY POINT — do not modify
# =========================================================

_demo = None   # module-level ref prevents GC of the demo object

def main():
	global _demo
	_demo = MyDemo()

main()