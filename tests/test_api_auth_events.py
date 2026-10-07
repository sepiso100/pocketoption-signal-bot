import unittest

from pocket_signal_bot.adapters.api_adapter import _is_auth_success_event


class AuthSuccessEventTests(unittest.TestCase):
    def test_sdk_successauth_event_is_recognized(self):
        self.assertTrue(_is_auth_success_event("successauth"))

    def test_browser_auth_success_event_is_recognized(self):
        self.assertTrue(_is_auth_success_event("auth/success"))

    def test_other_events_do_not_authorize(self):
        for event in ("updateAssets", "auth/error", "disconnect"):
            with self.subTest(event=event):
                self.assertFalse(_is_auth_success_event(event))
