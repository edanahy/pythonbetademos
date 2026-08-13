## pong.py — LEGO Double Motor Pong  (PyScript / browser)
## ==============================================================
##
## Left  paddle ← MOTOR_LEFT  speed  (twist the left  barrel)
## Right paddle ← MOTOR_RIGHT speed  (twist the right barrel)
##
## Python polls motor speeds at ~30 fps and feeds them into a
## self-contained JavaScript Pong engine running at 60 fps via
## requestAnimationFrame.  The canvas takes over the full panel
## space; an expand button (⛶) overlays the top-right corner and
## promotes the game to a full-screen modal overlay.
##
## Built from template.py + INSTRUCTIONS.md
## ─────────────────────────────────────────────────────────────
##  Rules observed:
##  • All JS globals: _singleUnderscore (no __ mangling)
##  • update() is async def
##  • setTimeout poll pattern (not setInterval)
##  • _demo at module level (prevents GC)
##  • _inject_script defined before Logger
##  • Hardware via sys.modules, not window.*
##  • No JsProxy [] subscript on DOM collections
##  • No third-party imports

from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy
import sys


# =========================================================
# SECTION 1 — CALLBACKS / EVENT HANDLERS
# =========================================================
# Pong has no discrete Python-side state transitions;
# the game FSM (serving, playing, win) lives entirely in JS.

STATE_HANDLERS = {}


# =========================================================
# SECTION 2 — CONFIGURATION
# =========================================================

# --- Canvas size  ------------------------------------------
FRAME_W          = 640   # Pong canvas width  (px)
FRAME_H          = 380   # Pong canvas height (px)

# --- Timing ------------------------------------------------
POLL_INTERVAL_MS = 33    # Python update() period ≈ 30 fps

# --- Unused layout defaults (kept for infrastructure compat) -
PANEL_BG         = "#000000"
RIGHT_COL_WIDTH  = 260
BAR_COLOR        = "#4A90D9"

# --- Pong feel ---------------------------------------------
# Motor speed is a signed percentage (−100 … +100).
# SPEED_SCALE converts it to paddle px/game-frame (≈60 fps).
# At 60 fps: 100 % → 100 × 0.11 = 11 px/frame → ~660 px/s peak.
SPEED_SCALE      = 0.11   # increase to make paddles more sensitive
PADDLE_H         = 80
PADDLE_W         = 14
BALL_RADIUS      = 7
BALL_SPEED_INIT  = 3      # px / JS frame at serve (grows 6% per rally hit)
WIN_SCORE        = 7      # first to this score wins
# Paddle direction — set to −1 to invert a side (e.g. if motor reads backwards)
INVERT_LEFT      = 1      # 1 = normal,  −1 = inverted
INVERT_RIGHT     = -1     # −1 = inverted by default (right barrel faces opposite)


# =========================================================
# INFRASTRUCTURE — verbatim from template; do not modify
# =========================================================

# ---------------------------------------------------------
# _inject_script — must be defined before Logger
# ---------------------------------------------------------

def _inject_script(js_text: str):
	s = document.createElement("script")
	s.text = js_text
	document.body.appendChild(s)


# ---------------------------------------------------------
# Logger
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


log = Logger()


# ---------------------------------------------------------
# StateMachine — kept verbatim; unused in this demo
# ---------------------------------------------------------

class StateMachine:
	"""
	update(candidate) each tick; on_enter(state) fires once per
	accepted transition after `debounce_ticks` consecutive votes.
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
			self._current = candidate
			self._on_enter(candidate)


# ---------------------------------------------------------
# ToggleButton
# ---------------------------------------------------------

class ToggleButton:
	"""
	Alternates between two labels; fires on_on / on_off on each flip.
	guard_on (optional callable → bool): only checked on OFF → ON.
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
		self._active = False
		self.element.textContent = self._label_on


# ---------------------------------------------------------
# ControlsRow
# ---------------------------------------------------------

class ControlsRow:
	"""
	row = ControlsRow()
	row.add("key", "Label On", "Label Off", on_on=fn, on_off=fn)
	parent_el.appendChild(row.element)
	row.reset("key")
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
# BarChartPanel — kept verbatim; unused in this demo
# ---------------------------------------------------------

class BarChartPanel:
	"""
	bars = BarChartPanel(width_px=260, default_color="#4A90D9")
	bars.add("key", "Label").update({"key": 0.75})
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


