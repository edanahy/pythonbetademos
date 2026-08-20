## posedetection.py - Pose Detection Demo (PyScript / browser)
## =========================================================================
##
## FILE LAYOUT
## ───────────
##   SECTION 1 — EVENT HANDLERS  ←  edit this section for each new demo
##   SECTION 2 — CONFIGURATION   ←  tweak frame size, timing, colors
##   INFRASTRUCTURE              ←  Logger, StateMachine, UI components
##   PoseDetectionDemo           ←  wires all components together
##   main()                      ←  one-line entry point
##
## PORTING GUIDE (for LLMs or humans creating new demos)
## ───────────────────────────────────────────────────────
##   1. Rewrite SECTION 1 to define what each pose state triggers.
##   2. Adjust SECTION 2 constants as needed.
##   3. To replace or extend the right-column panel (e.g. live graph,
##      sensor readout, classifier UI):
##        a. Write a component class with an `.element` (DOM node) and
##           an `.update(data: dict)` method.
##        b. In PoseDetectionDemo._build_layout(), attach your component
##           to `right_col` instead of / alongside `self.bars`.
##        c. In PoseDetectionDemo.check_pose(), feed data to your component
##           instead of (or alongside) `self.bars.update(confidences)`.
##   4. The infrastructure classes below are copy-portable across demos;
##      leave them unchanged unless you need genuinely new behaviour.

from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy


# =========================================================
# SECTION 1 — EVENT HANDLERS   (customize for each demo)
# =========================================================
#
# Each function fires exactly ONCE per rising-edge transition INTO
# that pose state — i.e. when the arm first comes up, not on every
# poll tick while it stays up.  It re-fires only after the state
# leaves and returns (both_up → both_down → both_up fires twice).
#
# Hardware note: add a Double Motor device (named `doublemotor`)
# before activating the Output button, or the motor calls will raise.

def on_left_arm_up():
	log("EVENT: left arm up", "log-info")
	doublemotor.motor_run_for_degrees(
		degrees=360, motor=le.MOTOR_LEFT,
		direction=le.MOVEMENT_TURN_DIRECTION_LEFT)


def on_right_arm_up():
	log("EVENT: right arm up", "log-info")
	doublemotor.motor_run_for_degrees(
		degrees=360, motor=le.MOTOR_RIGHT,
		direction=le.MOVEMENT_TURN_DIRECTION_LEFT)


def on_both_arms_up():
	log("EVENT: both arms up", "log-info")
	doublemotor.motor_run_for_degrees(
		degrees=360, motor=le.MOTOR_LEFT,
		direction=le.MOVEMENT_TURN_DIRECTION_LEFT, blocking=False)
	doublemotor.motor_run_for_degrees(
		degrees=360, motor=le.MOTOR_RIGHT,
		direction=le.MOVEMENT_TURN_DIRECTION_LEFT)


def on_both_arms_down():
	log("EVENT: both arms down", "log-info")
	# no motor action by default — add your own here


# Maps each state label to the handler called on entry.
# Keys must match PoseClassifier.LABELS.
STATE_HANDLERS = {
	"left_up":   on_left_arm_up,
	"right_up":  on_right_arm_up,
	"both_up":   on_both_arms_up,
	"both_down": on_both_arms_down,
}


# =========================================================
# SECTION 2 — CONFIGURATION
# =========================================================

FRAME_W              = 320   # camera canvas width  (px)
FRAME_H              = 240   # camera canvas height (px)
POLL_INTERVAL_MS     = 33    # pose-poll period     (~30 fps)
STATE_DEBOUNCE_TICKS = 2     # consecutive identical votes to accept a transition

BAR_PANEL_WIDTH = 220        # right-column bar chart width (px)
BAR_COLOR       = "#4A90D9"  # bar fill color


# =========================================================
# INFRASTRUCTURE — reusable building blocks
# (normally don't edit below this line for a new demo)
# =========================================================


# ---------------------------------------------------------
# _inject_script() — append a <script> block to document.body
# ---------------------------------------------------------

def _inject_script(js_text: str):
	"""Append a <script> element to document.body."""
	s = document.createElement("script")
	s.text = js_text
	document.body.appendChild(s)


