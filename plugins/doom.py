## doom.py — LEGO HW ↔ Doom Input Bridge (PyScript / browser)
## =====================================================================
##
## Embeds the Doom WASM port in the LEGO Education page and bridges
## LEGO Controller + DoubleMotor IMU inputs to Doom keyboard events.
##
## Controls:
##   Left lever fwd        → cycle WEAPON (keys 1-7)
##   Left lever bck        → FIRE (Control)          hold = rapid fire
##   Right lever fwd       → USE / activate (Space)
##   Right lever bck       → STRAFE MODE on  (left/right tilt = strafe)
##   Right lever ctr       → STRAFE MODE off (left/right tilt = turn)
##   IMU tilt fwd/bck      → ArrowUp / ArrowDown
##   IMU tilt left/right   → ArrowLeft/Right  OR  strafe ,/.
##   Left motor rotation   → RUN (Shift) when |degrees| > RUN_THRESHOLD
##
## WASM asset URL: set DOOM_WASM_URL in SECTION 2.
## IMU axes:       set IMU_PITCH_ATTR / IMU_ROLL_ATTR in SECTION 2.
##
## Rules followed:
##  • window._singleUnderscore only (Rule 1 — no mangling)
##  • update() is async def          (Rule 2)
##  • _demo is module-level          (Rule 5)
##  • _inject_script before Logger   (Rule 6)
##  • No third-party imports         (Rule 7)
##  • No options[n] subscript        (Rule 8)
##  • sys.modules for panel devices  (Rule 9)
##  • Any body child gets .id        (Rule 10)

import sys
from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy


# =========================================================
# SECTION 1 — CALLBACKS / EVENT HANDLERS   (edit this)
# =========================================================

# --- Weapon cycling ----------------------------------------
# Doom weapon slots 1–7.  We cycle through them unconditionally;
# Doom silently ignores slots the player doesn't have yet.
WEAPON_SLOTS  = [1, 2, 3, 4, 5, 6, 7]
_weapon_index = 0   # module-level so handlers can mutate it

def on_weapon_cycle():
	"""Left lever pushed forward → advance to next weapon slot.

	We use a JS setTimeout to hold the key down for one frame (~30 ms)
	before releasing, so Doom's keydown handler has time to register it.
	Issuing keydown+keyup in the same microtask is too fast for Doom's
	WASM tick loop to catch.
	"""
	global _weapon_index
	_weapon_index = (_weapon_index + 1) % len(WEAPON_SLOTS)
	slot = WEAPON_SLOTS[_weapon_index]
	log(f"🗡 Weapon → slot {slot}")
	window._doomKeyTap(str(slot))   # keydown, then keyup after 30 ms

def on_fire_press():
	"""Left lever pulled back → hold FIRE."""
	log("🔫 FIRE on")
	window._doomKeyDown("Control")

def on_fire_release():
	"""Left lever returned to centre → release FIRE."""
	log("🔫 FIRE off")
	window._doomKeyUp("Control")

def on_use_press():
	"""Right lever pushed forward → USE / activate."""
	log("🚪 USE")
	window._doomKeyTap(" ")   # tap: press + release after 30 ms

def on_strafe_on():
	log("↔ STRAFE mode on")

def on_strafe_off():
	log("↔ STRAFE mode off")
	# Release any active strafe keys when mode exits
	window._doomKeyUp(",")
	window._doomKeyUp(".")

def on_run_press():
	"""Left motor rotated far enough → hold RUN (Shift)."""
	window._doomKeyDown("Shift")

def on_run_release():
	"""Left motor returned to rest → release RUN."""
	window._doomKeyUp("Shift")

# Movement press/release — called from update() directly (not StateMachine)
# because they depend on the combined strafe-mode flag.

def on_forward_press():  window._doomKeyDown("ArrowUp")
def on_forward_release(): window._doomKeyUp("ArrowUp")
def on_back_press():     window._doomKeyDown("ArrowDown")
def on_back_release():   window._doomKeyUp("ArrowDown")

def on_left_press(strafe: bool):
	if strafe: window._doomKeyDown(",")
	else:      window._doomKeyDown("ArrowLeft")

