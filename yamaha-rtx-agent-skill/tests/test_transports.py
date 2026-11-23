"""Transport/encoding tests; no physical device required."""
import codecs
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "scripts" / "rtx_agent.py"
spec = importlib.util.spec_from_file_location("rtx_agent_transports", FILE)
rtx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtx)


class FakeConnection:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.enabled = False
        self.commands = []

    def enable(self):
        self.enabled = True

    def send_command(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return "正常に処理しました"

    def disconnect(self):
        return None


class TransportTests(unittest.TestCase):
    def test_inventory_accepts_ssh_telnet_serial(self):
        sample = {
            "devices": {
                "s": {"transport": "ssh", "host": "192.0.2.1", "username": "test"},
                "t": {"transport": "telnet", "host": "192.0.2.2"},
                "r": {"transport": "serial", "port": "/dev/ttyUSB0"},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            f = Path(directory) / "inventory.json"
            f.write_text(json.dumps(sample), encoding="utf-8")
            for key in ("s", "t", "r"):
                self.assertEqual(rtx.device_data(str(f), key)["transport"], sample["devices"][key]["transport"])

    def test_default_encoding_for_all_transports(self):
        for transport in ("ssh", "telnet", "serial"):
            self.assertEqual(rtx.device_encoding({"transport": transport}), "shift_jis")
        self.assertEqual(rtx.device_encoding({"encoding": "utf-8"}), "utf-8")
        with self.assertRaises(ValueError):
            rtx.device_encoding({"encoding": "this_is_not_an_encoding"})

    def test_telnet_driver_full_operations_with_shift_jis(self):
        instances = []

        def connect_handler(**kwargs):
            instance = FakeConnection(**kwargs)
            instances.append(instance)
            return instance

        fake = types.ModuleType("netmiko")
        fake.ConnectHandler = connect_handler
        with patch.dict(sys.modules, {"netmiko": fake}):
            client = rtx.NetworkSession({"transport": "telnet", "host": "192.0.2.2"}, "", "admin")
            self.assertEqual(instances[0].kwargs["device_type"], "yamaha_telnet")
            self.assertEqual(instances[0].kwargs["port"], 23)
            self.assertEqual(instances[0].kwargs["encoding"], "shift_jis")
            self.assertEqual(instances[0].kwargs["default_enter"], "\r\n")
            self.assertNotIn("ssh_strict", instances[0].kwargs)
            self.assertEqual(client.run("show environment"), "正常に処理しました")
            self.assertEqual(client.run("save", admin=True), "正常に処理しました")
            self.assertTrue(instances[0].enabled)
            self.assertEqual(instances[0].commands[-1][1]["expect_string"], r"(?:^|\n)[^\n\r]{0,70}[>#][ \t]*\Z")
            client.close()

    def test_ssh_driver_hostkey_and_shift_jis(self):
        fake = types.ModuleType("netmiko")
        captured = []
        fake.ConnectHandler = lambda **kwargs: captured.append(kwargs) or FakeConnection(**kwargs)
        with patch.dict(sys.modules, {"netmiko": fake}):
            s = rtx.NetworkSession({"transport": "ssh", "host": "192.0.2.1", "username": "op"}, "login", "admin")
            self.assertEqual(captured[0]["device_type"], "yamaha")
            self.assertTrue(captured[0]["ssh_strict"])
            self.assertTrue(captured[0]["system_host_keys"])
            self.assertEqual(captured[0]["encoding"], "shift_jis")
            self.assertNotIn("default_enter", captured[0])
            s.close()

    def test_shift_jis_incremental_decoder_split(self):
        text = "設定変更済み"
        raw = text.encode("shift_jis")
        decoder = codecs.getincrementaldecoder(rtx.device_encoding({}))(errors="replace")
        value = "".join(decoder.decode(bytes([b]), final=False) for b in raw)
        self.assertEqual(value, text)

    def test_yamaha_pagination_marker(self):
        self.assertIsNotNone(rtx.MORE_RE.search("--- つづく ---"))
        self.assertIsNotNone(rtx.MORE_RE.search("--- 続く ---"))
        self.assertIsNotNone(rtx.MORE_RE.search("---more---"))
        self.assertIsNone(rtx.MORE_RE.search("normal output ---more--- trailing"))

    def test_strip_echo_and_prompt(self):
        raw = "show ip route \x08 \x08\r\nDestination Gateway\r\n192.168.100.0/24 LAN1\r\n> "
        self.assertEqual(
            rtx.strip_echo_and_prompt(raw, "show ip route"),
            "Destination Gateway\n192.168.100.0/24 LAN1",
        )


if __name__ == "__main__":
    unittest.main()