# ---------------------------------------------------------
# Logger — writes to the on-page #log panel
# ---------------------------------------------------------

class Logger:
	"""
	Timestamped writer for the page's #log panel.

	Also injects window.jsLog() into the JS global scope so that
	Python code and any injected JS snippets write to the same panel.

	Usage:  log("message")  or  log("warning", "log-warn")
	CSS classes: "log-info" (default) | "log-warn" | "log-error"
	"""

	def __init__(self):
		# Expose this callable to JS as window.pyLog (rarely needed
		# externally, but useful for custom JS that wants Python-side logging)
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


# Module-level log() — usable from SECTION 1 event handlers.
# _inject_script must be defined above before this line executes.
log = Logger()


# ---------------------------------------------------------
# StateMachine — debounced, edge-triggered state transitions
# ---------------------------------------------------------

class StateMachine:
	"""
	Debounced, edge-triggered finite state machine.

	Call update(candidate) on every poll tick.  A state transition is
	accepted only after `debounce_ticks` consecutive identical candidates;
	`on_enter(new_state)` then fires exactly once per accepted transition.

	Parameters
	----------
	initial_state   : starting state label (str)
	debounce_ticks  : consecutive same-candidate votes required
	on_enter        : callable(new_state: str) — fires on each transition
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
		"""Feed one candidate state; may commit a transition and fire on_enter."""
		if candidate == self._pending:
			self._count += 1
		else:
			self._pending = candidate
			self._count   = 1

		if self._count < self._debounce:
			return

		if candidate != self._current:
			# Commit state BEFORE calling the handler.  If the handler raises
			# (e.g. a Pyodide stack-switch error mid-motor-command), the next
			# poll tick sees the updated state and does NOT re-fire the handler.
			self._current = candidate
			self._on_enter(candidate)


# ---------------------------------------------------------
# PoseClassifier — wrist/shoulder Y-coords → state confidences
# ---------------------------------------------------------

class PoseClassifier:
	"""
	Converts MediaPipe shoulder/wrist Y-coordinates into a probability
	distribution over four arm-pose states.

	All Y values are MediaPipe normalised coords (0 = top of frame,
	1 = bottom).  A positive margin (shoulder_y − wrist_y) means the
	wrist is above the shoulder — i.e. the arm is raised.

	The sigmoid maps margin → [0, 1] confidence, then the four
	exclusive-ish states are composed as products of left/right scores.

	Replace compute() with a trained model later — the rest of the
	pipeline only requires a dict[str → float] that sums to ~1.

	State labels (must match STATE_HANDLERS keys in SECTION 1):
		"left_up", "right_up", "both_up", "both_down"
	"""

	LABELS = ["left_up", "right_up", "both_up", "both_down"]

	@staticmethod
	def compute(lw_y, ls_y, rw_y, rs_y) -> dict:
		"""
		lw_y / ls_y : left  wrist / shoulder Y (normalised, 0–1)
		rw_y / rs_y : right wrist / shoulder Y (normalised, 0–1)
		Returns dict[label → float], values sum to ~1.
		"""
		import math

		def sigmoid(margin, k=12.0):
			return 1.0 / (1.0 + math.exp(-k * margin))

		l = sigmoid(ls_y - lw_y)   # left  arm-up confidence  (0..1)
		r = sigmoid(rs_y - rw_y)   # right arm-up confidence  (0..1)

		raw = {
			"left_up":   l * (1 - r),
			"right_up":  r * (1 - l),
			"both_up":   l * r,
			"both_down": (1 - l) * (1 - r),
		}
		total = sum(raw.values()) or 1.0
		return {k: v / total for k, v in raw.items()}


# ---------------------------------------------------------
# CameraComponent — webcam stream + MediaPipe PoseLandmarker
# ---------------------------------------------------------

class CameraComponent:
	"""
	Manages the webcam stream and MediaPipe PoseLandmarker.

	Creates:
		.video  — hidden <video> (raw stream source; never displayed directly)
		.canvas — visible <canvas> (drawn frame + skeleton overlay)

	Injects window._startCamera() / window._stopCamera() into JS.
	On every detected frame, JS publishes to:
		window.poseData   — dict with leftWristY/leftShoulderY/etc.
		window.poseReady  — True once first landmarks are available

	The "cover crop" technique (one fixed source rectangle computed once
	per stream open, used for BOTH ctx.drawImage and landmark detection)
	guarantees the skeleton overlay and video frame share an identical
	pixel grid — eliminating the overlay-drift bug caused by CSS object-fit
	cropping the display independently of MediaPipe's coordinate space.
	"""

	def __init__(self, frame_w: int, frame_h: int):
		self._w = frame_w
		self._h = frame_h
		self._build_elements()
		self._inject_js()

	@property
	def video(self):
		return self._video

	@property
	def canvas(self):
		return self._canvas

	def start(self):
		"""Start the webcam stream and pose landmarker."""
		window._startCamera()

	def stop(self):
		"""Stop the stream and clear the canvas."""
		window._stopCamera()

	# -- private -----------------------------------------------

	def _build_elements(self):
		# Hidden video: source only; CSS display:none keeps it off-screen.
		self._video             = document.createElement("video")
		self._video.autoplay    = True
		self._video.playsInline = True
		self._video.muted       = True
		self._video.style.display = "none"

		# Visible canvas: fixed pixel size, no CSS scaling/cropping.
		self._canvas              = document.createElement("canvas")
		self._canvas.width        = self._w
		self._canvas.height       = self._h
		self._canvas.style.width  = f"{self._w}px"
		self._canvas.style.height = f"{self._h}px"

		# Publish elements and dimensions to JS
		window.videoElement   = self._video
		window.canvasElement  = self._canvas
		window.frameW         = self._w
		window.frameH         = self._h

		# Initialise shared state flags used by both JS and Python
		window.poseData = {
			"leftWristY": None, "leftShoulderY": None,
			"rightWristY": None, "rightShoulderY": None,
		}
		window.poseReady      = False
		window.cameraActive   = False
		window.trackingActive = False

	def _inject_js(self):
		"""
		Inject __startCamera and __stopCamera into the JS global scope.

		All frame dimensions are read from window.frameW / window.frameH
		(set above) rather than string-interpolated, so this method is a
		plain string — no f-string brace escaping needed.
		"""
		_inject_script("""
