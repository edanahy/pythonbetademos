## rl_qtable_demo.py — Reinforcement Learning Q-Table Walker Demo
## ================================================================
## PyScript demo for the LEGO Education browser page.
## Teaches Q-learning by training a DoubleMotor robot to walk straight.
##
## Hardware: DoubleMotor connected via Device Panel (Model A).
##   - hw.imu_device.yaw        → heading offset in degrees (sync property)
##   - hw.motor[le.MOTOR_LEFT]  → left motor state  (sync property)
##   - hw.motor[le.MOTOR_RIGHT] → right motor state (sync property)
## Motor commands issued via injected JS helpers (_motorCmd, _motorStop).
##
## RL Formulation:
##   States  (5): Hard Left / Soft Left / Straight / Soft Right / Hard Right
##   Actions (3): Turn Left / Go Straight / Turn Right
##   Algorithm: Q-learning (ε-greedy, Bellman update)
##
## Rules from INSTRUCTIONS.md observed throughout:
##   Rule 1  — no window.__name inside class (use _singleUnderscore)
##   Rule 2  — update() is async def
##   Rule 3  — StateMachine not used; no issue
##   Rule 4  — _start_loop uses setTimeout pattern (verbatim from template)
##   Rule 5  — _demo kept at module level
##   Rule 6  — _inject_script defined before Logger
##   Rule 7  — only stdlib: math, json, random, sys
##   Rule 8  — no [] subscript on JsProxy DOM; use .value on selects
##   Rule 9  — hardware via sys.modules, not window
##   Rule 10 — all elements appended to body have .id set first

from datetime import datetime
from pyscript import document
from js import window
from pyodide.ffi import create_proxy
import sys
import json
import random
import math


# =========================================================
# SECTION 1 — MODULE-LEVEL CALLBACKS / EVENT HANDLERS
# =========================================================
# Called from the demo class at the right moment.
# `log` is available at call-time (defined in infrastructure below).
# Hardware reached via the demo instance stored in _demo.

def on_training_start():
	"""Begin the RL training loop."""
	global _demo
	if _demo is None:
		return
	_demo._read_config()
	_demo._training       = True
	_demo._running_policy = False
	_demo._step_once_mode = False
	window.demoActive = True
	log("Training started  (ε-greedy, α=" + str(_demo._alpha) +
		", γ=" + str(_demo._gamma) + ", ε=" + str(_demo._epsilon) + ")")


def on_training_stop():
	"""Stop training without resetting the Q-table."""
	global _demo
	if _demo is None:
		return
	_demo._training         = False
	_demo._running_policy   = False
	_demo._waiting_for_reset = False
	_demo._current_action   = None
	window.demoActive = False
	_demo._stop_motors()   # Python hw object, not window (Rule 9)
	if _demo._btn_continue is not None:
		_demo._btn_continue.setAttribute("disabled", "")
	log("Training stopped — Q-table preserved")


def on_policy_start():
	"""Run the current greedy policy (no exploration, no Q updates)."""
	global _demo
	if _demo is None:
		return
	_demo._read_config()
	_demo._training         = False
	_demo._running_policy   = True
	_demo._step_once_mode   = False
	_demo._waiting_for_reset = False
	_demo._current_action   = None
	window.demoActive = True
	log("Running learned policy (greedy, no Q updates)")


def on_policy_stop():
	"""Stop policy execution."""
	global _demo
	if _demo is None:
		return
	_demo._running_policy   = False
	_demo._current_action   = None
	window.demoActive = False
	_demo._stop_motors()   # Python hw object, not window (Rule 9)
	log("Policy execution stopped")


def on_reset_all():
	"""Reset Q-table, counters, yaw origin, and stop everything."""
	global _demo
	if _demo is None:
		return
	_demo._training         = False
	_demo._running_policy   = False
	_demo._step_once_mode   = False
	_demo._waiting_for_reset = False
	_demo._current_action   = None
	window.demoActive = False
	_demo._stop_motors()   # Python hw object, not window (Rule 9)
	if _demo._btn_continue is not None:
		_demo._btn_continue.setAttribute("disabled", "")
	# Reset Q-table
	_demo._q_table    = [[0.0] * 3 for _ in range(5)]
	_demo._episode    = 0
	_demo._step       = 0
	_demo._last_reward = None
	_demo._pre_action_state = None
	# Reset yaw origin to current reading
	hw = _demo._find_hw()
	if hw is not None:
		try:
			_demo._yaw_origin = float(hw.imu_device.yaw or 0)
		except Exception:
			_demo._yaw_origin = 0.0
	# Refresh display
	_demo._render_qtable()
	_demo._update_episode_span()
	_demo._clear_ep_log()
	log("Reset complete — Q-table zeroed, yaw origin reset", "log-warn")


# =========================================================
# SECTION 2 — CONFIGURATION
# =========================================================

# --- Layout -------------------------------------------------
FRAME_W          = 330    # left-column width (px)
FRAME_H          = 500    # left-column height (px)
RIGHT_COL_WIDTH  = 410    # right-column width (px)
POLL_INTERVAL_MS = 250    # update() period in ms (~4 Hz)
PANEL_BG         = "#0d1117"

# --- Motor speeds -------------------------------------------
BASE_SPEED       = 30     # % speed for both motors in "straight" action
TURN_BIAS        = 12     # differential for left/right turns
						  #   Turn Left:  left=BASE-BIAS, right=BASE+BIAS
						  #   Straight:   left=right=BASE
						  #   Turn Right: left=BASE+BIAS, right=BASE-BIAS
MOVE_DURATION_MS = 500    # ms per action step (yaw read immediately after — no settle)

# --- State thresholds (degrees) -----------------------------
# Yaw is reported in deci-degrees by the IMU, divided by 10 in _get_yaw().
# These are wider than typical because the robot's mismatched legs cause
# large yaw swings even from small movements.
YAW_SOFT = 20   # |yaw| boundary between Straight and Soft Left/Right
YAW_HARD = 50   # |yaw| boundary between Soft and Hard Left/Right

# --- RL hyperparameters -------------------------------------
ALPHA              = 0.3   # learning rate
GAMMA              = 0.9   # discount factor
EPSILON            = 0.3   # exploration probability (training only)
STEPS_PER_EPISODE  = 10    # steps before episode counter increments
MAX_EPISODES       = 20    # auto-stop after this many episodes

# --- Rewards ------------------------------------------------
REWARD_STRAIGHT    =  1.0
REWARD_SOFT        = -0.3
REWARD_HARD        = -1.0

