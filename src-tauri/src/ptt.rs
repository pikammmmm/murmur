//! Pure hold-alone push-to-talk state machine — no Win32, fully unit-tested.
//!
//! The Shift key is used constantly for capitalization, so we only start
//! recording when the trigger key is held *alone* past a threshold. If any other
//! key is pressed while the trigger is held (before recording starts), it was
//! capitalization/a shortcut, not dictation, and we cancel. `now_ms` is injected
//! so the logic is deterministic and testable; the Win32 layer feeds real time.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    StartRecording,
    StopRecording,
    /// Abort an in-progress dictation: discard the audio, type nothing (Esc).
    Cancel,
}

#[derive(Debug)]
pub struct PttState {
    threshold_ms: u64,
    armed_at: Option<u64>,
    recording: bool,
    /// Set when a dictation is aborted (Esc) while the trigger is still held.
    /// Suppresses re-arming until the trigger is released, so holding the key
    /// after a cancel can't immediately restart recording. Cleared on release.
    cancelled: bool,
}

impl PttState {
    pub fn new(threshold_ms: u64) -> Self {
        Self { threshold_ms, armed_at: None, recording: false, cancelled: false }
    }

    pub fn is_recording(&self) -> bool {
        self.recording
    }

    pub fn is_armed(&self) -> bool {
        self.armed_at.is_some()
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled
    }

    pub fn set_threshold(&mut self, threshold_ms: u64) {
        self.threshold_ms = threshold_ms;
    }

    /// Trigger key down. Arms the timer; key-repeat does not reset it. While the
    /// cancel latch is set (Esc pressed, trigger still held) it stays disarmed.
    pub fn on_trigger_down(&mut self, now_ms: u64) {
        if self.recording || self.cancelled {
            return;
        }
        if self.armed_at.is_none() {
            self.armed_at = Some(now_ms);
        }
    }

    /// Any non-trigger key down. Before recording starts this cancels the arm
    /// (the trigger was a modifier for that key, e.g. a capital letter).
    ///
    /// Only the Windows hook observes non-trigger keys — on Linux the binder
    /// reports the trigger alone — so this is genuinely unused there and would
    /// otherwise warn on every Linux build. Its tests still exercise it.
    #[cfg_attr(not(any(windows, test)), allow(dead_code))]
    pub fn on_other_key(&mut self, _now_ms: u64) {
        if !self.recording {
            self.armed_at = None;
        }
    }

    /// Time advanced. Starts recording once the trigger has been held alone
    /// for `threshold_ms` (never while the cancel latch is set).
    pub fn on_tick(&mut self, now_ms: u64) -> Option<Action> {
        if !self.recording && !self.cancelled {
            if let Some(t) = self.armed_at {
                if now_ms.saturating_sub(t) >= self.threshold_ms {
                    self.recording = true;
                    return Some(Action::StartRecording);
                }
            }
        }
        None
    }

    /// Trigger key up. Stops recording if it had started; otherwise no-op
    /// (a sub-threshold tap, or a release after a cancel). Always clears the
    /// cancel latch so the next press starts fresh.
    pub fn on_trigger_up(&mut self, _now_ms: u64) -> Option<Action> {
        let was_recording = self.recording;
        self.recording = false;
        self.armed_at = None;
        self.cancelled = false;
        if was_recording {
            Some(Action::StopRecording)
        } else {
            None
        }
    }

    /// Abort an in-progress dictation (Esc). Returns `Some(Cancel)` only when a
    /// recording was actually underway (so the sidecar is told to discard);
    /// while merely armed it disarms silently. Either way it latches `cancelled`
    /// so a still-held trigger can't immediately re-arm. A plain Esc with
    /// nothing in progress is a no-op and does NOT latch — the trigger isn't
    /// held to clear it, which would otherwise block the next dictation.
    pub fn cancel(&mut self) -> Option<Action> {
        if !self.recording && self.armed_at.is_none() {
            return None;
        }
        let was_recording = self.recording;
        self.recording = false;
        self.armed_at = None;
        self.cancelled = true;
        if was_recording {
            Some(Action::Cancel)
        } else {
            None
        }
    }