window._poseApp = window._poseApp || {};

// ---- _startCamera --------------------------------------------------
window._startCamera = async function() {
  var app = window._poseApp;
  if (app.stream) {
	window.jsLog("startCamera: stream already active", "log-warn");
	return;
  }

  var FRAME_W = window.frameW;
  var FRAME_H = window.frameH;

  var vision = await import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest");
  var PoseLandmarker  = vision.PoseLandmarker;
  var FilesetResolver = vision.FilesetResolver;
  var DrawingUtils    = vision.DrawingUtils;

  var video  = window.videoElement;
  var canvas = window.canvasElement;
  var ctx    = canvas.getContext("2d");

  var stream = await navigator.mediaDevices.getUserMedia({
	video: { width: FRAME_W, height: FRAME_H }, audio: false
  });

  video.srcObject = stream;
  await video.play();
  if (video.readyState < 1) {
	await new Promise(function(r) {
	  video.addEventListener("loadedmetadata", r, { once: true });
	});
  }

  var fileset = await FilesetResolver.forVisionTasks(
	"https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm"
  );
  var landmarker = await PoseLandmarker.createFromOptions(fileset, {
	baseOptions: {
	  modelAssetPath:
		"https://storage.googleapis.com/mediapipe-models/pose_landmarker/" +
		"pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
	},
	runningMode: "VIDEO"
  });
  var drawingUtils = new DrawingUtils(ctx);

  // Compute one fixed "cover" source rectangle.
  // Using the same crop rect for drawImage() and landmark detection means
  // the skeleton and video picture share an identical coordinate space
  // and can never drift apart — no matter the camera's native resolution.
  var vw = video.videoWidth;
  var vh = video.videoHeight;
  var targetAspect = FRAME_W / FRAME_H;
  var srcAspect    = vw / vh;
  var sx, sy, sw, sh;
  if (srcAspect > targetAspect) {
	sh = vh; sw = vh * targetAspect; sx = (vw - sw) / 2; sy = 0;
  } else {
	sw = vw; sh = vw / targetAspect; sx = 0; sy = (vh - sh) / 2;
  }

  app.stream        = stream;
  app.landmarker    = landmarker;
  app.drawingUtils  = drawingUtils;
  app.crop          = { sx: sx, sy: sy, sw: sw, sh: sh };
  app.lastVideoTime = -1;
  app.rafId         = null;


  window.jsLog(
	"Camera " + vw + "x" + vh +
	" → crop " + Math.round(sw) + "x" + Math.round(sh) +
	" @ " + Math.round(sx) + "," + Math.round(sy)
  );
  window.cameraActive = true;
  window.jsLog("Camera started");

  function loop() {
	if (!window.cameraActive) return;   // exit rAF loop when camera stopped

	if (video.currentTime !== app.lastVideoTime) {
	  app.lastVideoTime = video.currentTime;
	  var c = app.crop;

	  // Always render the cropped camera frame — even while tracking is paused —
	  // so the live picture keeps updating without requiring pose detection.
	  ctx.drawImage(video, c.sx, c.sy, c.sw, c.sh, 0, 0, FRAME_W, FRAME_H);

	  if (window.trackingActive) {
		// Detect on the canvas we just drew into, so landmark coordinates
		// are normalised against exactly the same FRAME_W x FRAME_H grid
		// the user sees (not the raw, uncropped camera frame).
		var result = app.landmarker.detectForVideo(canvas, performance.now());
		if (result.landmarks && result.landmarks.length > 0) {
		  var lm = result.landmarks[0];
		  app.drawingUtils.drawLandmarks(lm, { radius: 3 });
		  app.drawingUtils.drawConnectors(lm, PoseLandmarker.POSE_CONNECTIONS);
		  window.poseData = {
			leftShoulderY:  lm[11].y,
			rightShoulderY: lm[12].y,
			leftWristY:     lm[15].y,
			rightWristY:    lm[16].y
		  };
		  window.poseReady = true;
		}
	  }
	}
	app.rafId = requestAnimationFrame(loop);
  }
  loop();
};