# --- State / action labels ----------------------------------
STATE_NAMES  = ["Hard Left", "Soft Left", "Straight", "Soft Right", "Hard Right"]
ACTION_NAMES = ["Turn Left", "Go Straight", "Turn Right"]
STATE_EMOJI  = ["⬅⬅", "⬅", "⬆", "➡", "➡➡"]
ACTION_EMOJI = ["↰", "↑", "↱"]


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
	parent_el.appendChild(row.element)
	row.reset("key")
	"""

	def __init__(self):
		self.element = document.createElement("div")
		self.element.style.display        = "flex"
		self.element.style.gap            = "8px"
		self.element.style.justifyContent = "center"
		self.element.style.flexWrap       = "wrap"
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
# Kept for completeness; RLWalkerDemo uses its own panel.
# ---------------------------------------------------------

class BarChartPanel:
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
# RL WALKER DEMO CLASS
# =========================================================

class RLWalkerDemo:
	"""
	RL Q-Table demo: trains a DoubleMotor robot to walk straight.

	Q-Table: 5 states (yaw-based orientation) × 3 actions (turn left,
	go straight, turn right).  Updated via Q-learning (Bellman equation).
	Motor commands issued via injected JS helpers to avoid blocking calls.
	"""

	# ------------------------------------------------------------------
	# Construction
	# ------------------------------------------------------------------

	def __init__(self):
		# Cache sys.modules refs once (Rule 9)
		self._panel_devices_ref = None
		self._le_module         = None
		self._device_id         = None
		for mod_name in ("main_pyodide", "__main__"):
			mod = sys.modules.get(mod_name)
			if mod is not None:
				d = getattr(mod, "_panel_devices", None)
				if isinstance(d, dict):
					self._panel_devices_ref = d
					self._le_module = getattr(mod, "le", None)
					break

		# ── RL state ────────────────────────────────────────────────
		self._q_table           = [[0.0] * 3 for _ in range(5)]
		self._episode           = 0
		self._step              = 0
		self._yaw_origin        = 0.0
		self._training          = False
		self._running_policy    = False
		self._step_once_mode    = False
		self._last_reward       = None
		self._current_state     = 2       # assume Straight initially
		self._current_action    = None    # None = pick fresh action next tick
		self._pre_action_state  = None
		self._phase_until_ms    = 0       # wall-clock deadline for current action
		self._waiting_for_reset = False   # True while paused between episodes

		# ── Motor tracking ───────────────────────────────────────────
		# True when motor_run_for_time is used (auto-stops); False for motor_run.
		# Set by _run_motors on first successful call.
		self._motor_autostop   = False

		# ── Config (read from UI sliders/inputs before each episode) ─
		self._base_speed        = BASE_SPEED
		self._turn_bias         = TURN_BIAS
		self._move_duration     = MOVE_DURATION_MS
		self._yaw_soft          = YAW_SOFT
		self._yaw_hard          = YAW_HARD
		self._alpha             = ALPHA
		self._gamma             = GAMMA
		self._epsilon           = EPSILON
		self._steps_per_episode = STEPS_PER_EPISODE
		self._max_episodes      = MAX_EPISODES
		self._reward_straight   = REWARD_STRAIGHT
		self._reward_soft       = REWARD_SOFT
		self._reward_hard       = REWARD_HARD

		# ── UI element refs (set during _build_layout) ───────────────
		self._inp_base_speed    = None
		self._inp_turn_bias     = None
		self._inp_move_dur      = None
		self._inp_yaw_soft      = None
		self._inp_yaw_hard      = None
		self._inp_alpha         = None
		self._inp_gamma         = None
		self._inp_epsilon       = None
		self._inp_steps         = None
		self._inp_episodes      = None
		self._inp_rew_straight  = None
		self._inp_rew_soft      = None
		self._inp_rew_hard      = None
		self._btn_continue      = None

		self._span_yaw          = None
		self._span_state        = None
		self._span_episode      = None
		self._span_step         = None
		self._span_action       = None
		self._span_reward       = None

		# ── Assemble ─────────────────────────────────────────────────
		self.controls = ControlsRow()
		self._inject_styles()
		self._inject_motor_helpers()
		self._inject_qtable_renderer()
		self._build_layout()
		self._setup_controls()
		self._start_loop()

		log("RL Walker Demo ready — connect a DoubleMotor, then press Start Training")

	# ------------------------------------------------------------------
	# Hardware helpers (Rule 9: sys.modules, not window)
	# ------------------------------------------------------------------

	def _find_hw(self):
		"""Return the DoubleMotor panel device object, or None."""
		if self._panel_devices_ref is None:
			return None
		# Auto-discover on first call
		if self._device_id is None:
			for pid, dev in self._panel_devices_ref.items():
				type_name = type(dev).__name__
				if "Double" in type_name or "double" in type_name:
					self._device_id = pid
					break
			if self._device_id is None:
				# Fallback: any device that has imu_device
				for pid, dev in self._panel_devices_ref.items():
					if hasattr(dev, "imu_device"):
						self._device_id = pid
						break
		if self._device_id is None:
			return None
		return self._panel_devices_ref.get(self._device_id)

	def _get_yaw(self):
		"""Return yaw (degrees) relative to origin, or None if unavailable.
		The IMU reports yaw in deci-degrees (e.g. 900 = 90°), so divide by 10.
		"""
		hw = self._find_hw()
		if hw is None or not getattr(hw, "connected", True):
			return None
		try:
			raw = float(hw.imu_device.yaw or 0) / 10.0   # deci-degrees → degrees
			return raw - self._yaw_origin
		except Exception:
			return None

	def _reset_yaw_origin(self):
		"""Store current absolute yaw (degrees) as the origin."""
		hw = self._find_hw()
		if hw is not None:
			try:
				self._yaw_origin = float(hw.imu_device.yaw or 0) / 10.0
				log(f"Yaw origin reset to {self._yaw_origin:.1f}°")
				return
			except Exception:
				pass
		self._yaw_origin = 0.0
		log("Yaw origin reset (no HW — using 0.0)", "log-warn")

	# ------------------------------------------------------------------
	# RL helpers
	# ------------------------------------------------------------------

	def _yaw_to_state(self, yaw) -> int:
		"""Map relative yaw (degrees) → state index 0-4.
		Thresholds are tunable via RL Settings (default ±20° soft, ±50° hard).
		"""
		if yaw is None:                    return 2
		hs = self._yaw_soft
		hh = self._yaw_hard
		if yaw >  hh:  return 0   # Hard Left
		if yaw >  hs:  return 1   # Soft Left
		if yaw >= -hs: return 2   # Straight
		if yaw >= -hh: return 3   # Soft Right
		return 4                  # Hard Right

	def _state_reward(self, state: int) -> float:
		if state == 2:          return self._reward_straight
		if state in (1, 3):    return self._reward_soft
		return self._reward_hard

	def _choose_action(self, state: int) -> int:
		"""ε-greedy (ε=0 when running policy)."""
		eps = 0.0 if self._running_policy else self._epsilon
		if random.random() >= eps:
			row = self._q_table[state]
			best = max(row)
			# Break ties randomly
			best_indices = [i for i, v in enumerate(row) if v == best]
			return random.choice(best_indices)
		return random.randint(0, 2)

	def _update_qtable_values(self, s: int, a: int, r: float, sp: int):
		"""Bellman Q-learning update."""
		best_next = max(self._q_table[sp])
		old_val   = self._q_table[s][a]
		self._q_table[s][a] = old_val + self._alpha * (
			r + self._gamma * best_next - old_val)

	# ------------------------------------------------------------------
	# Motor control — uses the Python hw object directly (Model A).
	# The JS helper path was wrong: the DoubleMotor device is a Python
	# object in _panel_devices, NOT a JS global on window.
	# ------------------------------------------------------------------

	def _log_hw_methods(self, hw):
		"""One-time introspection: log what the hw object exposes."""
		try:
			attrs = sorted(a for a in dir(hw) if not a.startswith("__"))
			log(f"HW type: {type(hw).__name__}", "log-info")
			log(f"HW attrs: {', '.join(attrs)}", "log-info")
			# Also inspect hw.motor if present
			le = self._le_module
			if le is not None:
				try:
					motor_l = hw.motor[le.MOTOR_LEFT]
					mattrs = sorted(a for a in dir(motor_l) if not a.startswith("__"))
					log(f"hw.motor[LEFT] attrs: {', '.join(mattrs)}", "log-info")
				except Exception as me:
					log(f"hw.motor inspect: {me}", "log-warn")
		except Exception as e:
			log(f"hw inspect error: {e}", "log-warn")

	def _run_motors(self, left_pct: int, right_pct: int):
		"""
		Drive both motors. Left motor forward = CCW, Right motor forward = CW.

		Tries in order:
		  1. movement_move_tank(left_speed, right_speed) — single call, no direction
			 constants needed: positive speed = forward for each side.
		  2. motor_run_for_time with le.COUNTERCLOCKWISE / le.CLOCKWISE (or string
			 fallbacks "counterclockwise" / "clockwise").
		  3. motor_run continuous with same direction constants.

		NOTE: MOVEMENT_TURN_DIRECTION_LEFT/RIGHT are whole-robot movement constants
		and do not map correctly to individual motor direction for motor_run_for_time.
		"""
		hw = self._find_hw()
		if hw is None:
			log("_run_motors: no HW found — check Device Panel", "log-warn")
			return

		# One-time introspection on first call
		if not getattr(self, "_hw_inspected", False):
			self._hw_inspected = True
			self._log_hw_methods(hw)

		le = self._le_module
		if le is None:
			log("_run_motors: le module not available", "log-warn")
			return

		abs_l = int(left_pct)
		abs_r = int(right_pct)

		# ── Pattern 1: movement_move_tank ─────────────────────────────────
		# Takes signed speeds directly — positive = forward, no direction constants.
		# Left slower than right → robot curves left; left faster → curves right.
		mmtank = getattr(hw, "movement_move_tank", None)
		if mmtank is not None:
			_tank_ok = False
			# Try timed auto-stop first (unit="msec" or "seconds")
			for kw in (
				{"left_speed": abs_l, "right_speed": abs_r, "unit": "msec",    "amount": self._move_duration},
				{"left_speed": abs_l, "right_speed": abs_r, "unit": "seconds", "amount": self._move_duration / 1000.0},
			):
				try:
					mmtank(**kw)
					self._motor_autostop = True
					_tank_ok = True
					break
				except Exception:
					pass
			if not _tank_ok:
				# Continuous fallback (keyword then positional)
				for call in (
					lambda: mmtank(left_speed=abs_l, right_speed=abs_r),
					lambda: mmtank(abs_l, abs_r),
				):
					try:
						call()
						self._motor_autostop = False
						_tank_ok = True
						break
					except Exception:
						pass
			if _tank_ok:
				if not getattr(self, "_motor_method_logged", False):
					self._motor_method_logged = True
					mode = "auto-stop" if self._motor_autostop else "continuous"
					log(f"Motor: movement_move_tank ({mode}) ✓", "log-info")
				return
			log("movement_move_tank: all signatures failed — trying per-motor API", "log-warn")

		# ── Pattern 2: motor_run_for_time with per-motor direction constants ─
		# Left motor always CCW (forward), Right motor always CW (forward).
		# Speeds differ for turns but directions never change.
		ml  = getattr(le, "MOTOR_LEFT",  None)
		mr  = getattr(le, "MOTOR_RIGHT", None)
		ccw = getattr(le, "COUNTERCLOCKWISE", None) or "counterclockwise"
		cw  = getattr(le, "CLOCKWISE",        None) or "clockwise"

		if not getattr(self, "_dir_logged", False):
			self._dir_logged = True
			log(f"Per-motor dir consts: CCW={ccw!r}  CW={cw!r}", "log-info")

		mrft = getattr(hw, "motor_run_for_time", None)
		if mrft is not None:
			for extra_kw in ({"blocking": False}, {}):
				try:
					if abs_l > 0:
						mrft(time=self._move_duration, motor=ml, direction=ccw,
							 speed=abs_l, **extra_kw)
					if abs_r > 0:
						mrft(time=self._move_duration, motor=mr, direction=cw,
							 speed=abs_r, **extra_kw)
					self._motor_autostop = True
					if not getattr(self, "_motor_method_logged", False):
						self._motor_method_logged = True
						log("Motor: motor_run_for_time ✓", "log-info")
					return
				except Exception:
					continue
			log("motor_run_for_time: both blocking modes failed", "log-warn")

		# ── Pattern 3: motor_run (continuous) ─────────────────────────────
		mr_fn = getattr(hw, "motor_run", None)
		if mr_fn is not None:
			try:
				if abs_l > 0: mr_fn(motor=ml, direction=ccw, speed=abs_l)
				if abs_r > 0: mr_fn(motor=mr, direction=cw,  speed=abs_r)
				self._motor_autostop = False
				if not getattr(self, "_motor_method_logged", False):
					self._motor_method_logged = True
					log("Motor: motor_run (continuous) ✓", "log-info")
				return
			except Exception as e:
				log(f"motor_run failed: {e}", "log-warn")

		log("_run_motors: no working motor method — see HW attrs above", "log-warn")

	def _stop_motors(self):
		"""
		Stop both motors.

		Uses movement_stop() first (single call, both motors).
		Falls back to motor_stop(motor=X) per motor.
		Called by button handlers and by the phase FSM when motor_autostop=False.
		"""
		hw = self._find_hw()
		if hw is None:
			return

		# ── movement_stop: stops both motors in one call ───────────────────
		mvstop = getattr(hw, "movement_stop", None)
		if mvstop is not None:
			try:
				mvstop()
				return
			except Exception as e:
				log(f"movement_stop failed: {e}", "log-warn")

		# ── motor_stop per motor ───────────────────────────────────────────
		le = self._le_module
		if le is None:
			return
		ml = getattr(le, "MOTOR_LEFT",  None)
		mr = getattr(le, "MOTOR_RIGHT", None)
		mstop = getattr(hw, "motor_stop", None)
		if mstop is None:
			return
		for motor in (ml, mr):
			if motor is None:
				continue
			# Try several likely signatures
			for call in (
				lambda: mstop(motor=motor),
				lambda: mstop(motor=motor, stop_action="brake"),
				lambda: mstop(motor=motor, end_state="brake"),
			):
				try:
					call()
					break
				except Exception:
					continue

	def _apply_action(self, action: int):
		"""Translate action index → left/right motor speeds and start motors."""
		b = self._base_speed
		t = self._turn_bias
		if action == 0:
			left, right = b - t, b + t   # Turn Left:  left slower, right faster
		elif action == 1:
			left, right = b, b            # Go Straight: equal speeds
		else:
			left, right = b + t, b - t   # Turn Right: left faster, right slower
		# Clamp to [0, 100] — negative speeds would reverse direction unexpectedly
		self._run_motors(max(0, left), max(0, right))

	# ------------------------------------------------------------------
	# Config read-back from UI
	# ------------------------------------------------------------------

	def _read_config(self):
		"""Pull current slider / input values into instance variables."""
		def _fv(inp, default, lo=None, hi=None):
			if inp is None:
				return default
			try:
				v = float(str(inp.value or default))
				if lo is not None: v = max(lo, v)
				if hi is not None: v = min(hi, v)
				return v
			except Exception:
				return default

		self._base_speed        = int(_fv(self._inp_base_speed,   BASE_SPEED,    5, 60))
		self._turn_bias         = int(_fv(self._inp_turn_bias,     TURN_BIAS,     0, 30))
		self._move_duration     = int(_fv(self._inp_move_dur,      MOVE_DURATION_MS, 100, 3000))
		self._alpha             = _fv(self._inp_alpha,    ALPHA,    0.01, 1.0)
		self._gamma             = _fv(self._inp_gamma,    GAMMA,    0.01, 1.0)
		self._epsilon           = _fv(self._inp_epsilon,  EPSILON,  0.0,  1.0)
		self._steps_per_episode = int(_fv(self._inp_steps,    STEPS_PER_EPISODE, 1, 100))
		self._max_episodes      = int(_fv(self._inp_episodes, MAX_EPISODES,       1, 200))
		self._yaw_soft          = _fv(self._inp_yaw_soft, YAW_SOFT, 5,  90)
		self._yaw_hard          = _fv(self._inp_yaw_hard, YAW_HARD, 10, 180)
		self._reward_straight   = _fv(self._inp_rew_straight, REWARD_STRAIGHT)
		self._reward_soft       = _fv(self._inp_rew_soft,     REWARD_SOFT)
		self._reward_hard       = _fv(self._inp_rew_hard,     REWARD_HARD)

	# ------------------------------------------------------------------
	# Style injection
	# ------------------------------------------------------------------

	def _inject_styles(self):
		_inject_script("""