# =========================================================
# PONG DEMO
# =========================================================

class PongDemo:
	"""
	LEGO Double Motor Pong.

	Architecture
	─────────────
	• _build_layout()        — full-width canvas + expand-to-modal overlay btn
	• _setup_controls()      — ▶ Start / ■ Stop toggle
	• _inject_pong_constants — push Python config to window._PONG object
	• _inject_pong_engine    — self-contained JS game engine (RAF loop)
	• _start_loop()          — Python setTimeout poll (per-template pattern)
	• update()               — reads motor speeds, calls window._setPaddleSpeeds
	"""

	def __init__(self):
		# Cache _panel_devices and le from main_pyodide once at startup.
		# Re-checked lazily in update() if still None.
		self._panel_devices_ref = None
		self._le_module         = None
		self._refresh_module_refs()

		self.controls = ControlsRow()

		self._build_layout()
		self._setup_controls()
		self._inject_pong_constants()
		self._inject_pong_engine()
		self._start_loop()

		log("Pong Demo ready — connect DoubleMotor, then press ▶ Start Pong")

	# ------------------------------------------------------------------
	# Module / hardware helpers
	# ------------------------------------------------------------------

	def _refresh_module_refs(self):
		for mod_name in ("main_pyodide", "__main__"):
			mod = sys.modules.get(mod_name)
			if mod is not None:
				d = getattr(mod, "_panel_devices", None)
				if isinstance(d, dict):
					self._panel_devices_ref = d
					self._le_module = getattr(mod, "le", None)
					return

	def _find_hw_device(self):
		"""
		Return the first DoubleMotor device from _panel_devices,
		or the first device of any type as a fallback.
		"""
		if self._panel_devices_ref is None:
			return None
		# Prefer DoubleMotor by class name
		for dev in self._panel_devices_ref.values():
			if "Double" in type(dev).__name__:
				return dev
		# Fallback: first available device
		if self._panel_devices_ref:
			return next(iter(self._panel_devices_ref.values()))
		return None

	# ------------------------------------------------------------------
	# Layout — full-width canvas, no right column
	# ------------------------------------------------------------------

	def _build_layout(self):
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		# Header
		header = document.createElement("div")
		header.className = "panel-header"
		h2 = document.createElement("h2")
		h2.textContent = "LEGO Pong"
		header.appendChild(h2)

		# Outer wrapper (column-direction, centred)
		wrapper = document.createElement("div")
		wrapper.className           = "columns-panel"
		wrapper.style.display       = "flex"
		wrapper.style.flexDirection = "column"
		wrapper.style.alignItems    = "center"
		wrapper.style.gap           = "0"

		# Canvas container — position:relative so we can overlay the expand btn
		canvas_container = document.createElement("div")
		canvas_container.style.cssText = (
			"position:relative;display:inline-block;line-height:0;"
		)

		# Pong canvas
		canvas = document.createElement("canvas")
		canvas.id     = "pongCanvas"
		canvas.width  = FRAME_W
		canvas.height = FRAME_H
		canvas.style.cssText = (
			f"width:{FRAME_W}px;height:{FRAME_H}px;"
			"display:block;background:#000;"
			"border:2px solid rgba(255,255,255,0.2);"
			"border-radius:4px;"
		)
		window.pongCanvas = canvas
		canvas_container.appendChild(canvas)

		# Expand button — top-right corner of canvas
		expand_btn = document.createElement("button")
		expand_btn.id = "pongExpandBtn"
		expand_btn.setAttribute("type", "button")
		expand_btn.title = "Expand to full screen"
		expand_btn.style.cssText = (
			"position:absolute;top:8px;right:8px;"
			"background:rgba(255,255,255,0.10);"
			"border:1px solid rgba(255,255,255,0.30);"
			"color:#fff;font-size:15px;width:28px;height:28px;"
			"border-radius:4px;cursor:pointer;line-height:1;"
			"display:flex;align-items:center;justify-content:center;"
			"padding:0;transition:background 0.15s;"
		)
		# ⛶ SQUARE FOUR CORNERS (U+26F6)
		expand_btn.textContent = "⛶"

		def _on_expand(evt):
			window._pongToggleModal()
		expand_btn.addEventListener("click", create_proxy(_on_expand))
		canvas_container.appendChild(expand_btn)

		wrapper.appendChild(canvas_container)
		wrapper.appendChild(self.controls.element)

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(wrapper, header.nextSibling)

	# ------------------------------------------------------------------
	# Controls
	# ------------------------------------------------------------------

	def _setup_controls(self):
		window.demoActive = False

		def _start():
			window.demoActive = True
			window._pongStart()
			log("Game on — twist DoubleMotor to move paddles")

		def _stop():
			window.demoActive = False
			window._pongStop()
			log("Game paused")

		self.controls.add(
			"run",
			"▶ Start Pong",   # ▶ Start Pong
			"■ Stop Pong",    # ■ Stop Pong
			on_on=_start,
			on_off=_stop,
		)

	# ------------------------------------------------------------------
	# JavaScript — inject constants then the engine (two separate tags
	# so the engine body can be a plain string with no f-string brace
	# escaping required)
	# ------------------------------------------------------------------

	def _inject_pong_constants(self):
		"""Push Python config values to a single window._PONG object."""
		_inject_script(
			"window._PONG = {"
			f" CW:{FRAME_W}, CH:{FRAME_H},"
			f" PW:{PADDLE_W}, PH:{PADDLE_H},"
			f" BR:{BALL_RADIUS},"
			f" INIT_SPD:{BALL_SPEED_INIT},"
			f" WIN:{WIN_SCORE}"
			" };"
		)

	def _inject_pong_engine(self):
		"""
		Self-contained Pong engine.  All game logic and rendering runs
		in JavaScript at RAF speed (~60 fps).  Python only supplies the
		paddle velocities each ~30 ms tick.

		Public JS API (all _singleUnderscore — no Python name mangling):
		  window._setPaddleSpeeds(lvY, rvY)  ← Python calls each tick
		  window._pongStart()                 ← reset + begin RAF loop
		  window._pongStop()                  ← halt RAF loop
		  window._pongToggleModal()           ← expand / collapse modal
		"""
		_inject_script("""
(function () {
  "use strict";

  var P  = window._PONG;
  var CW = P.CW, CH = P.CH;
  var PW = P.PW, PH = P.PH;
  var BR = P.BR;
  var PAD_M = 20;   // paddle margin from edge

  // ── Game state ────────────────────────────────────────────────────
  var ball   = { x: CW / 2, y: CH / 2, vx: P.INIT_SPD, vy: P.INIT_SPD * 0.7 };
  var leftY  = CH / 2 - PH / 2;
  var rightY = CH / 2 - PH / 2;
  var leftVY = 0, rightVY = 0;   // set by Python each tick
  var scoreL = 0, scoreR = 0;
  var gameRunning  = false;
  var rafId        = null;
  var flashTimer   = 0;
  var flashSide    = null;        // "left" | "right"

  // ── Modal state ───────────────────────────────────────────────────
  var isModal      = false;
  var modalOverlay = null;

  // ── Public: paddle velocities (called from Python update()) ───────
  window._setPaddleSpeeds = function (lvY, rvY) {
	leftVY  = lvY;
	rightVY = rvY;
  };

  // ── Public: start / stop ──────────────────────────────────────────
  window._pongStart = function () {
	scoreL = 0; scoreR = 0;
	leftY  = CH / 2 - PH / 2;
	rightY = CH / 2 - PH / 2;
	resetBall(1);
	gameRunning = true;
	if (rafId) cancelAnimationFrame(rafId);
	rafId = requestAnimationFrame(gameLoop);
  };

  window._pongStop = function () {
	gameRunning = false;
	if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
	drawSplash("PAUSED", null, null, "Press ▶ Start Pong to continue");
  };

  // ── Public: expand / collapse modal ──────────────────────────────
  window._pongToggleModal = function () {
	var canvas = document.getElementById("pongCanvas");
	if (!canvas) return;

	if (!isModal) {
	  // Build black full-screen overlay.
	  // IMPORTANT: give it an id so the page's MutationObserver
	  // (which removes id-less elements appended to body) leaves it alone.
	  modalOverlay = document.createElement("div");
	  modalOverlay.id = "pongModal";
	  modalOverlay.style.cssText = [
		"position:fixed;top:0;left:0;width:100vw;height:100vh;",
		"background:#000;display:flex;flex-direction:column;",
		"align-items:center;justify-content:center;z-index:9999;"
	  ].join("");

	  var closeBtn = document.createElement("button");
	  closeBtn.textContent = "✕  Close";
	  closeBtn.style.cssText = [
		"position:absolute;top:14px;right:18px;",
		"background:rgba(255,255,255,0.10);",
		"border:1px solid rgba(255,255,255,0.35);",
		"color:#fff;font-size:14px;padding:5px 14px;",
		"border-radius:4px;cursor:pointer;"
	  ].join("");
	  closeBtn.addEventListener("click", function () {
		window._pongToggleModal();
	  });
	  modalOverlay.appendChild(closeBtn);

	  // Scale canvas to fill available viewport
	  var vw    = window.innerWidth  - 48;
	  var vh    = window.innerHeight - 96;
	  var scale = Math.min(vw / CW, vh / CH);
	  var dw    = Math.round(CW * scale);
	  var dh    = Math.round(CH * scale);

	  // Remember where the canvas lived so we can put it back
	  canvas._origParent  = canvas.parentNode;
	  canvas._origNextSib = canvas.nextSibling;
	  canvas.style.width  = dw + "px";
	  canvas.style.height = dh + "px";

	  modalOverlay.appendChild(canvas);
	  document.body.appendChild(modalOverlay);
	  isModal = true;
	  window.jsLog("Expanded — press ✕ Close to return to panel");
	} else {
	  // Restore canvas to its original DOM position
	  canvas.style.width  = CW + "px";
	  canvas.style.height = CH + "px";
	  var ns = canvas._origNextSib;
	  var op = canvas._origParent;
	  if (op) {
		if (ns && ns.parentNode === op) {
		  op.insertBefore(canvas, ns);
		} else {
		  op.appendChild(canvas);
		}
	  }
	  if (modalOverlay && modalOverlay.parentNode) {
		modalOverlay.parentNode.removeChild(modalOverlay);
	  }
	  modalOverlay = null;
	  isModal = false;
	  window.jsLog("Returned to panel");
	}
  };

  // ── Ball reset ────────────────────────────────────────────────────
  function resetBall(dir) {
	ball.x = CW / 2;
	ball.y = CH / 2;
	var angle = (Math.random() * 40 - 20) * Math.PI / 180;
	var spd   = P.INIT_SPD;   // always reset to base speed; per-hit 1.06× handles acceleration
	ball.vx   = (dir || 1) * Math.cos(angle) * spd;
	ball.vy   = Math.sin(angle) * spd;
	flashTimer = 0;
	flashSide  = null;
  }

  // ── Utilities ─────────────────────────────────────────────────────
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  function roundRect(ctx, x, y, w, h, r) {
	ctx.beginPath();
	ctx.moveTo(x + r, y);
	ctx.arcTo(x + w, y,     x + w, y + h, r);
	ctx.arcTo(x + w, y + h, x,     y + h, r);
	ctx.arcTo(x,     y + h, x,     y,     r);
	ctx.arcTo(x,     y,     x + w, y,     r);
	ctx.closePath();
	ctx.fill();
  }

  // ── Main game loop (runs at RAF cadence, ~60 fps) ─────────────────
  function gameLoop() {
	if (!gameRunning) return;

	// Clamp paddle positions
	leftY  = clamp(leftY  + leftVY,  0, CH - PH);
	rightY = clamp(rightY + rightVY, 0, CH - PH);

	// Move ball
	ball.x += ball.vx;
	ball.y += ball.vy;

	// Top / bottom wall bounce
	if (ball.y - BR <= 0) {
	  ball.y  = BR;
	  ball.vy = Math.abs(ball.vy);
	} else if (ball.y + BR >= CH) {
	  ball.y  = CH - BR;
	  ball.vy = -Math.abs(ball.vy);
	}

	// Left paddle collision (ball moving left)
	var lEdge = PAD_M + PW;
	if (ball.vx < 0
		&& ball.x - BR <= lEdge
		&& ball.x - BR >  lEdge - PW - 4
		&& ball.y + BR >= leftY
		&& ball.y - BR <= leftY + PH) {
	  ball.x  = lEdge + BR;
	  ball.vx = Math.abs(ball.vx) * 1.06;   // 6% faster each rally hit
	  ball.vy = clamp(ball.vy + leftVY * 0.30, -13, 13);
	}

	// Right paddle collision (ball moving right)
	var rEdge = CW - PAD_M - PW;
	if (ball.vx > 0
		&& ball.x + BR >= rEdge
		&& ball.x + BR <  rEdge + PW + 4
		&& ball.y + BR >= rightY
		&& ball.y - BR <= rightY + PH) {
	  ball.x  = rEdge - BR;
	  ball.vx = -Math.abs(ball.vx) * 1.06;  // 6% faster each rally hit
	  ball.vy = clamp(ball.vy + rightVY * 0.30, -13, 13);
	}

	// Absolute speed cap
	var spd = Math.sqrt(ball.vx * ball.vx + ball.vy * ball.vy);
	if (spd > 16) { ball.vx = ball.vx / spd * 16; ball.vy = ball.vy / spd * 16; }

	// Scoring
	if (ball.x + BR < 0) {
	  scoreR++;
	  window.jsLog("Right scores!  " + scoreL + " – " + scoreR);
	  flashSide = "right"; flashTimer = 25;
	  if (scoreR >= P.WIN) {
		gameRunning = false;
		draw();
		drawWin("RIGHT");
		return;
	  }
	  resetBall(1);
	} else if (ball.x - BR > CW) {
	  scoreL++;
	  window.jsLog("Left scores!  " + scoreL + " – " + scoreR);
	  flashSide = "left"; flashTimer = 25;
	  if (scoreL >= P.WIN) {
		gameRunning = false;
		draw();
		drawWin("LEFT");
		return;
	  }
	  resetBall(-1);
	}

	if (flashTimer > 0) flashTimer--;

	draw();
	rafId = requestAnimationFrame(gameLoop);
  }

  // ── Rendering ─────────────────────────────────────────────────────
  function getCanvas() { return document.getElementById("pongCanvas"); }

  function draw() {
	var canvas = getCanvas();
	if (!canvas) return;
	var ctx = canvas.getContext("2d");
	var W   = canvas.width, H = canvas.height;

	// Background
	ctx.fillStyle = "#000";
	ctx.fillRect(0, 0, W, H);

	// Score-side flash on point
	if (flashTimer > 0 && flashSide) {
	  var alpha = (flashTimer / 25) * 0.14;
	  ctx.fillStyle = "rgba(255,255,255," + alpha + ")";
	  if (flashSide === "left")  ctx.fillRect(0,     0, W / 2, H);
	  else                       ctx.fillRect(W / 2, 0, W / 2, H);
	}

	// Centre dashed divider
	ctx.save();
	ctx.setLineDash([14, 14]);
	ctx.strokeStyle = "rgba(255,255,255,0.20)";
	ctx.lineWidth   = 3;
	ctx.beginPath();
	ctx.moveTo(W / 2, 0);
	ctx.lineTo(W / 2, H);
	ctx.stroke();
	ctx.restore();

	// Scores
	ctx.fillStyle    = "rgba(255,255,255,0.82)";
	ctx.font         = 'bold 54px "Courier New",monospace';
	ctx.textBaseline = "top";
	ctx.textAlign    = "right";
	ctx.fillText(scoreL, W / 2 - 26, 12);
	ctx.textAlign    = "left";
	ctx.fillText(scoreR, W / 2 + 26, 12);

	// Paddles
	ctx.fillStyle = "#fff";
	roundRect(ctx, PAD_M,            leftY,  PW, PH, 4);
	roundRect(ctx, W - PAD_M - PW,   rightY, PW, PH, 4);

	// Ball
	ctx.beginPath();
	ctx.arc(ball.x, ball.y, BR, 0, Math.PI * 2);
	ctx.fillStyle = "#fff";
	ctx.fill();
  }

  // Overlay text on top of the current frame
  function drawSplash(headline, score, subScore, hint) {
	var canvas = getCanvas();
	if (!canvas) return;
	var ctx = canvas.getContext("2d");
	var W   = canvas.width, H = canvas.height;

	// Dim overlay
	ctx.fillStyle = "rgba(0,0,0,0.62)";
	ctx.fillRect(0, 0, W, H);

	ctx.textAlign    = "center";
	ctx.textBaseline = "middle";

	if (headline) {
	  ctx.fillStyle = "#fff";
	  ctx.font      = 'bold 44px "Courier New",monospace';
	  ctx.fillText(headline, W / 2, H / 2 - (score !== null ? 28 : 0));
	}
	if (score !== null && score !== undefined) {
	  ctx.fillStyle = "rgba(255,255,255,0.75)";
	  ctx.font      = '26px "Courier New",monospace';
	  ctx.fillText(score, W / 2, H / 2 + 18);
	}
	if (hint) {
	  ctx.fillStyle = "rgba(255,255,255,0.42)";
	  ctx.font      = '14px "Courier New",monospace';
	  ctx.fillText(hint, W / 2, H / 2 + (score !== null ? 58 : 30));
	}
  }

  function drawWin(winner) {
	drawSplash(
	  winner + " WINS!",
	  scoreL + "  –  " + scoreR,
	  null,
	  "Press ■ Stop then ▶ Start to play again"
	);
	window.jsLog(
	  "🏆 " + winner + " wins!  Final: " + scoreL + " – " + scoreR
	);
  }

  // ── Idle / attract screen (drawn once, immediately) ────────────────
  (function drawIdle() {
	var canvas = getCanvas();
	if (!canvas) return;
	var ctx = canvas.getContext("2d");
	var W   = canvas.width, H = canvas.height;

	ctx.fillStyle = "#000";
	ctx.fillRect(0, 0, W, H);

	ctx.save();
	ctx.setLineDash([14, 14]);
	ctx.strokeStyle = "rgba(255,255,255,0.18)";
	ctx.lineWidth   = 3;
	ctx.beginPath(); ctx.moveTo(W / 2, 0); ctx.lineTo(W / 2, H); ctx.stroke();
	ctx.restore();

	// Ghost paddles
	ctx.fillStyle = "rgba(255,255,255,0.35)";
	roundRect(ctx, PAD_M,            H / 2 - PH / 2, PW, PH, 4);
	roundRect(ctx, W - PAD_M - PW,   H / 2 - PH / 2, PW, PH, 4);

	ctx.textAlign    = "center";
	ctx.textBaseline = "middle";
	ctx.fillStyle    = "rgba(255,255,255,0.90)";
	ctx.font         = 'bold 52px "Courier New",monospace';
	ctx.fillText("PONG", W / 2, H / 2 - 30);

	ctx.fillStyle = "rgba(255,255,255,0.48)";
	ctx.font      = '15px "Courier New",monospace';
	ctx.fillText("Twist DoubleMotor barrels to control paddles", W / 2, H / 2 + 14);
	ctx.fillText("Press ▶ Start Pong to begin", W / 2, H / 2 + 36);
  }());

})();
""")

	# ------------------------------------------------------------------
	# Poll loop (setTimeout pattern — required for Pyodide stack switching)
	# ------------------------------------------------------------------

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

	# ------------------------------------------------------------------
	# Per-tick update — reads motor speeds, pushes to JS game engine
	# ------------------------------------------------------------------

	async def update(self, *_args):
		if not window.demoActive:
			return

		# Lazy-refresh module refs in case main_pyodide loaded after __init__
		if self._panel_devices_ref is None or self._le_module is None:
			self._refresh_module_refs()

		hw = self._find_hw_device()
		if hw is None or not getattr(hw, "connected", True):
			log("DoubleMotor not connected — stopping", "log-warn")
			window.demoActive = False
			self.controls.reset("run")
			window._pongStop()
			return

		try:
			le = self._le_module
			if le is not None:
				left_speed  = float(hw.motor[le.MOTOR_LEFT].speed  or 0)
				right_speed = float(hw.motor[le.MOTOR_RIGHT].speed or 0)
			else:
				# Fallback subscript with int indices if le not yet available
				left_speed  = float(hw.motor[0].speed or 0)
				right_speed = float(hw.motor[1].speed or 0)

			# Motor speed is in percent (−100 … +100).
			# Convert to px/game-frame; apply per-side invert factor.
			window._setPaddleSpeeds(
				left_speed  * SPEED_SCALE * INVERT_LEFT,
				right_speed * SPEED_SCALE * INVERT_RIGHT,
			)

		except Exception as exc:
			log(f"Motor read error: {exc}", "log-error")


# =========================================================
# ENTRY POINT — do not modify
# =========================================================

_demo = None

def main():
	global _demo
	_demo = PongDemo()

main()