// ---- _stopCamera ---------------------------------------------------
window._stopCamera = function() {
  var app = window._poseApp;
  window.cameraActive   = false;
  window.trackingActive = false;
  window.poseReady      = false;
  if (app.rafId)  { cancelAnimationFrame(app.rafId); app.rafId = null; }
  if (app.stream) { app.stream.getTracks().forEach(function(t) { t.stop(); }); app.stream = null; }
  var video  = window.videoElement;
  var canvas = window.canvasElement;
  if (video)  video.srcObject = null;
  if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
  window.jsLog("Camera stopped");
};
""")


# ---------------------------------------------------------
# ToggleButton — a single two-state button
# ---------------------------------------------------------

class ToggleButton:
	"""
	A <button> that alternates between two labels and fires
	on_on / on_off callbacks on each flip.

	Parameters
	----------
	label_on  : label shown when the feature is OFF (clicking turns it ON)
	label_off : label shown when the feature is ON  (clicking turns it OFF)
	on_on     : called when flipping OFF → ON
	on_off    : called when flipping ON  → OFF
	guard_on  : optional callable() → bool.  Only checked on the OFF → ON
				transition.  Return False to suppress the toggle and the
				callback (the button stays in its OFF state).  The ON → OFF
				direction is never guarded.
	"""

	def __init__(self, label_on: str, label_off: str,
				 on_on, on_off, guard_on=None):
		self._label_on  = label_on
		self._label_off = label_off
		self._active    = False

		self.element = document.createElement("button")
		self.element.setAttribute("type", "button")   # prevent accidental form submit
		self.element.textContent   = label_on
		self.element.style.padding = "6px 12px"

		def _click(event):
			if not self._active:
				# --- Turning ON ---
				if guard_on is not None and not guard_on():
					return   # blocked; button stays in OFF state
				self._active = True
				self.element.textContent = label_off
				on_on()
			else:
				# --- Turning OFF (never guarded) ---
				self._active = False
				self.element.textContent = label_on
				on_off()

		self.element.addEventListener("click", create_proxy(_click))

	def reset(self):
		"""Force the button back to its inactive (OFF) state. No callbacks fired."""
		self._active = False
		self.element.textContent = self._label_on


# ---------------------------------------------------------
# ControlsRow — horizontal strip of ToggleButtons
# ---------------------------------------------------------

class ControlsRow:
	"""
	Horizontal flex row of ToggleButton instances, keyed by name.

	Usage
	-----
	row = ControlsRow()
	row.add("camera",   "Start Camera",   "Stop Camera",   on_on=..., on_off=...)
	row.add("tracking", "Start Tracking", "Stop Tracking", on_on=..., on_off=...,
			guard_on=lambda: bool(window.cameraActive))
	parent_el.appendChild(row.element)
	row.reset("tracking")   # e.g. when camera is stopped
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
		"""Add a button and return it (keep the reference to call .reset() individually)."""
		btn = ToggleButton(label_on, label_off, on_on, on_off, guard_on)
		self._buttons[key] = btn
		self.element.appendChild(btn.element)
		return btn

	def reset(self, key: str):
		"""Reset the named button to its inactive state (no callbacks fired)."""
		if key in self._buttons:
			self._buttons[key].reset()