(function(){
var style = document.createElement('style');
style.id = 'rlDemoStyles';
style.textContent = `
  .rl-panel { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #e0e0e0; }
  .rl-section { background:#16213e; border:1px solid #2a3a5a; border-radius:7px;
				padding:6px 10px; margin-bottom:8px; }
  .rl-section summary { cursor:pointer; font-weight:600; padding:4px 0;
						 color:#7eb8f7; user-select:none; }
  .rl-section summary:hover { color:#aad4ff; }
  .rl-row { display:flex; align-items:center; justify-content:space-between;
			 margin:4px 0; gap:8px; }
  .rl-row label { flex:1; color:#b0c0d8; font-size:12px; }
  .rl-row input[type=range] { flex:1.2; accent-color:#4a90d9; }
  .rl-row input[type=number] { width:65px; background:#0d1117; color:#e0e0e0;
								 border:1px solid #3a4a6a; border-radius:4px;
								 padding:2px 5px; font-size:12px; }
  .rl-row .rl-val { width:40px; text-align:right; color:#7eb8f7; font-size:12px; }
  .rl-status { background:#0f2040; border:1px solid #1a3060; border-radius:7px;
			   padding:10px 14px; margin:8px 0; line-height:2.0; }
  .rl-status-row { display:flex; justify-content:space-between; }
  .rl-status-label { color:#7eb8f7; font-size:12px; }
  .rl-status-val   { color:#ffffff; font-weight:600; font-size:13px; }
  .rl-btn { padding:7px 14px; border:none; border-radius:5px; cursor:pointer;
			 font-size:12px; font-weight:600; transition:opacity 0.15s; }
  .rl-btn:hover { opacity:0.85; }
  .rl-btn-train  { background:#2a6496; color:#fff; }
  .rl-btn-policy { background:#2a7a4a; color:#fff; }
  .rl-btn-step   { background:#5a4a20; color:#fff; }
  .rl-btn-reset  { background:#6a2a2a; color:#fff; }
  .rl-btn-yaw    { background:#3a3a6a; color:#fff; font-size:11px; padding:5px 10px; }
  .rl-controls   { display:flex; flex-wrap:wrap; gap:7px; justify-content:center;
					margin:10px 0; }
  #rlQTableWrap { overflow-x:auto; }
  #rlQTable { border-collapse:collapse; width:100%; font-family:monospace;
			   font-size:13px; }
  #rlQTable th { background:#1a2040; color:#7eb8f7; padding:6px 10px;
				  border:1px solid #2a3a5a; text-align:center; }
  #rlQTable td { text-align:center; padding:7px 8px; border:1px solid #2a3a5a;
				  transition:background 0.35s; cursor:default; min-width:82px; }
  #rlQTable td.rl-state-label { text-align:left; padding-left:10px; min-width:90px;
								  font-size:12px; color:#ccc; }
  #rlEpLog { height:200px; overflow-y:auto; background:#0d1117;
			  border:1px solid #2a3a5a; border-radius:5px;
			  padding:6px 8px; font-family:monospace; font-size:11px;
			  color:#aaa; line-height:1.6; }
  .rl-ep-step  { color:#ccc; }
  .rl-ep-ep    { color:#7eb8f7; font-weight:600; }
  .rl-ep-good  { color:#4caf50; }
  .rl-ep-bad   { color:#ef5350; }
  .rl-ep-soft  { color:#ffb74d; }
  .rl-ep-pause { color:#ffd700; font-style:italic; font-weight:600; }
  .rl-btn-continue { background:#1a6640; color:#fff; }
  .rl-btn-continue:disabled { background:#1e2e26; color:#4a7a5a; cursor:default; }
  .rl-legend   { display:flex; gap:12px; font-size:11px; margin:4px 0 8px 0; }
  .rl-legend-item { display:flex; align-items:center; gap:5px; }
  .rl-legend-swatch { width:14px; height:14px; border-radius:3px; display:inline-block; }
  .rl-right-section { margin-bottom:14px; }
  .rl-right-h3 { font-size:13px; font-weight:700; color:#7eb8f7; margin:0 0 6px 0; }
`;
document.head.appendChild(style);
})();
""")

	# ------------------------------------------------------------------
	# _inject_motor_helpers — previously injected JS motor helpers.
	# Motor commands now go through the Python hw object directly
	# (_run_motors / _stop_motors above), so this is a no-op stub kept
	# for call-site compatibility with __init__.
	# ------------------------------------------------------------------

	def _inject_motor_helpers(self):
		pass  # Motor control is now fully Python-side (Model A hw object)

	# ------------------------------------------------------------------
	# JS Q-table renderer
	# ------------------------------------------------------------------

	def _inject_qtable_renderer(self):
		_inject_script("""
