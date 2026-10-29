"""Regression tests for interrupted writes and stale raw inputs."""
import unittest
from unittest.mock import patch
import test_update
from update import apply, plan, UpdateError


class TransactionInterruptTests(unittest.TestCase):
    setUp = test_update.UpdateTests.setUp
    save = test_update.UpdateTests.save
    batch = test_update.UpdateTests.batch
    sample = test_update.UpdateTests.sample
    snapshot = test_update.UpdateTests.snapshot
    def test_keyboard_interrupt_restores_all_reference_bytes(self):
        before = self.snapshot()
        from update import atomic_write as original
        calls = 0
        def interrupted(path, text):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt('synthetic interrupt')
            original(path, text)
        with patch('update.atomic_write', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                apply(self.root, self.sample())
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / '.atlas-update.lock').exists())

    def test_system_exit_restores_all_reference_bytes(self):
        before = self.snapshot()
        from update import atomic_write as original
        calls = 0
        def interrupted(path, text):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SystemExit(2)
            original(path, text)
        with patch('update.atomic_write', side_effect=interrupted):
            with self.assertRaises(SystemExit):
                apply(self.root, self.sample())
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / '.atlas-update.lock').exists())

    def test_raw_change_after_plan_is_not_overwritten(self):
        batch = self.sample()
        path = self.root / 'references' / '2021-2026.json'
        expected = path.read_bytes() + b'\n'
        def changed(root, proposal, **options):
            result = plan(root, proposal, **options)
            path.write_bytes(expected)
            return result
        with patch('update.plan', side_effect=changed):
            with self.assertRaisesRegex(UpdateError, '基线|资料'):
                apply(self.root, batch)
        self.assertEqual(path.read_bytes(), expected)
