import importlib.util
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "scripts" / "rtx_agent.py"
spec = importlib.util.spec_from_file_location("rtx_agent", FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Tests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(
            m.parse_identity("RTX810 Rev.11.01.33 (test)"),
            {"model": "RTX810", "revision": "11.01.33"},
        )
        self.assertEqual(
            m.parse_identity("FWX120 Rev.11.03.29"),
            {"model": "FWX120", "revision": "11.03.29"},
        )
        env = (
            "RTX830 Rev.15.02.33 (Tue Jul 15 12:21:51 2025)\n"
            "Security Class: 1, FORGET: ON, TELNET: OFF, SSH: OFF\n"
        )
        self.assertEqual(
            m.parse_identity(env),
            {
                "model": "RTX830",
                "revision": "15.02.33",
                "security_class": "1, FORGET: ON, TELNET: OFF, SSH: OFF",
            },
        )

    def test_block(self):
        for command in (
            "save",
            "restart",
            "cold start",
            "load config0",
            "administrator",
            "no administrator password",
            "login user bob test",
            "no login user bob",
            "user attribute test connection=serial",
            "sshd service on",
            "no sshd host",
        ):
            with self.assertRaises(ValueError, msg=command):
                m.parse_commands(command)

    def test_risk(self):
        cmds, risks = m.parse_commands("description 1 lab\nip lan1 address 192.0.2.1/24")
        self.assertEqual(len(cmds), 2)
        self.assertTrue(risks)
        cmds2, risks2 = m.parse_commands("ip route 10.250.250.0/24 gateway 192.168.100.254")
        self.assertEqual(len(cmds2), 1)
        self.assertTrue(risks2)

    def test_balanced_select_context(self):
        cmds, risks = m.parse_commands("pp select 1\npp auth accept pap chap\npp select none")
        self.assertEqual(len(cmds), 3)
        self.assertTrue(risks)
        with self.assertRaises(ValueError):
            m.parse_commands("pp select 1\npp auth accept pap chap")
        with self.assertRaises(ValueError):
            m.parse_commands("tunnel select 1\ntunnel encapsulation ipsec")
        cmds2, _ = m.parse_commands("tunnel select 1\nno tunnel select")
        self.assertEqual(len(cmds2), 2)

    def test_normalize_config_ignores_reporting_date(self):
        cfg1 = (
            "show config \x08 \x08\r\n"
            "# RTX830 Rev.15.02.33\r\n"
            "# Reporting Date: Nov 23 22:57:23 2026\r\n"
            "ip lan1 address 192.168.100.1/24\r\n"
            "# "
        )
        cfg2 = (
            "# RTX830 Rev.15.02.33\n"
            "# Reporting Date: Nov 23 22:58:09 2026\n"
            "ip lan1 address 192.168.100.1/24\n"
        )
        self.assertEqual(
            m.sha(m.normalize_config_for_hash(cfg1)),
            m.sha(m.normalize_config_for_hash(cfg2)),
        )

    def test_validate_read_command(self):
        self.assertEqual(m.validate_read_command("show ip route"), "show ip route")
        self.assertEqual(m.validate_read_command("show config list"), "show config list")
        for bad in ("show config", "show config 0", "restart", "show ip route\nrestart"):
            with self.assertRaises(ValueError, msg=bad):
                m.validate_read_command(bad)

    def test_prompt_and_error_and_save_regex(self):
        self.assertIsNone(m.PROMPT_RE.search("#\n"))
        self.assertIsNotNone(m.PROMPT_RE.search("# "))
        self.assertIsNotNone(m.PROMPT_RE.search("pp1# "))
        self.assertIsNotNone(m.ERROR_RE.search("Error: Illegal command name"))
        self.assertIsNotNone(m.ERROR_RE.search("エラー: コマンド名が不正です"))
        self.assertIsNotNone(m.SAVE_OK_RE.search("Saving ... CONFIG0 Done"))
        self.assertIsNotNone(m.SAVE_OK_RE.search("セーブ中... CONFIG0 終了"))

    def test_mask_command(self):
        self.assertEqual(m.mask_command("description 1 lab"), "description 1 lab")
        self.assertIn("[REDACTED]", m.mask_command("ipsec ike pre-shared-key 1 text mysecret"))

    def test_digest(self):
        self.assertEqual(m.sha("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


if __name__ == "__main__":
    unittest.main()