    /// Clear the cancel latch — used by the timer self-heal when it observes the
    /// trigger is physically up but we never saw the key-up event (it landed
    /// behind an elevated window).
    pub fn clear_cancelled(&mut self) {
        self.cancelled = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hold_alone_past_threshold_starts() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        assert_eq!(s.on_tick(100), None);
        assert_eq!(s.on_tick(400), Some(Action::StartRecording));
    }

    #[test]
    fn other_key_before_threshold_cancels() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        s.on_other_key(100); // capitalization, not dictation
        assert_eq!(s.on_tick(400), None);
    }

    #[test]
    fn release_after_recording_stops() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        assert_eq!(s.on_tick(400), Some(Action::StartRecording));
        assert_eq!(s.on_trigger_up(500), Some(Action::StopRecording));
    }

    #[test]
    fn release_before_threshold_is_no_action() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        assert_eq!(s.on_trigger_up(100), None);
    }

    #[test]
    fn key_repeat_does_not_reset_timer() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        s.on_trigger_down(200); // OS key-repeat; must not push the arm time out
        assert_eq!(s.on_tick(360), Some(Action::StartRecording));
    }

    #[test]
    fn other_key_during_recording_keeps_recording() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        assert_eq!(s.on_tick(400), Some(Action::StartRecording));
        s.on_other_key(450); // speaking while pressing keys must not stop it
        assert_eq!(s.on_trigger_up(500), Some(Action::StopRecording));
    }

    #[test]
    fn re_arm_after_stop_works() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        s.on_tick(400);
        s.on_trigger_up(500);
        // second dictation
        s.on_trigger_down(1000);
        assert_eq!(s.on_tick(1400), Some(Action::StartRecording));
    }

    #[test]
    fn cancel_while_recording_emits_cancel() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        assert_eq!(s.on_tick(400), Some(Action::StartRecording));
        assert_eq!(s.cancel(), Some(Action::Cancel));
        assert!(!s.is_recording());
    }

    #[test]
    fn cancel_when_idle_does_nothing_and_does_not_latch() {
        let mut s = PttState::new(350);
        // A plain Esc with nothing in progress must NOT latch — the trigger isn't
        // held, so the latch would never clear and would block the next dictation.
        assert_eq!(s.cancel(), None);
        assert!(!s.is_cancelled());
    }

    #[test]
    fn cancel_latches_so_a_held_trigger_cannot_restart_recording() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        assert_eq!(s.on_tick(400), Some(Action::StartRecording));
        s.cancel();
        assert!(s.is_cancelled());
        // The trigger is still physically held: ticks past threshold must not restart.
        assert_eq!(s.on_tick(800), None);
        // An OS auto-repeat keydown must not re-arm it either.
        s.on_trigger_down(820);
        assert_eq!(s.on_tick(1200), None);
    }

    #[test]
    fn release_after_cancel_clears_latch_and_emits_nothing() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        s.on_tick(400);
        s.cancel();
        // Releasing the already-aborted trigger must produce no StopRecording.
        assert_eq!(s.on_trigger_up(900), None);
        assert!(!s.is_cancelled());
        // A fresh dictation works again afterwards.
        s.on_trigger_down(1000);
        assert_eq!(s.on_tick(1400), Some(Action::StartRecording));
    }

    #[test]
    fn cancel_while_only_armed_disarms_without_emitting() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0); // armed, not yet recording
        assert_eq!(s.cancel(), None); // nothing captured yet -> no Cancel action
        assert!(s.is_cancelled()); // but latched: the key is held, don't arm
        assert_eq!(s.on_tick(400), None);
    }

    #[test]
    fn clear_cancelled_self_heals_a_missed_release() {
        let mut s = PttState::new(350);
        s.on_trigger_down(0);
        s.on_tick(400);
        s.cancel();
        assert!(s.is_cancelled());
        s.clear_cancelled(); // timer saw the trigger physically up though we missed the event
        assert!(!s.is_cancelled());
        s.on_trigger_down(1000);
        assert_eq!(s.on_tick(1400), Some(Action::StartRecording));
    }
}
