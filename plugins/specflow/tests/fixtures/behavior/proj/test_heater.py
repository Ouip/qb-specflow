import unittest

from heater import heater_on, set_target


class Range(unittest.TestCase):
    longMessage = False  # the failure prints only the message, so the verify map can match it exactly

    def test_rejects_95(self):
        self.assertEqual(set_target(95), "ERR_RANGE", "RANGE_REJECTS_95")


class Threshold(unittest.TestCase):
    def test_runs(self):
        heater_on(60, 57)  # calls the code but asserts nothing: a reference that does not test behavior


if __name__ == "__main__":
    unittest.main()