def on_left_release(strafe: bool):
	# Release both so we never leave a key stuck when mode switches
	window._doomKeyUp(",")
	window._doomKeyUp("ArrowLeft")

def on_right_press(strafe: bool):
	if strafe: window._doomKeyDown(".")
	else:      window._doomKeyDown("ArrowRight")

def on_right_release(strafe: bool):
	window._doomKeyUp(".")
	window._doomKeyUp("ArrowRight")


# =========================================================
# SECTION 2 — CONFIGURATION   (edit this)
# =========================================================

# --- Doom WASM asset URL -----------------------------------
DOOM_WASM_URL = "https://edanahy.github.io/pythonbetademos/assets/doom.wasm"

# --- Doom canvas size (display; Doom resizes to its own framebuffer) --
DOOM_W = 500    # px — display width in the page
DOOM_H = 313    # px — ~500 * (200/320) keeps Doom's aspect ratio

# --- Fullscreen size (centered in modal overlay) -----------
DOOM_FULL_W = 640
DOOM_FULL_H = 400

# --- Timing ------------------------------------------------
POLL_INTERVAL_MS = 50   # ~20 Hz

# --- Controller dead-zone ----------------------------------
LEVER_THRESHOLD = 10    # %

# --- IMU tilt dead-zone ------------------------------------
IMU_THRESHOLD = 20      # degrees

# --- IMU axis attribute names on hw.imu_device -------------
# Swap these two if fwd/bck and left/right are transposed on your hub.
IMU_FB_ATTR   = "pitch"   # forward/back tilt axis
IMU_LR_ATTR   = "roll"    # left/right  tilt axis

# --- IMU axis inversion ------------------------------------
# Set to -1 if a tilt direction triggers the wrong Doom direction.
IMU_FB_INVERT = 1    # 1 = tilt fwd → move fwd; -1 = invert
IMU_LR_INVERT = 1    # 1 = tilt right → turn right; -1 = invert

# --- Left motor run threshold ------------------------------
# Degrees of relative rotation from the reset position that
# triggers RUN (Shift).  Absolute value is used, so rotating
# either direction counts.
RUN_THRESHOLD = 45   # degrees

# --- Layout ------------------------------------------------
RIGHT_COL_WIDTH = 240


# =========================================================
# INFRASTRUCTURE — do not modify; copy-portable across demos
# =========================================================

def _inject_script(js_text: str):
	s = document.createElement("script")
	s.text = js_text
	document.body.appendChild(s)


class Logger:
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


# =========================================================
# INDICATOR PANEL
# =========================================================

class IndicatorPanel:
	"""
	A vertical list of labelled on/off indicator lights.
	Each indicator is a coloured dot + text label.

	  panel = IndicatorPanel()
	  panel.add("fire", "🔫 Fire")
	  panel.set("fire", True)   # light up
	  panel.set("fire", False)  # dim
	  panel.set_label("weapon", "🗡 Weapon [3]")  # update label text
	"""
	COLOR_ON  = "#e05c5c"
	COLOR_OFF = "#444"
	TEXT_ON   = "#111"   # dark text on active — readable against any bg
	TEXT_OFF  = "#888"

	def __init__(self, width_px: int = 220):
		self.element = document.createElement("div")
		self.element.style.cssText = (
			f"width:{width_px}px;font-family:monospace;font-size:13px;"
		)
		self._dots   = {}   # key → dot element
		self._labels = {}   # key → label span element

	def add(self, key: str, label: str) -> "IndicatorPanel":
		row = document.createElement("div")
		row.style.cssText = (
			"display:flex;align-items:center;gap:8px;"
			"margin-bottom:7px;"
		)
		dot = document.createElement("span")
		dot.style.cssText = (
			f"display:inline-block;width:12px;height:12px;"
			f"border-radius:50%;background:{self.COLOR_OFF};"
			"flex-shrink:0;"
		)
		lbl = document.createElement("span")
		lbl.textContent = label
		lbl.style.color = self.TEXT_OFF
		row.appendChild(dot)
		row.appendChild(lbl)
		self.element.appendChild(row)
		self._dots[key]   = dot
		self._labels[key] = lbl
		return self

	def set(self, key: str, active: bool):
		dot = self._dots.get(key)
		lbl = self._labels.get(key)
		if dot is None:
			return
		dot.style.background = self.COLOR_ON  if active else self.COLOR_OFF
		if lbl:
			lbl.style.color  = self.TEXT_ON   if active else self.TEXT_OFF

	def set_label(self, key: str, text: str):
		lbl = self._labels.get(key)
		if lbl:
			lbl.textContent = text