# ---------------------------------------------------------
# BarChartPanel — vertical stack of labelled progress bars
# ---------------------------------------------------------

class BarChartPanel:
	"""
	A column of horizontal progress bars, one per tracked quantity.

	Usage
	-----
	bars = BarChartPanel(width_px=220, default_color="#4A90D9")
	bars.add("left_up",   "Left Up")
	bars.add("right_up",  "Right Up")
	bars.add("both_up",   "Both Up")
	bars.add("both_down", "Both Down")
	parent_el.appendChild(bars.element)

	# On each pose update:
	bars.update({"left_up": 0.82, "right_up": 0.05, "both_up": 0.04, "both_down": 0.09})
	"""

	def __init__(self, width_px: int = 220,
				 default_color: str = "#4A90D9",
				 font_size: str = "12px"):
		self.element = document.createElement("div")
		self.element.style.width      = f"{width_px}px"
		self.element.style.fontFamily = "sans-serif"
		self.element.style.fontSize   = font_size
		self._fills         = {}
		self._default_color = default_color

	def add(self, key: str, label: str, color: str = None) -> "BarChartPanel":
		"""Add a bar row.  Returns self for method chaining."""
		color = color or self._default_color

		row = document.createElement("div")
		row.style.marginBottom = "10px"

		lbl = document.createElement("div")
		lbl.textContent        = label
		lbl.style.marginBottom = "2px"

		track = document.createElement("div")
		track.style.width        = "100%"
		track.style.height       = "14px"
		track.style.background   = "#eee"
		track.style.borderRadius = "4px"
		track.style.overflow     = "hidden"

		fill = document.createElement("div")
		fill.style.height     = "100%"
		fill.style.width      = "0%"
		fill.style.background = color
		fill.style.transition = "width 0.08s linear"

		track.appendChild(fill)
		row.appendChild(lbl)
		row.appendChild(track)
		self.element.appendChild(row)
		self._fills[key] = fill
		return self

	def update(self, values: dict):
		"""
		Refresh bar widths from dict[key → float in 0..1].
		Keys absent from `values` are left unchanged.
		"""
		for key, fill in self._fills.items():
			pct = max(0.0, min(1.0, values.get(key, 0.0))) * 100
			fill.style.width = f"{pct:.1f}%"


# =========================================================
# PoseDetectionDemo — main demo orchestrator
# =========================================================