window._rlRenderQTable = function(jsonStr) {
  var data = JSON.parse(jsonStr);
  var q = data.qtable, cs = data.curState, ca = data.curAction;

  // Find max absolute value for normalisation
  var maxAbs = 0.01;
  for (var r = 0; r < 5; r++)
	for (var c = 0; c < 3; c++) {
	  var av = Math.abs(q[r][c]);
	  if (av > maxAbs) maxAbs = av;
	}

  for (var r = 0; r < 5; r++) {
	// State label highlight
	var lbl = document.getElementById('rl_slabel_' + r);
	if (lbl) {
	  lbl.style.color      = (r === cs) ? '#FFD700' : '#ccc';
	  lbl.style.fontWeight = (r === cs) ? '700' : '400';
	}

	for (var c = 0; c < 3; c++) {
	  var cell = document.getElementById('rl_cell_' + r + '_' + c);
	  if (!cell) continue;
	  var val = q[r][c];
	  var intensity = Math.min(1.0, Math.abs(val) / maxAbs);
	  var bg;
	  if (val > 0.005) {
		var g = Math.round(55 + 200 * intensity);
		bg = 'rgb(20,' + g + ',35)';
	  } else if (val < -0.005) {
		var rv = Math.round(55 + 200 * intensity);
		bg = 'rgb(' + rv + ',20,20)';
	  } else {
		bg = '#1a2040';
	  }
	  cell.style.background = bg;
	  cell.textContent      = val.toFixed(3);
	  cell.style.color      = val > 0.005 ? '#a0ffb0' : val < -0.005 ? '#ffaaaa' : '#888';
	  // Gold outline on current state+action
	  cell.style.outline    = (r === cs && c === ca) ? '3px solid #FFD700' : 'none';
	  cell.style.fontWeight = (r === cs && c === ca) ? '700' : '400';
	}
  }

  // Best action arrow per row
  for (var r = 0; r < 5; r++) {
	var row_q = q[r];
	var best_v = Math.max(row_q[0], row_q[1], row_q[2]);
	var arrows = ['↰', '↑', '↱'];
	for (var c = 0; c < 3; c++) {
	  var cell = document.getElementById('rl_cell_' + r + '_' + c);
	  if (!cell) continue;
	  var val = q[r][c];
	  var txt = val.toFixed(3);
	  if (row_q[c] === best_v && Math.abs(best_v) > 0.005) {
		txt = arrows[c] + ' ' + txt;
	  }
	  cell.textContent = txt;
	}
  }
};
""")

	# ------------------------------------------------------------------
	# Build page layout
	# ------------------------------------------------------------------

	def _build_layout(self):
		"""Insert two-column panel into the page next to #device-panel."""
		device_panel = document.getElementById("device-panel")
		parent       = device_panel.parentNode

		# ── Header ──────────────────────────────────────────────────
		header = document.createElement("div")
		header.className = "panel-header"
		h2 = document.createElement("h2")
		h2.textContent = "RL Walker — Teach a Robot to Walk Straight"
		header.appendChild(h2)

		# ── Columns wrapper ──────────────────────────────────────────
		columns = document.createElement("div")
		columns.className           = "columns-panel"
		columns.style.display       = "flex"
		columns.style.flexDirection = "row"
		columns.style.gap           = "18px"
		columns.style.alignItems    = "flex-start"
		columns.style.justifyContent = "center"

		left_col  = document.createElement("div")
		left_col.style.display       = "flex"
		left_col.style.flexDirection = "column"
		left_col.style.width         = f"{FRAME_W}px"
		left_col.className           = "rl-panel"

		right_col = document.createElement("div")
		right_col.style.display       = "flex"
		right_col.style.flexDirection = "column"
		right_col.style.width         = f"{RIGHT_COL_WIDTH}px"
		right_col.className           = "rl-panel"

		self._build_left_col(left_col)
		self._build_right_col(right_col)

		columns.appendChild(left_col)
		columns.appendChild(right_col)

		parent.insertBefore(header,  device_panel.nextSibling)
		parent.insertBefore(columns, header.nextSibling)

	def _build_left_col(self, parent):
		"""Populate the left column with config sections and controls."""

		# ── Robot Config (collapsible) ───────────────────────────────
		robot_cfg = document.createElement("details")
		robot_cfg.className = "rl-section"
		robot_cfg.setAttribute("open", "")
		rcsum = document.createElement("summary")
		rcsum.textContent = "⚙ Robot Config"
		robot_cfg.appendChild(rcsum)

		self._inp_base_speed = self._make_slider(
			robot_cfg, "Base Speed", 5, 60, 1, BASE_SPEED, "%")
		self._inp_turn_bias = self._make_slider(
			robot_cfg, "Turn Bias", 0, 30, 1, TURN_BIAS, "%")
		self._inp_move_dur = self._make_slider(
			robot_cfg, "Step Duration", 100, 3000, 100, MOVE_DURATION_MS, "ms")

		# Yaw reset button
		yaw_row = document.createElement("div")
		yaw_row.style.marginTop = "6px"
		yaw_btn = document.createElement("button")
		yaw_btn.setAttribute("type", "button")
		yaw_btn.textContent = "🔄 Reset Yaw Origin"
		yaw_btn.className = "rl-btn rl-btn-yaw"

		def _yaw_click(e):
			self._reset_yaw_origin()
		yaw_btn.addEventListener("click", create_proxy(_yaw_click))
		yaw_row.appendChild(yaw_btn)
		robot_cfg.appendChild(yaw_row)
		parent.appendChild(robot_cfg)

		# ── RL Settings (collapsible, closed by default) ─────────────
		rl_cfg = document.createElement("details")
		rl_cfg.className = "rl-section"
		rlsum = document.createElement("summary")
		rlsum.textContent = "🧠 RL Settings"
		rl_cfg.appendChild(rlsum)

		self._inp_alpha   = self._make_number(rl_cfg, "Learning Rate α", ALPHA,   0.01, 1.0, 0.01)
		self._inp_gamma   = self._make_number(rl_cfg, "Discount Factor γ", GAMMA, 0.01, 1.0, 0.01)
		self._inp_epsilon = self._make_number(rl_cfg, "Exploration ε", EPSILON,   0.0,  1.0, 0.05)
		self._inp_steps   = self._make_number(rl_cfg, "Steps / Episode", STEPS_PER_EPISODE, 1, 100, 1)
		self._inp_episodes = self._make_number(rl_cfg, "Max Episodes", MAX_EPISODES, 1, 200, 1)

		rl_sep2 = document.createElement("div")
		rl_sep2.style.cssText = "margin:6px 0 2px 0;font-size:11px;color:#5a7a9a;"
		rl_sep2.textContent = "State thresholds (°):"
		rl_cfg.appendChild(rl_sep2)
		self._inp_yaw_soft = self._make_number(rl_cfg, "Soft drift (±°)", YAW_SOFT, 5, 90, 5)
		self._inp_yaw_hard = self._make_number(rl_cfg, "Hard drift (±°)", YAW_HARD, 10, 180, 5)

		rl_sep = document.createElement("div")
		rl_sep.style.cssText = "margin:6px 0 2px 0;font-size:11px;color:#5a7a9a;"
		rl_sep.textContent = "Rewards:"
		rl_cfg.appendChild(rl_sep)
		self._inp_rew_straight = self._make_number(rl_cfg, "Straight",  REWARD_STRAIGHT, -5, 5, 0.1)
		self._inp_rew_soft     = self._make_number(rl_cfg, "Soft drift", REWARD_SOFT,    -5, 5, 0.1)
		self._inp_rew_hard     = self._make_number(rl_cfg, "Hard drift", REWARD_HARD,    -5, 5, 0.1)
		parent.appendChild(rl_cfg)

		# ── Live Status ──────────────────────────────────────────────
		status = document.createElement("div")
		status.className = "rl-status"

		self._span_yaw     = self._status_row(status, "Yaw",         "—°")
		self._span_state   = self._status_row(status, "State",       "—")
		self._span_episode = self._status_row(status, "Episode",     "0")
		self._span_step    = self._status_row(status, "Step",        "0")
		self._span_action  = self._status_row(status, "Last Action", "—")
		self._span_reward  = self._status_row(status, "Last Reward", "—")
		parent.appendChild(status)

		# ── Controls ─────────────────────────────────────────────────
		ctrl_div = document.createElement("div")
		ctrl_div.className = "rl-controls"

		# Training toggle
		self._btn_train = ToggleButton(
			"▶ Start Training", "⏹ Stop Training",
			on_on=on_training_start, on_off=on_training_stop)
		self._btn_train.element.className = "rl-btn rl-btn-train"
		ctrl_div.appendChild(self._btn_train.element)

		# Policy toggle
		self._btn_policy = ToggleButton(
			"🎯 Run Policy", "⏹ Stop Policy",
			on_on=on_policy_start, on_off=on_policy_stop)
		self._btn_policy.element.className = "rl-btn rl-btn-policy"
		ctrl_div.appendChild(self._btn_policy.element)

		# Step Once button
		step_btn = document.createElement("button")
		step_btn.setAttribute("type", "button")
		step_btn.textContent = "⏭ Step Once"
		step_btn.className   = "rl-btn rl-btn-step"
		def _step_click(e):
			if self._training or self._running_policy:
				log("Stop current mode before stepping", "log-warn")
				return
			self._read_config()
			self._step_once_mode    = True
			self._waiting_for_reset = False
			self._current_action    = None
			self._training          = True
			window.demoActive       = True
			log("Single step requested")
		step_btn.addEventListener("click", create_proxy(_step_click))
		ctrl_div.appendChild(step_btn)

		# Continue button (enabled when paused between episodes)
		self._btn_continue = document.createElement("button")
		self._btn_continue.setAttribute("type", "button")
		self._btn_continue.setAttribute("disabled", "")
		self._btn_continue.textContent = "▶ Continue"
		self._btn_continue.className   = "rl-btn rl-btn-continue"
		def _continue_click(e):
			if not self._waiting_for_reset:
				return
			self._reset_yaw_origin()          # auto-reset yaw since robot is pointing straight
			self._waiting_for_reset = False
			self._current_action    = None
			self._btn_continue.setAttribute("disabled", "")
			log("▶ Continuing — yaw reset, next episode starting")
		self._btn_continue.addEventListener("click", create_proxy(_continue_click))
		ctrl_div.appendChild(self._btn_continue)

		# Reset All button
		reset_btn = document.createElement("button")
		reset_btn.setAttribute("type", "button")
		reset_btn.textContent = "🗑 Reset All"
		reset_btn.className   = "rl-btn rl-btn-reset"
		def _reset_click(e):
			# Also reset the toggle buttons visual state
			self._btn_train.reset()
			self._btn_policy.reset()
			on_reset_all()
		reset_btn.addEventListener("click", create_proxy(_reset_click))
		ctrl_div.appendChild(reset_btn)

		parent.appendChild(ctrl_div)

	def _build_right_col(self, parent):
		"""Populate the right column with Q-table and episode log."""

		# ── Q-Table ─────────────────────────────────────────────────
		qt_section = document.createElement("div")
		qt_section.className = "rl-right-section"

		qt_h3 = document.createElement("h3")
		qt_h3.className   = "rl-right-h3"
		qt_h3.textContent = "Q-Table  (rows = states, cols = actions)"
		qt_section.appendChild(qt_h3)

		# Color legend
		legend = document.createElement("div")
		legend.className = "rl-legend"
		for color, label in [("#1a7a23", "Positive reward"), ("#7a1a1a", "Negative reward"),
							  ("#1a2040", "Zero / neutral"), ("#FFD700", "Current state+action")]:
			item = document.createElement("div")
			item.className = "rl-legend-item"
			sw = document.createElement("span")
			sw.className = "rl-legend-swatch"
			sw.style.background = color
			sw.style.border = ("2px solid #FFD700" if color == "#FFD700" else "1px solid #444")
			tx = document.createElement("span")
			tx.textContent = label
			tx.style.fontSize = "11px"
			item.appendChild(sw)
			item.appendChild(tx)
			legend.appendChild(item)
		qt_section.appendChild(legend)

		# Table wrapper (for horizontal scroll if needed)
		wrap = document.createElement("div")
		wrap.id = "rlQTableWrap"

		tbl = document.createElement("table")
		tbl.id = "rlQTable"

		# Header row
		thead = document.createElement("thead")
		hrow  = document.createElement("tr")
		for label in ["State", "Turn Left ↰", "Go Straight ↑", "Turn Right ↱"]:
			th = document.createElement("th")
			th.textContent = label
			hrow.appendChild(th)
		thead.appendChild(hrow)
		tbl.appendChild(thead)

		# Body rows
		tbody = document.createElement("tbody")
		for r in range(5):
			tr = document.createElement("tr")
			# State label cell
			td_lbl = document.createElement("td")
			td_lbl.className = "rl-state-label"
			td_lbl.id        = f"rl_slabel_{r}"
			td_lbl.textContent = f"{STATE_EMOJI[r]} {STATE_NAMES[r]}"
			tr.appendChild(td_lbl)
			# Q-value cells
			for c in range(3):
				td = document.createElement("td")
				td.id          = f"rl_cell_{r}_{c}"
				td.textContent = "0.000"
				td.style.background = "#1a2040"
				td.style.color      = "#888"
				tr.appendChild(td)
			tbody.appendChild(tr)

		tbl.appendChild(tbody)
		wrap.appendChild(tbl)
		qt_section.appendChild(wrap)
		parent.appendChild(qt_section)

		# ── Episode info ─────────────────────────────────────────────
		ep_section = document.createElement("div")
		ep_section.className = "rl-right-section"
		ep_h3 = document.createElement("h3")
		ep_h3.className   = "rl-right-h3"
		ep_h3.textContent = "Episode Log"
		ep_section.appendChild(ep_h3)
		ep_log = document.createElement("div")
		ep_log.id = "rlEpLog"
		ep_log.className = "rl-ep-log"
		ep_section.appendChild(ep_log)
		parent.appendChild(ep_section)

	# ------------------------------------------------------------------
	# UI helper builders
	# ------------------------------------------------------------------

	def _make_slider(self, parent, label: str, lo, hi, step, default, unit: str = ""):
		"""Create a labeled range slider row. Returns the input element."""
		row = document.createElement("div")
		row.className = "rl-row"
		lbl = document.createElement("label")
		lbl.textContent = label
		inp = document.createElement("input")
		inp.setAttribute("type", "range")
		inp.setAttribute("min",  str(lo))
		inp.setAttribute("max",  str(hi))
		inp.setAttribute("step", str(step))
		inp.value = str(default)
		val_span = document.createElement("span")
		val_span.className   = "rl-val"
		val_span.textContent = str(default) + unit

		def _on_input(e):
			val_span.textContent = str(inp.value) + unit
		inp.addEventListener("input", create_proxy(_on_input))

		row.appendChild(lbl)
		row.appendChild(inp)
		row.appendChild(val_span)
		parent.appendChild(row)
		return inp

	def _make_number(self, parent, label: str, default, lo, hi, step):
		"""Create a labeled number input row. Returns the input element."""
		row = document.createElement("div")
		row.className = "rl-row"
		lbl = document.createElement("label")
		lbl.textContent = label
		inp = document.createElement("input")
		inp.setAttribute("type",  "number")
		inp.setAttribute("min",   str(lo))
		inp.setAttribute("max",   str(hi))
		inp.setAttribute("step",  str(step))
		inp.value = str(default)
		row.appendChild(lbl)
		row.appendChild(inp)
		parent.appendChild(row)
		return inp

	def _status_row(self, parent, label: str, initial: str):
		"""Create a status label+value row. Returns the value <span>."""
		row = document.createElement("div")
		row.className = "rl-status-row"
		lbl = document.createElement("span")
		lbl.className   = "rl-status-label"
		lbl.textContent = label + ":"
		val = document.createElement("span")
		val.className   = "rl-status-val"
		val.textContent = initial
		row.appendChild(lbl)
		row.appendChild(val)
		parent.appendChild(row)
		return val

	# ------------------------------------------------------------------
	# Controls setup (called after _build_layout)
	# ------------------------------------------------------------------

	def _setup_controls(self):
		window.demoActive = False

	# ------------------------------------------------------------------
	# Display update helpers
	# ------------------------------------------------------------------

	def _render_qtable(self):
		"""Push Q-table data to the JS renderer."""
		try:
			data = json.dumps({
				"qtable":   self._q_table,
				"curState": self._current_state,
				"curAction": self._current_action if self._current_action is not None else -1,
			})
			window._rlRenderQTable(data)
		except Exception:
			pass

	def _update_live_display(self, yaw, state: int):
		"""Update the status spans in the left column."""
		try:
			if self._span_yaw   is not None:
				self._span_yaw.textContent = f"{yaw:.1f}°"
			if self._span_state is not None:
				self._span_state.textContent = f"{STATE_EMOJI[state]} {STATE_NAMES[state]}"
		except Exception:
			pass

	def _update_step_display(self, action, reward):
		"""Update last-action and last-reward spans."""
		try:
			if self._span_action is not None and action is not None:
				self._span_action.textContent = f"{ACTION_EMOJI[action]} {ACTION_NAMES[action]}"
			if self._span_reward is not None and reward is not None:
				sign = "+" if reward > 0 else ""
				self._span_reward.textContent = f"{sign}{reward:.2f}"
				col = "#4caf50" if reward > 0 else "#ef5350" if reward < 0 else "#888"
				self._span_reward.style.color = col
		except Exception:
			pass

	def _update_episode_span(self):
		"""Update episode and step counter displays."""
		try:
			if self._span_episode is not None:
				self._span_episode.textContent = str(self._episode)
			if self._span_step is not None:
				self._span_step.textContent = str(self._step)
		except Exception:
			pass

	def _append_ep_log(self, msg: str, css_class: str = "rl-ep-step"):
		"""Prepend a line to the episode log (newest on top)."""
		try:
			el = document.getElementById("rlEpLog")
			if el is None:
				return
			line = document.createElement("div")
			line.className   = css_class
			line.textContent = msg
			if el.firstChild:
				el.insertBefore(line, el.firstChild)
			else:
				el.appendChild(line)
		except Exception:
			pass

	def _clear_ep_log(self):
		try:
			el = document.getElementById("rlEpLog")
			if el is not None:
				el.innerHTML = ""
		except Exception:
			pass

	def _end_training(self, reason: str = "complete"):
		"""Stop training and reset visual toggle state."""
		self._training          = False
		self._running_policy    = False
		self._waiting_for_reset = False
		self._current_action    = None
		window.demoActive       = False
		self._stop_motors()
		if self._btn_continue is not None:
			self._btn_continue.setAttribute("disabled", "")
		self._btn_train.reset()
		self._btn_policy.reset()
		log(f"Training {reason} — {self._episode} episodes, Q-table ready", "log-warn")

	# ------------------------------------------------------------------
	# Poll loop (verbatim infrastructure — do not modify)
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
	# Main update loop (Rule 2: must be async def)
	# ------------------------------------------------------------------

	async def update(self, *_args):
		# ── Always refresh live yaw/state display ────────────────────
		yaw = self._get_yaw()
		if yaw is not None:
			state = self._yaw_to_state(yaw)
			self._current_state = state
			self._update_live_display(yaw, state)
		else:
			state = self._current_state

		# Paused between episodes — waiting for robot to be repositioned
		if self._waiting_for_reset:
			return

		# Nothing running → idle
		if not (self._training or self._running_policy):
			return

		# Max-episode limit (training only)
		if self._training and not self._running_policy and \
				self._episode >= self._max_episodes:
			self._end_training("complete")
			return

		# Hardware lost
		if yaw is None:
			log("DoubleMotor not found — stopping. Check Device Panel.", "log-warn")
			self._end_training("aborted — no HW")
			return

		try:
			now_ms = int(window.performance.now())
		except Exception:
			return

		# ── First tick of a new run: pick and apply first action ────
		if self._current_action is None:
			s = self._current_state
			a = self._choose_action(s)
			self._pre_action_state = s
			self._current_action   = a
			self._phase_until_ms   = now_ms + self._move_duration
			self._apply_action(a)
			self._render_qtable()
			self._update_step_display(a, self._last_reward)
			return

		# ── Action still in progress — keep reading yaw, wait for timer
		if now_ms < self._phase_until_ms:
			return

		# ── Action complete: read result, update Q, schedule next ────
		sp = self._current_state          # yaw already updated at top of tick
		s  = self._pre_action_state
		a  = self._current_action
		r  = self._state_reward(sp)
		self._last_reward = r

		# Q-table update (training only, not policy run)
		if self._training and not self._running_policy:
			self._update_qtable_values(s, a, r, sp)

		# Log the transition
		rew_cls = ("rl-ep-good" if r > 0 else
				   "rl-ep-bad"  if r < self._reward_soft else "rl-ep-soft")
		sign = "+" if r > 0 else ""
		step_msg = (f"Ep{self._episode+1} S{self._step+1}:  "
					f"{STATE_NAMES[s]} → {ACTION_NAMES[a]} "
					f"→ r={sign}{r:.2f} → {STATE_NAMES[sp]}")
		self._append_ep_log(step_msg, rew_cls)

		# ── Step-once: stop after exactly this one action ────────────
		if self._step_once_mode:
			self._step_once_mode = False
			self._training       = False
			window.demoActive    = False
			self._stop_motors()
			self._current_action = None
			self._update_episode_span()
			self._update_step_display(a, r)
			self._render_qtable()
			log("Step complete")
			return

		# ── Advance step / episode counters ──────────────────────────
		self._step += 1
		ep_boundary = (self._step >= self._steps_per_episode
					   and not self._running_policy)
		if ep_boundary:
			self._episode += 1
			self._step = 0
			self._append_ep_log(
				f"══ Episode {self._episode} / {self._max_episodes} complete ══",
				"rl-ep-ep")

		self._update_episode_span()
		self._update_step_display(a, r)
		self._render_qtable()

		# ── Episode boundary: stop and wait for robot reset ──────────
		if ep_boundary and self._training:
			self._stop_motors()
			self._current_action    = None
			self._waiting_for_reset = True
			if self._btn_continue is not None:
				self._btn_continue.removeAttribute("disabled")
			self._append_ep_log(
				"⏸ Pick up robot, point straight, press ▶ Continue", "rl-ep-pause")
			return

		# ── Immediately apply next action (no stop/settle gap) ───────
		new_a = self._choose_action(sp)
		self._pre_action_state = sp
		self._current_action   = new_a
		self._phase_until_ms   = now_ms + self._move_duration
		self._apply_action(new_a)
		self._render_qtable()
		self._update_step_display(new_a, r)


# =========================================================
# ENTRY POINT — do not modify
# =========================================================

_demo = None   # module-level ref prevents GC of demo object (Rule 5)

def main():
	global _demo
	_demo = RLWalkerDemo()

main()