# =========================================================
# DOOM LEGO BRIDGE DEMO CLASS
# =========================================================

class DoomLegoBridgeDemo:
	"""
	Loads Doom WASM directly onto the page and bridges:
	  • LEGO Controller levers → FIRE, WEAPON CYCLE, STRAFE MODE
	  • DoubleMotor IMU tilt   → FWD/BCK/LEFT/RIGHT (or STRAFE L/R)

	Strafe mode (right lever back): left/right IMU tilt sends strafe
	keys instead of turn keys.  Forward/back tilt is unaffected.
	"""

	def __init__(self):
		# ── Cache sys.modules refs ───────────────────────────────
		self._panel_devices_ref = None
		self._le_module         = None
		for mod_name in ("main_pyodide", "__main__"):
			mod = sys.modules.get(mod_name)
			if mod is not None:
				d = getattr(mod, "_panel_devices", None)
				if isinstance(d, dict):
					self._panel_devices_ref = d
					self._le_module = getattr(mod, "le", None)
					break

		# ── State machines — Controller levers ───────────────────
		self._sm_left  = StateMachine("ctr", 2, self._on_left_lever)
		self._sm_right = StateMachine("ctr", 2, self._on_right_lever)

		# ── State machines — IMU axes ────────────────────────────
		self._sm_fb = StateMachine("ctr", 2, self._on_imu_fb)   # fwd/back
		self._sm_lr = StateMachine("ctr", 2, self._on_imu_lr)   # left/right

		# ── Runtime state ────────────────────────────────────────
		self._strafe_mode  = False   # toggled by right lever
		self._running      = False   # True when left motor past threshold
		self._imu_base_fb  = 0.0    # IMU baseline captured at bridge-start
		self._imu_base_lr  = 0.0

		# ── Controls ─────────────────────────────────────────────
		self.controls = ControlsRow()

		# ── Indicator panel ──────────────────────────────────────
		self.indicators = IndicatorPanel(width_px=RIGHT_COL_WIDTH)
		(self.indicators
			 .add("weapon",  f"🗡 Weapon [{WEAPON_SLOTS[_weapon_index]}] (L-lever fwd)")
			 .add("fire",    "🔫 Fire (L-lever bck)")
			 .add("use",     "🚪 Use (R-lever fwd)")
			 .add("strafe",  "↔️ Strafe mode (R-lever bck)")
			 .add("forward", "⬆️ Forward (tilt fwd)")
			 .add("back",    "⬇️ Back (tilt bck)")
			 .add("left",    "⬅️ Left (tilt left)")
			 .add("right",   "➡️ Right (tilt right)")
			 .add("run",     "🏃 Run (L-motor rotation)"))

		# ── Assemble page ────────────────────────────────────────
		self._build_layout()
		self._inject_key_bridge()
		self._inject_fullscreen_modal()
		self._inject_doom_js()
		self._setup_controls()
		self._start_loop()

		log("Doom ↔ LEGO Bridge ready — connect hardware, then ▶ Start Bridge")

	# ── Layout ───────────────────────────────────────────────────

	def _build_layout(self):
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		header = document.createElement("div")
		header.className = "panel-header"
		h2 = document.createElement("h2")
		h2.innerHTML = "<em>Will it Doom?</em> (LEGO HW × Doom Bridge)"
		header.appendChild(h2)

		columns = document.createElement("div")
		columns.className            = "columns-panel"
		columns.style.display        = "flex"
		columns.style.flexDirection  = "row"
		columns.style.gap            = "16px"
		columns.style.alignItems     = "flex-start"
		columns.style.justifyContent = "center"

		# ── Left column: canvas + fullscreen button ───────────────
		left_col = document.createElement("div")
		left_col.style.display       = "flex"
		left_col.style.flexDirection = "column"
		left_col.style.alignItems    = "center"

		canvas = document.createElement("canvas")
		canvas.id = "DoomGame"
		canvas.width  = DOOM_W
		canvas.height = DOOM_H
		canvas.setAttribute("tabindex", "0")
		canvas.style.cssText = (
			f"display:block;width:{DOOM_W}px;height:{DOOM_H}px;"
			"border:2px solid #333;opacity:0.8;cursor:pointer;"
		)
		_inject_script("""
(function() {
  document.addEventListener("focusin",  function(e) {
	if (e.target && e.target.id === "DoomGame") e.target.style.opacity = "1.0";
  });
  document.addEventListener("focusout", function(e) {
	if (e.target && e.target.id === "DoomGame") e.target.style.opacity = "0.8";
  });
})();
""")

		caption = document.createElement("p")
		caption.style.cssText = (
			"margin:6px 0 2px;font-size:12px;color:#ccc;"
			"text-align:center;font-family:sans-serif;"
		)
		caption.innerHTML = "Click Doom to focus keyboard (ESC for main menu).<br />LEGO HW inputs work regardless when Bridge is active."

		fs_btn = document.createElement("button")
		fs_btn.id = "doomFullscreenBtn"
		fs_btn.setAttribute("type", "button")
		fs_btn.textContent = "⛶ Fullscreen"
		fs_btn.style.cssText = "margin-top:6px;padding:5px 14px;cursor:pointer;"
		fs_btn.addEventListener("click", create_proxy(
			lambda e: window._doomOpenFullscreen()
		))

		left_col.appendChild(canvas)
		left_col.appendChild(caption)
		left_col.appendChild(fs_btn)

		# ── Right column: Start Bridge + indicators ───────────────
		right_col = document.createElement("div")
		right_col.style.display       = "flex"
		right_col.style.flexDirection = "column"
		right_col.style.gap           = "12px"

		right_col.appendChild(self.controls.element)
		right_col.appendChild(self.indicators.element)

		columns.appendChild(left_col)
		columns.appendChild(right_col)

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(columns, header.nextSibling)

	# ── JS: key bridge ───────────────────────────────────────────

	def _inject_key_bridge(self):
		"""
		_doomKeyDown(key) / _doomKeyUp(key) dispatch real KeyboardEvents
		on #DoomGame so Doom's own canvas listeners fire.
		Always targets the real (small) canvas — the modal clone just
		mirrors visually; Doom's WASM still drives the original canvas.
		"""
		_inject_script("""
(function() {
  function dispatch(key, down) {
	var c = document.getElementById("DoomGame");
	if (!c) { window.jsLog("DoomGame canvas not found", "log-warn"); return; }
	c.dispatchEvent(new KeyboardEvent(down ? "keydown" : "keyup",
	  { key: key, bubbles: true, cancelable: true }));
  }
  window._doomKeyDown = function(k) { dispatch(k, true);  };
  window._doomKeyUp   = function(k) { dispatch(k, false); };
  // Tap: keydown now, keyup after 30 ms — long enough for Doom's
  // 35 Hz tick loop (~28 ms/frame) to see the key before release.
  window._doomKeyTap  = function(k) {
	dispatch(k, true);
	setTimeout(function() { dispatch(k, false); }, 30);
  };
})();
""")

	# ── JS: fullscreen modal ──────────────────────────────────────

	def _inject_fullscreen_modal(self):
		"""
		_doomOpenFullscreen() opens a modal overlay with the Doom canvas
		scaled up to DOOM_FULL_W × DOOM_FULL_H via CSS transform.
		A visible ✕ button (and clicking the gray backdrop) closes it
		and returns the canvas to its original position.
		ESC is NOT intercepted — it passes through to Doom as usual.
		"""
		_inject_script(
			"window._DOOM_FULL_W = " + str(DOOM_FULL_W) + ";"
			"window._DOOM_FULL_H = " + str(DOOM_FULL_H) + ";"
			"window._DOOM_W      = " + str(DOOM_W)      + ";"
			"window._DOOM_H      = " + str(DOOM_H)      + ";"
		)
		_inject_script("""
(function() {
  var _overlay = null;
  var _placeholder = null;   // invisible div that holds the canvas's spot

  window._doomOpenFullscreen = function() {
	if (_overlay) return;   // already open
	var canvas = document.getElementById("DoomGame");
	if (!canvas) return;

	// Create an invisible placeholder the same size as the canvas
	// so the left column doesn't collapse while the canvas is away.
	_placeholder = document.createElement("div");
	_placeholder.style.cssText = (
	  "width:"  + window._DOOM_W + "px;" +
	  "height:" + window._DOOM_H + "px;" +
	  "visibility:hidden;"
	);
	canvas.parentNode.insertBefore(_placeholder, canvas);

	// Overlay — semi-transparent backdrop
	_overlay = document.createElement("div");
	_overlay.id = "doomOverlay";
	_overlay.style.cssText = (
	  "position:fixed;top:0;left:0;width:100%;height:100%;" +
	  "background:rgba(0,0,0,0.75);z-index:9000;" +
	  "display:flex;align-items:center;justify-content:center;"
	);

	// Close button
	var closeBtn = document.createElement("button");
	closeBtn.textContent = "✕ Close";
	closeBtn.style.cssText = (
	  "position:absolute;top:16px;right:20px;" +
	  "padding:6px 14px;font-size:14px;cursor:pointer;z-index:9001;"
	);
	closeBtn.addEventListener("click", function(e) {
	  e.stopPropagation();
	  window._doomCloseFullscreen();
	});

	// Canvas wrapper — sets the display size without touching Doom's
	// internal framebuffer resolution.
	var wrapper = document.createElement("div");
	wrapper.style.cssText = (
	  "position:relative;" +
	  "width:"  + window._DOOM_FULL_W + "px;" +
	  "height:" + window._DOOM_FULL_H + "px;"
	);

	// Move canvas into wrapper; resize its CSS display box
	canvas.style.width  = window._DOOM_FULL_W + "px";
	canvas.style.height = window._DOOM_FULL_H + "px";
	wrapper.appendChild(canvas);
	_overlay.appendChild(wrapper);
	_overlay.appendChild(closeBtn);

	// Click on gray backdrop (not on canvas/button) closes modal
	_overlay.addEventListener("click", function(e) {
	  if (e.target === _overlay) window._doomCloseFullscreen();
	});

	document.body.appendChild(_overlay);
	canvas.focus();
  };

  window._doomCloseFullscreen = function() {
	if (!_overlay) return;
	var canvas = document.getElementById("DoomGame");

	// Restore canvas to original display size and put it back
	if (canvas) {
	  canvas.style.width  = window._DOOM_W + "px";
	  canvas.style.height = window._DOOM_H + "px";
	  if (_placeholder && _placeholder.parentNode) {
		_placeholder.parentNode.insertBefore(canvas, _placeholder);
	  }
	}
	if (_placeholder && _placeholder.parentNode) {
	  _placeholder.parentNode.removeChild(_placeholder);
	}
	_placeholder = null;
	_overlay.parentNode.removeChild(_overlay);
	_overlay = null;
  };
})();
""")

	# ── JS: Doom loader ───────────────────────────────────────────

	def _inject_doom_js(self):
		_inject_script(f'window._DOOM_WASM_URL = "{DOOM_WASM_URL}";')
		_inject_script("""
(function() {
  var canvas = document.getElementById("DoomGame");
  if (!canvas) { window.jsLog("DoomGame canvas missing", "log-error"); return; }

  var mem = null;
  var ctx = canvas.getContext("2d");
  var img = null;

  function onGameInit(w, h) {
	canvas.width  = w; canvas.height = h;
	img = ctx.createImageData(w, h);
	window.jsLog("Doom init " + w + "x" + h, "log-info");
  }

  function drawFrame(idx) {
	var buf = new Uint8Array(mem.buffer, idx, canvas.width * canvas.height * 4);
	var d = img.data;
	for (var i = 0, n = d.length / 4; i < n; i++) {
	  d[4*i]   = buf[4*i+2];
	  d[4*i+1] = buf[4*i+1];
	  d[4*i+2] = buf[4*i];
	  d[4*i+3] = 255;
	}
	ctx.putImageData(img, 0, 0);
  }

  function readStr(ptr, len) {
	return new TextDecoder("utf-8", {fatal:false})
	  .decode(new Uint8Array(mem.buffer).slice(ptr, ptr + len));
  }

  var imports = {
	loading:        { onGameInit: onGameInit, wadSizes: function(){}, readWads: function(){} },
	ui:             { drawFrame: drawFrame },
	runtimeControl: { timeInMilliseconds: function(){ return BigInt(Math.trunc(performance.now())); } },
	console:        { onInfoMessage:  function(p,l){ console.log(readStr(p,l));   },
					  onErrorMessage: function(p,l){ console.error(readStr(p,l)); } },
	gameSaving:     { sizeOfSaveGame: function(){ return 0; },
					  readSaveGame:   function(){ return 0; },
					  writeSaveGame:  function(){ return 0; } },
  };

  window.jsLog("Fetching doom.wasm…");
  WebAssembly.instantiateStreaming(fetch(window._DOOM_WASM_URL), imports)
	.then(function(r) {
	  var exp = r.instance.exports;
	  mem = exp.memory;

	  var keyMap = new Map([
		["ArrowLeft",  exp.KEY_LEFTARROW],  ["ArrowRight", exp.KEY_RIGHTARROW],
		["ArrowUp",    exp.KEY_UPARROW],    ["ArrowDown",  exp.KEY_DOWNARROW],
		[",",          exp.KEY_STRAFE_L],   [".",          exp.KEY_STRAFE_R],
		["Control",    exp.KEY_FIRE],       [" ",          exp.KEY_USE],
		["Shift",      exp.KEY_SHIFT],      ["Tab",        exp.KEY_TAB],
		["Escape",     exp.KEY_ESCAPE],     ["Enter",      exp.KEY_ENTER],
		["Backspace",  exp.KEY_BACKSPACE],  ["Alt",        exp.KEY_ALT],
	  ]);

	  function toDoomKey(e) {
		var k = keyMap.has(e.key) ? keyMap.get(e.key)
			  : e.key.length === 1 ? e.key.charCodeAt(0) : null;
		if (k !== null) { e.stopPropagation(); e.preventDefault(); }
		return k;
	  }
	  canvas.addEventListener("keydown", function(e){ var k=toDoomKey(e); if(k!==null) exp.reportKeyDown(k); });
	  canvas.addEventListener("keyup",   function(e){ var k=toDoomKey(e); if(k!==null) exp.reportKeyUp(k);   });

	  exp.initGame();
	  setInterval(exp.tickGame, 1000 / 35);
	  window.jsLog("Doom running — click canvas to focus!", "log-info");
	})
	.catch(function(e){ window.jsLog("Doom load failed: " + e, "log-error"); });
})();
""")

	# ── Controls ─────────────────────────────────────────────────

	def _setup_controls(self):
		window.demoActive = False

		def _start():
			# ── Capture IMU baseline ─────────────────────────────
			self._imu_base_fb = 0.0
			self._imu_base_lr = 0.0
			hw = self._find_doublemotor()
			if hw is not None:
				imu = getattr(hw, "imu_device", None)
				if imu is not None:
					self._imu_base_fb = (
						float(getattr(imu, IMU_FB_ATTR, 0) or 0) / 10.0
					)
					self._imu_base_lr = (
						float(getattr(imu, IMU_LR_ATTR, 0) or 0) / 10.0
					)
					log(f"IMU baseline — fb:{self._imu_base_fb:.1f}° "
						f"lr:{self._imu_base_lr:.1f}°")

			# ── Reset left motor relative position ───────────────
			self._running = False
			if hw is not None and self._le_module is not None:
				try:
					hw.motor_reset_relative_position(
						motor=self._le_module.MOTOR_LEFT,
						position=0,
						blocking=True,
					)
					log("Left motor relative position reset to 0")
				except Exception as exc:
					log(f"Motor reset warning: {exc}", "log-warn")

			window.demoActive = True
			log("Bridge started")

		def _stop():
			for key in ("Control", " ", "Shift", "ArrowUp", "ArrowDown",
						"ArrowLeft", "ArrowRight", ",", "."):
				try:
					window._doomKeyUp(key)
				except Exception:
					pass
			for ind_key in ("weapon", "fire", "use", "strafe",
							"forward", "back", "left", "right", "run"):
				self.indicators.set(ind_key, False)
			self._strafe_mode = False
			self._running     = False
			window.demoActive = False
			log("Bridge stopped")

		self.controls.add("run", "▶ Start Bridge", "■ Stop Bridge",
						  on_on=_start, on_off=_stop)

	# ── Poll loop ─────────────────────────────────────────────────

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

	# ── Per-tick update ───────────────────────────────────────────

	async def update(self, *_args):
		if not window.demoActive:
			return
		try:
			self._tick_controller()
			self._tick_imu()
			self._tick_motor()
		except Exception as exc:
			log(f"Hardware read error: {exc}", "log-error")

	# ── Hardware: Controller ──────────────────────────────────────

	def _tick_controller(self):
		hw = self._find_controller()
		if hw is None:
			return
		sensor = hw.sensor
		left_pct  = float(getattr(sensor, "leftPercent",  0) or 0)
		right_pct = float(getattr(sensor, "rightPercent", 0) or 0)
		self._sm_left.update(self._classify(left_pct))
		self._sm_right.update(self._classify(right_pct))

	def _find_controller(self):
		if self._panel_devices_ref is None:
			return None
		for hw in self._panel_devices_ref.values():
			if hw is None or not getattr(hw, "connected", True):
				continue
			sensor = getattr(hw, "sensor", None)
			if sensor is None:
				continue
			if getattr(sensor, "leftPercent", None) is not None:
				return hw
		return None

	# ── Hardware: DoubleMotor IMU ─────────────────────────────────

	def _tick_imu(self):
		hw = self._find_doublemotor()
		if hw is None:
			return
		imu = getattr(hw, "imu_device", None)
		if imu is None:
			return

		# IMU values are in deci-degrees; divide by 10 → degrees.
		# Subtract baseline captured at bridge-start so resting position = 0.
		raw_fb = float(getattr(imu, IMU_FB_ATTR, 0) or 0) / 10.0
		raw_lr = float(getattr(imu, IMU_LR_ATTR, 0) or 0) / 10.0
		delta_fb = (raw_fb - self._imu_base_fb) * IMU_FB_INVERT
		delta_lr = (raw_lr - self._imu_base_lr) * IMU_LR_INVERT

		self._sm_fb.update(self._classify_imu(delta_fb))
		self._sm_lr.update(self._classify_imu(delta_lr))

	def _tick_motor(self):
		"""Read left motor relative position and toggle RUN (Shift)."""
		hw = self._find_doublemotor()
		if hw is None or self._le_module is None:
			return
		try:
			pos = float(hw.motor[self._le_module.MOTOR_LEFT].position or 0)
		except Exception:
			return
		should_run = abs(pos) > RUN_THRESHOLD
		if should_run and not self._running:
			self._running = True
			on_run_press()
			self.indicators.set("run", True)
		elif not should_run and self._running:
			self._running = False
			on_run_release()
			self.indicators.set("run", False)

	def _find_doublemotor(self):
		if self._panel_devices_ref is None:
			return None
		for hw in self._panel_devices_ref.values():
			if hw is None or not getattr(hw, "connected", True):
				continue
			# DoubleMotor exposes imu_device; Controller does not.
			if getattr(hw, "imu_device", None) is not None:
				return hw
		return None

	# ── Classify helpers ──────────────────────────────────────────

	def _classify(self, pct: float) -> str:
		"""Lever % → 'fwd' | 'ctr' | 'bck'."""
		if pct >  LEVER_THRESHOLD: return "fwd"
		if pct < -LEVER_THRESHOLD: return "bck"
		return "ctr"

	def _classify_imu(self, deg: float) -> str:
		"""IMU degrees → 'fwd' | 'ctr' | 'bck'."""
		if deg >  IMU_THRESHOLD: return "fwd"
		if deg < -IMU_THRESHOLD: return "bck"
		return "ctr"

	# ── State machine callbacks: Controller ───────────────────────

	def _on_left_lever(self, new_zone: str):
		"""Left lever: fwd = WEAPON CYCLE, bck = FIRE (hold)."""
		try:
			if new_zone == "fwd":
				on_weapon_cycle()
				slot = WEAPON_SLOTS[_weapon_index]
				self.indicators.set_label("weapon",
					f"🗡 Weapon [{slot}] (L-lever fwd)")
				# Flash weapon indicator briefly to show the cycle fired
				self.indicators.set("weapon", True)
			elif new_zone == "ctr":
				on_fire_release()
				self.indicators.set("fire",   False)
				self.indicators.set("weapon", False)
			elif new_zone == "bck":
				on_fire_press()
				self.indicators.set("fire", True)
		except Exception as exc:
			log(f"Left lever error: {exc}", "log-error")

	def _on_right_lever(self, new_zone: str):
		"""Right lever: fwd = USE (tap), bck = STRAFE MODE on, ctr = off."""
		try:
			if new_zone == "fwd":
				on_use_press()
				self.indicators.set("use", True)
			elif new_zone == "bck":
				self.indicators.set("use", False)
				self._strafe_mode = True
				on_strafe_on()
				self.indicators.set("strafe", True)
			else:  # ctr
				self.indicators.set("use", False)
				if self._strafe_mode:
					self._strafe_mode = False
					on_strafe_off()
					self.indicators.set("strafe", False)
					self._reissue_lr()
		except Exception as exc:
			log(f"Right lever error: {exc}", "log-error")

	# ── State machine callbacks: IMU ──────────────────────────────

	def _on_imu_fb(self, new_zone: str):
		"""IMU forward/back tilt → move forward / back."""
		try:
			if new_zone == "fwd":
				on_forward_press()
				self.indicators.set("forward", True)
				self.indicators.set("back",    False)
			elif new_zone == "bck":
				on_back_press()
				self.indicators.set("back",    True)
				self.indicators.set("forward", False)
			else:  # ctr
				on_forward_release()
				on_back_release()
				self.indicators.set("forward", False)
				self.indicators.set("back",    False)
		except Exception as exc:
			log(f"IMU fwd/bck error: {exc}", "log-error")

	def _on_imu_lr(self, new_zone: str):
		"""IMU left/right tilt → turn or strafe (depending on mode)."""
		try:
			if new_zone == "fwd":   # "fwd" here means tilted right
				on_right_press(self._strafe_mode)
				self.indicators.set("right", True)
				self.indicators.set("left",  False)
			elif new_zone == "bck": # "bck" here means tilted left
				on_left_press(self._strafe_mode)
				self.indicators.set("left",  True)
				self.indicators.set("right", False)
			else:  # ctr
				on_right_release(self._strafe_mode)
				on_left_release(self._strafe_mode)
				self.indicators.set("left",  False)
				self.indicators.set("right", False)
		except Exception as exc:
			log(f"IMU left/right error: {exc}", "log-error")

	def _reissue_lr(self):
		"""
		When strafe mode changes while a left/right tilt is active,
		release the old key and press the new one so Doom doesn't
		get a stuck key during the mode switch.
		"""
		current = self._sm_lr.current
		if current == "fwd":
			on_right_release(not self._strafe_mode)  # release old
			on_right_press(self._strafe_mode)         # press new
		elif current == "bck":
			on_left_release(not self._strafe_mode)
			on_left_press(self._strafe_mode)


# =========================================================
# ENTRY POINT — do not modify
# =========================================================

_demo = None

def main():
	global _demo
	_demo = DoomLegoBridgeDemo()

main()