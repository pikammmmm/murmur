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
}

#[derive(Debug)]
pub struct PttState {
    threshold_ms: u64,
    armed_at: Option<u64>,
    recording: bool,
}

impl PttState {
    pub fn new(threshold_ms: u64) -> Self {
        Self { threshold_ms, armed_at: None, recording: false }
    }

    pub fn is_recording(&self) -> bool {
        self.recording
    }

    pub fn is_armed(&self) -> bool {
        self.armed_at.is_some()
    }

    pub fn set_threshold(&mut self, threshold_ms: u64) {
        self.threshold_ms = threshold_ms;
    }

    /// Trigger key down. Arms the timer; key-repeat does not reset it.
    pub fn on_trigger_down(&mut self, now_ms: u64) {
        if self.recording {
            return;
        }
        if self.armed_at.is_none() {
            self.armed_at = Some(now_ms);
        }
    }

    /// Any non-trigger key down. Before recording starts this cancels the arm
    /// (the trigger was a modifier for that key, e.g. a capital letter).
    pub fn on_other_key(&mut self, _now_ms: u64) {
        if !self.recording {
            self.armed_at = None;
        }
    }

    /// Time advanced. Starts recording once the trigger has been held alone
    /// for `threshold_ms`.
    pub fn on_tick(&mut self, now_ms: u64) -> Option<Action> {
        if !self.recording {
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
    /// (a sub-threshold tap).
    pub fn on_trigger_up(&mut self, _now_ms: u64) -> Option<Action> {
        let was_recording = self.recording;
        self.recording = false;
        self.armed_at = None;
        if was_recording {
            Some(Action::StopRecording)
        } else {
            None
        }
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
}