class PoseDetectionDemo:
	"""
	Wires together CameraComponent, ControlsRow, BarChartPanel,
	PoseClassifier, and StateMachine to produce the pose-detection demo.

	Page layout
	───────────
	Left column  : camera viewport (video/canvas) + control buttons
	Right column : confidence bar chart

	Control flow
	────────────
	1. User presses "Start Camera"  → CameraComponent starts the webcam + rAF loop.
	2. User presses "Start Tracking" → JS rAF loop begins calling MediaPipe and
	   publishing window.poseData / window.poseReady.
	3. JS poll loop calls async check_pose() every POLL_INTERVAL_MS.
	4. check_pose() reads poseData, runs PoseClassifier, updates bars, feeds
	   StateMachine.  When the machine accepts a transition, _dispatch_state_event()
	   fires the matching handler from STATE_HANDLERS.
	5. "Activate Output" gates hardware calls; handlers log but do nothing without it.
	"""

	def __init__(self, state_handlers: dict,
				 frame_w: int   = 320,
				 frame_h: int   = 240,
				 poll_ms: int   = 33,
				 debounce: int  = 2,
				 bar_color: str = "#4A90D9",
				 bar_width: int = 220):

		self._handlers      = state_handlers
		self._output_active = False

		# ── Components ────────────────────────────────────────
		self.camera   = CameraComponent(frame_w, frame_h)
		self.controls = ControlsRow()
		self.bars     = BarChartPanel(width_px=bar_width, default_color=bar_color)

		for key, label in [
			("left_up",   "Left Up"),
			("right_up",  "Right Up"),
			("both_up",   "Both Up"),
			("both_down", "Both Down"),
		]:
			self.bars.add(key, label)

		# ── State machine ─────────────────────────────────────
		self.sm = StateMachine(
			initial_state  = "both_down",
			debounce_ticks = debounce,
			on_enter       = self._dispatch_state_event,
		)

		# ── Assemble ──────────────────────────────────────────
		self._build_layout(frame_w, frame_h)
		self._setup_controls()
		self._start_poll_loop(poll_ms)

		log("Pose detection demo initialised")

	# -- layout ------------------------------------------------

	def _build_layout(self, frame_w: int, frame_h: int):
		"""
		Insert the two-column panel into the page next to #device-panel.
		Override this method to restructure the overall page layout.
		"""
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		# Section header
		header       = document.createElement("div")
		header.className = "panel-header"
		h2           = document.createElement("h2")
		h2.textContent = "Video"
		header.appendChild(h2)

		# Two-column flex container
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

		# Fixed-size camera viewport (black background container)
		viewport = document.createElement("div")
		viewport.style.position       = "relative"
		viewport.style.width          = f"{frame_w}px"
		viewport.style.height         = f"{frame_h}px"
		viewport.style.margin         = "0 auto"
		viewport.style.display        = "flex"
		viewport.style.justifyContent = "center"
		viewport.style.alignItems     = "center"
		viewport.style.background     = "#000"
		viewport.style.overflow       = "hidden"

		viewport.appendChild(self.camera.video)
		viewport.appendChild(self.camera.canvas)

		left_col.appendChild(viewport)
		left_col.appendChild(self.controls.element)

		# Right column: confidence bars (replace/extend this for new demos)
		right_col.appendChild(self.bars.element)

		columns.appendChild(left_col)
		columns.appendChild(right_col)

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(columns, header.nextSibling)

	# -- controls ----------------------------------------------

	def _setup_controls(self):
		"""
		Wire up the three toggle buttons: Camera / Tracking / Output.
		Add more buttons here for demo-specific controls.
		"""

		# Camera ──────────────────────────────────────────────
		def _cam_on():
			log("User clicked Start Camera")
			self.camera.start()

		def _cam_off():
			log("User clicked Stop Camera")
			self.camera.stop()
			# Stopping the camera implicitly resets tracking; mirror that in the UI.
			self.controls.reset("tracking")

		self.controls.add(
			"camera", "Start Camera", "Stop Camera",
			on_on=_cam_on, on_off=_cam_off)

		# Tracking ────────────────────────────────────────────
		def _tracking_guard() -> bool:
			"""Prevent starting tracking when camera isn't running."""
			if not window.cameraActive:
				log("Start Tracking: camera not active — ignoring", "log-warn")
				return False
			return True

		def _tracking_on():
			log("User clicked Start Tracking")
			window.trackingActive = True

		def _tracking_off():
			log("User clicked Stop Tracking")
			window.trackingActive = False

		self.controls.add(
			"tracking", "Start Tracking", "Stop Tracking",
			on_on=_tracking_on, on_off=_tracking_off,
			guard_on=_tracking_guard)

		# Output ──────────────────────────────────────────────
		def _output_on():
			self._output_active = True
			log("Motor output ACTIVATED", "log-warn")

		def _output_off():
			self._output_active = False
			log("Motor output deactivated")

		self.controls.add(
			"output", "Activate Output", "Deactivate Output",
			on_on=_output_on, on_off=_output_off)

	# -- poll loop ---------------------------------------------

	def _start_poll_loop(self, poll_ms: int):
		"""
		Register check_pose as an async JS-driven poll loop.

		WHY NOT setInterval:
		setInterval invokes callbacks synchronously (non-promising), which
		disables Pyodide stack switching.  Without stack switching, any
		run_sync()-based hardware call after the first one inside a handler
		raises "Cannot stack switch because the Python entrypoint was a
		synchronous function".  Directly awaiting an *async* Python function
		from JS satisfies Pyodide's condition #2 for enabling stack switching,
		keeping every sequential motor command inside a valid context.

		WHY self-rescheduling setTimeout (not a fixed clock):
		The next poll fires only AFTER check_pose resolves, so a slow hardware
		command pushes the next tick back rather than overlapping it — preventing
		two polls from both issuing a motor command simultaneously.
		"""
		window._checkPose     = create_proxy(self.check_pose)
		window.pollIntervalMs = poll_ms

		_inject_script("""
(function() {
  // Increment the generation counter so any previous poll loop (e.g. from
  // a re-executed script) detects the new generation and exits quietly.
  window._posePollGen = (window._posePollGen || 0) + 1;
  var myGen = window._posePollGen;
  var ms    = window.pollIntervalMs;

  async function pollLoop() {
	if (myGen !== window._posePollGen) return;   // superseded; die quietly
	try { await window._checkPose(); }
	catch (e) { window.jsLog("check_pose error: " + e, "log-error"); }
	setTimeout(pollLoop, ms);
  }
  pollLoop();
})();
""")

	# -- state dispatch ----------------------------------------

	def _dispatch_state_event(self, new_state: str):
		"""
		Called by StateMachine exactly once per accepted transition.
		Looks up the handler in STATE_HANDLERS and calls it, guarded by
		the output-active flag set by the "Activate Output" button.
		"""
		if not self._output_active:
			log(f"state → {new_state} (output not activated)", "log-warn")
			return

		handler = self._handlers.get(new_state)
		if handler is None:
			log(f"state → {new_state} (no handler registered)", "log-warn")
			return

		log(f"state → {new_state} (calling {handler.__name__})", "log-info")
		try:
			handler()
		except Exception as exc:
			log(f"ERROR in {handler.__name__}: {exc}", "log-error")

	# -- async poll callback -----------------------------------

	async def check_pose(self, *_args):
		"""
		Async pose-check callback — called every POLL_INTERVAL_MS by the JS loop.

		MUST REMAIN async def.
		Pyodide enables stack switching (required for run_sync() inside hardware
		handlers) when an async Python function is called directly from JS.
		Converting this to a plain synchronous function breaks all but the first
		hardware call inside any handler that calls motor_run_for_degrees.

		Latency note: because the next poll fires AFTER this call resolves (not
		on a fixed clock), a blocking hardware command pushes back the next tick
		rather than overlapping it — intentionally preventing concurrent motor
		commands from two overlapping polls.
		"""
		if not window.cameraActive or not window.trackingActive:
			return
		if not window.poseReady:
			return

		pose_js = window.poseData
		if not pose_js:
			return

		pose = pose_js.to_py()
		lw   = pose.get("leftWristY")
		ls   = pose.get("leftShoulderY")
		rw   = pose.get("rightWristY")
		rs   = pose.get("rightShoulderY")
		if None in (lw, ls, rw, rs):
			return

		confidences = PoseClassifier.compute(lw, ls, rw, rs)
		self.bars.update(confidences)
		self.sm.update(max(confidences, key=confidences.get))


# =========================================================
# ENTRY POINT
# =========================================================

# Module-level reference keeps the demo object alive for the session.
# (window._checkPose holds a proxy to self.check_pose, which keeps
# self reachable, but an explicit reference is good practice.)
_demo = None


def main():
	global _demo
	_demo = PoseDetectionDemo(
		state_handlers       = STATE_HANDLERS,
		frame_w              = FRAME_W,
		frame_h              = FRAME_H,
		poll_ms              = POLL_INTERVAL_MS,
		debounce             = STATE_DEBOUNCE_TICKS,
		bar_color            = BAR_COLOR,
		bar_width            = BAR_PANEL_WIDTH,
	)


main()
