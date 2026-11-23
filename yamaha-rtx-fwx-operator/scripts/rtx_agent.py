#!/usr/bin/env python3
"""Yamaha RTX/FWX agent transport CLI (SSH / Telnet / Serial, default Shift_JIS)."""
from __future__ import annotations

import argparse
import codecs
import difflib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_RE = re.compile(r"\b(RTX(?:810|830|840|1200|1210|1220|1300|3500|3510|5000)|FWX120)\b", re.I)
REV_RE = re.compile(r"\bRev\.(\d+(?:\.\d+)+)", re.I)
SEC_CLASS_RE = re.compile(r"Security\s+Class\s*:\s*([^\r\n]+)", re.I)
PROMPT_RE = re.compile(r"(?:^|\n)[^\n\r]{0,70}[>#][ \t]*\Z")
MORE_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:---\s*more\s*---|(?<!-)--\s*more\s*--(?!-)|<more>|more\?|---\s*(?:つづく|続く)\s*---)[ \t]*\Z",
    re.I,
)
SAVE_PROMPT_RE = re.compile(r"\(Y/N\)[ \t]*\Z", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
REPORTING_DATE_RE = re.compile(r"^#\s*Reporting Date\s*:.*$", re.I)
ERROR_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"Error\s*:|"
    r"Invalid\s+(?:command|parameter)|"
    r"Illegal\s+(?:command|parameter)|"
    r"%\s*Error|"
    r"Too\s+(?:few|many)\s+arguments|"
    r"コマンド(?:が不正|名が不正|が見つかりません)|"
    r"不正な(?:コマンド|パラメータ|引数)|"
    r"引数が(?:足りません|多すぎます)|"
    r"管理者権限が必要|"
    r"エラー\s*[:：]"
    r")",
    re.I,
)
SAVE_OK_RE = re.compile(r"(?:Done|保存|Saved|CONFIG\d|セーブ中.*終了|終了)", re.I)
BLOCKED_RE = re.compile(
    r"^(?:"
    r"save(?:\s|$)|"
    r"restart(?:\s|$)|"
    r"reboot(?:\s|$)|"
    r"cold\s+start(?:\s|$)|"
    r"load(?:\s|$)|"
    r"erase(?:\s|$)|"
    r"clear(?:\s|$)|"
    r"delete(?:\s|$)|"
    r"copy\s+config(?:\s|$)|"
    r"quit(?:\s|$)|"
    r"exit(?:\s|$)|"
    r"(?:no\s+)?administrator(?:\s|$)|"
    r"(?:no\s+)?login(?:\s|$)|"
    r"(?:no\s+)?user\s+attribute(?:\s|$)|"
    r"(?:no\s+)?sshd(?:\s|$)"
    r")",
    re.I,
)
HIGH_RE = re.compile(
    r"(?:^|\s)(?:"
    r"no|"
    r"ip\s+lan\d+\s+address|"
    r"ip\s+route|"
    r"ipsec|"
    r"tunnel|"
    r"pp|"
    r"nat|"
    r"security\s+class|"
    r"firewall|"
    r"ip\s+filter|"
    r"ipv6\s+filter|"
    r"secure\s+filter|"
    r"console|"
    r"dhcp\s+scope|"
    r"dns\s+server|"
    r"telnetd|"
    r"httpd|"
    r"vlan|"
    r"lan\s+type"
    r")(?:\s|$)",
    re.I,
)
SENSITIVE_RE = re.compile(
    r"(?:password|pre-shared-key|secret|auth\s+(?:myname|username)|community|key\s+\S)",
    re.I,
)
PP_SELECT_RE = re.compile(r"^(?:(no)\s+)?pp\s+select(?:\s+(\S+))?$", re.I)
TUNNEL_SELECT_RE = re.compile(r"^(?:(no)\s+)?tunnel\s+select(?:\s+(\S+))?$", re.I)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_backspaces(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch == "\x08":
            if out and out[-1] != "\n":
                out.pop()
        else:
            out.append(ch)
    return "".join(out)


def clean_terminal_text(text: str) -> str:
    # ページング消去の '\r            \r' を先に除去する。
    normalized = re.sub(r"\r[ \t]+\r", "\n", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = ANSI_RE.sub("", normalized)
    normalized = resolve_backspaces(normalized)
    lines = [line for line in normalized.split("\n") if not MORE_RE.match(line)]
    return "\n".join(lines)


def strip_echo_and_prompt(text: str, command: str) -> str:
    cleaned = clean_terminal_text(text)
    lines = cleaned.split("\n")
    cmd_norm = command.strip()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and cmd_norm:
        first = lines[0].strip()
        if first == cmd_norm or first.endswith(f"> {cmd_norm}") or first.endswith(f"# {cmd_norm}"):
            lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and PROMPT_RE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip("\n")


def normalize_config_for_hash(config_text: str) -> str:
    cleaned = clean_terminal_text(config_text)
    lines: list[str] = []
    for line in cleaned.split("\n"):
        stripped = line.rstrip()
        if REPORTING_DATE_RE.match(stripped):
            continue
        if stripped == "show config":
            continue
        lines.append(stripped)
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if lines and PROMPT_RE.match(lines[-1]):
        lines.pop()
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def mask_command(command: str) -> str:
    if SENSITIVE_RE.search(command):
        parts = command.split()
        head = " ".join(parts[:3]) if len(parts) > 3 else parts[0]
        return f"{head} [REDACTED]"
    return command


def check_mojibake(text: str, *, strict: bool) -> bool:
    if "\ufffd" not in text:
        return False
    msg = (
        "出力に文字化け(\\ufffd)を検出しました。"
        "inventory.json の encoding と機器の console character を確認してください"
    )
    if strict:
        raise RuntimeError(msg)
    print(f"WARNING: {msg}", file=sys.stderr)
    return True


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    owned = True
    try:
        os.fchmod(fd, 0o600)
        f = os.fdopen(fd, "w", encoding="utf-8")
        owned = False
        with f:
            f.write(text)
    finally:
        if owned:
            try:
                os.close(fd)
            except OSError:
                pass


def json_private(path: Path, data: Any) -> None:
    write_private(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def parse_commands(text: str) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    risks: list[str] = []
    active_pp: str | None = None
    active_tunnel: str | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\n" in line or "\r" in line or any(ord(c) < 32 for c in line):
            raise ValueError(f"制御文字を含むコマンド: {lineno}")
        if BLOCKED_RE.search(line):
            raise ValueError(f"直接実行禁止コマンド (line {lineno}): {line.split()[0]}")
        m_pp = PP_SELECT_RE.match(line)
        if m_pp:
            if m_pp.group(1) or (m_pp.group(2) and m_pp.group(2).lower() == "none"):
                active_pp = None
            elif m_pp.group(2):
                active_pp = m_pp.group(2)
        m_tn = TUNNEL_SELECT_RE.match(line)
        if m_tn:
            if m_tn.group(1) or (m_tn.group(2) and m_tn.group(2).lower() == "none"):
                active_tunnel = None
            elif m_tn.group(2):
                active_tunnel = m_tn.group(2)
        commands.append(line)
        if HIGH_RE.search(line):
            risks.append(f"line {lineno}: {mask_command(line)}")
    if not commands:
        raise ValueError("コマンドがありません")
    if active_pp is not None:
        raise ValueError(f"pp select {active_pp} が解除されていません。末尾に 'pp select none' を追加してください")
    if active_tunnel is not None:
        raise ValueError(
            f"tunnel select {active_tunnel} が解除されていません。末尾に 'tunnel select none' を追加してください"
        )
    return commands, risks


def parse_identity(environment: str) -> dict[str, str]:
    match = MODEL_RE.search(environment)
    rev = REV_RE.search(environment)
    if not match or not rev:
        raise RuntimeError("機種またはファームウェアを判別できません。機種別対応を確認してから操作してください")
    result = {"model": match.group(1).upper(), "revision": rev.group(1)}
    sec = SEC_CLASS_RE.search(environment)
    if sec:
        result["security_class"] = sec.group(1).strip()
    return result


def config_diff_summary(before: str, after: str) -> dict[str, Any]:
    before_lines = normalize_config_for_hash(before).splitlines()
    after_lines = normalize_config_for_hash(after).splitlines()
    added: list[str] = []
    removed: list[str] = []
    for line in difflib.unified_diff(before_lines, after_lines, n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added.append(mask_command(line[1:]))
        elif line.startswith("-"):
            removed.append(mask_command(line[1:]))
    return {"added": added, "removed": removed}


def device_encoding(item: dict[str, Any]) -> str:
    codec = str(item.get("encoding", "shift_jis"))
    try:
        codecs.lookup(codec)
    except LookupError as e:
        raise ValueError(f"使用できない文字コードです: {codec}") from e
    return codec


class NetworkSession:
    def __init__(self, item: dict[str, Any], login_pw: str, admin_pw: str):
        try:
            from netmiko import ConnectHandler
        except ImportError as e:
            raise RuntimeError("pip install -r requirements.txt が必要です") from e
        transport = item["transport"]
        if transport not in {"ssh", "telnet"}:
            raise ValueError(f"未対応のネットワーク接続方式です: {transport}")
        if transport == "ssh" and not item.get("username"):
            raise ValueError("SSHにはユーザー名が必要です")
        self.transport = transport
        self.admin_pw = admin_pw
        self._in_admin = False
        # Yamaha RTX/FWXは通常 Shift_JIS。console character を変更した機器は inventory で上書きする。
        codec = device_encoding(item)
        kwargs: dict[str, Any] = dict(
            device_type="yamaha" if transport == "ssh" else "yamaha_telnet",
            host=item["host"],
            port=int(item.get("port", 22 if transport == "ssh" else 23)),
            username=item.get("username", ""),
            password=login_pw,
            secret=admin_pw,
            timeout=30,
            conn_timeout=15,
            auth_timeout=20,
            encoding=codec,
        )
        if transport == "ssh":
            # ホスト鍵を事前に確認して known_hosts に登録する。
            kwargs.update(ssh_strict=True, system_host_keys=True)
        else:
            # Netmiko の yamaha_telnet 既定 default_enter='\n' は RTX 実機で無視されるため CRLF を使う。
            kwargs["default_enter"] = "\r\n"
        # Netmiko の YamahaBase.session_preparation() は 'console lines infinity' を送信し
        # 機器の running-config を意図せず変更してしまうため、disable_paging を無効化してから接続する。
        kwargs["auto_connect"] = False
        self.conn = ConnectHandler(**kwargs)
        if hasattr(self.conn, "_open"):
            self.conn.disable_paging = lambda *a, **k: ""
            self.conn._open()

    def _enable(self) -> None:
        if self._in_admin:
            return
        # テスト用モック等、低レベルチャネルAPIを持たない場合は Netmiko 標準の enable() にフォールバック。
        if not (hasattr(self.conn, "write_channel") and hasattr(self.conn, "read_until_pattern")):
            self.conn.enable()
            self._in_admin = True
            return
        ret = getattr(self.conn, "RETURN", "\r\n")
        self.conn.clear_buffer()
        self.conn.write_channel(ret)
        cur = self.conn.read_until_pattern(pattern=r"[>#][ \t]*$", read_timeout=10)
        if re.search(r"#[ \t]*$", cur):
            self._in_admin = True
            return
        self.conn.write_channel("administrator" + ret)
        out = self.conn.read_until_pattern(
            pattern=r"(?:Password\s*:|[>#][ \t]*$)", re_flags=re.I, read_timeout=15
        )
        if ERROR_RE.search(out):
            raise RuntimeError(f"管理者モードへの移行が拒否されました: {clean_terminal_text(out).strip()}")
        if re.search(r"Password\s*:", out, re.I):
            self.conn.write_channel(self.admin_pw + ret)
            out = self.conn.read_until_pattern(
                pattern=r"(?:Password\s*:|[>#][ \t]*$)", re_flags=re.I, read_timeout=15
            )
        if ERROR_RE.search(out) or re.search(r"Password\s*:", out, re.I) or not re.search(r"#[ \t]*$", out):
            raise RuntimeError(f"管理者モードへの移行に失敗しました: {clean_terminal_text(out).strip()}")
        self._in_admin = True

    def run(self, command: str, admin: bool = False) -> str:
        if admin:
            self._enable()
        # テスト用モック等、低レベルチャネルAPIを持たない場合は send_command にフォールバック。
        if not (hasattr(self.conn, "write_channel") and hasattr(self.conn, "read_channel")):
            raw = str(
                self.conn.send_command(
                    command,
                    expect_string=r"(?:^|\n)[^\n\r]{0,70}[>#][ \t]*\Z",
                    read_timeout=120,
                )
            )
            return clean_terminal_text(raw)
        ret = getattr(self.conn, "RETURN", "\r\n")
        self.conn.clear_buffer()
        self.conn.write_channel(command + ret)
        collected = ""
        deadline = time.monotonic() + 120.0
        prompt_since: float | None = None
        while time.monotonic() < deadline:
            chunk = self.conn.read_channel()
            if chunk:
                prompt_since = None
                collected += chunk
                raw_norm = ANSI_RE.sub("", re.sub(r"\r[ \t]+\r", "\n", collected))
                raw_norm = raw_norm.replace("\r\n", "\n").replace("\r", "\n")
                raw_last = raw_norm.rsplit("\n", 1)[-1]
                if MORE_RE.search(raw_last):
                    collected = raw_norm.rsplit("\n", 1)[0] + "\n" if "\n" in raw_norm else ""
                    self.conn.write_channel(" ")
                    continue
                if PROMPT_RE.search(raw_last):
                    prompt_since = time.monotonic()
            else:
                if prompt_since is not None and (time.monotonic() - prompt_since) >= 0.12:
                    return strip_echo_and_prompt(collected, command)
                time.sleep(0.02)
        raise TimeoutError("ネットワークコンソール応答がタイムアウトしました。コマンドを再送する前に状態を確認してください")

    def close(self) -> None:
        try:
            if self._in_admin and hasattr(self.conn, "write_channel") and hasattr(self.conn, "read_channel"):
                ret = getattr(self.conn, "RETURN", "\r\n")
                self.conn.write_channel("exit" + ret)
                time.sleep(0.4)
                out = self.conn.read_channel()
                if SAVE_PROMPT_RE.search(out) or "(Y/N)" in out:
                    # 自動保存は厳禁。必ず N を応答する。
                    self.conn.write_channel("N" + ret)
                    time.sleep(0.3)
        except Exception:
            pass
        finally:
            self.conn.disconnect()


SSHSession = NetworkSession  # 従来のクラス名からの参照互換（接続方式はinventoryで判定）


class SerialSession:
    def __init__(self, item: dict[str, Any], login_pw: str, admin_pw: str):
        try:
            import serial
        except ImportError as e:
            raise RuntimeError("pip install -r requirements.txt が必要です") from e
        self.admin_pw = admin_pw
        self._in_admin = False
        self.ser = serial.Serial(
            port=item["port"],
            baudrate=int(item.get("baudrate", 9600)),
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=True,
            rtscts=False,
            timeout=0.1,
            write_timeout=5,
        )
        self.codec = device_encoding(item)
        self.decoder = codecs.getincrementaldecoder(self.codec)(errors="replace")
        self.ser.reset_input_buffer()
        self._login(item.get("username", ""), login_pw)

    def _write(self, value: str) -> None:
        self.ser.write((value + "\r").encode(self.codec, errors="replace"))
        self.ser.flush()

    def _read_until(
        self,
        timeout: float = 30,
        allow_password: bool = False,
        allow_save_prompt: bool = False,
        quiesce_sec: float = 0.14,
    ) -> str:
        deadline = time.monotonic() + timeout
        collected = ""
        prompt_since: float | None = None
        while time.monotonic() < deadline:
            waiting = getattr(self.ser, "in_waiting", 0)
            raw = self.ser.read(max(1, waiting))
            if raw:
                prompt_since = None
                # Shift_JISの2バイト文字がread境界で分割されても壊さない。
                collected += self.decoder.decode(raw, final=False)
                raw_norm = ANSI_RE.sub("", re.sub(r"\r[ \t]+\r", "\n", collected))
                raw_norm = raw_norm.replace("\r\n", "\n").replace("\r", "\n")
                last_line = raw_norm.rsplit("\n", 1)[-1]
                if allow_save_prompt and SAVE_PROMPT_RE.search(last_line):
                    return clean_terminal_text(collected)
                if allow_password and re.search(r"(?:Password|Username)\s*:[ \t]*\Z", last_line, re.I):
                    return clean_terminal_text(collected)
                if MORE_RE.search(last_line):
                    # ページング行を除去し、スペースを1回だけ送信する。
                    collected = raw_norm.rsplit("\n", 1)[0] + "\n" if "\n" in raw_norm else ""
                    self.ser.write(b" ")
                    self.ser.flush()
                    continue
                if PROMPT_RE.search(last_line):
                    prompt_since = time.monotonic()
            else:
                if prompt_since is not None and (time.monotonic() - prompt_since) >= quiesce_sec:
                    return clean_terminal_text(collected)
                time.sleep(0.02)
        raise TimeoutError("コンソール応答がタイムアウトしました。コマンドを再送する前に状態を確認してください")

    def _login(self, user: str, password: str) -> None:
        self._write("")
        chunk = self._read_until(20, allow_password=True, allow_save_prompt=True)
        sent_empty_for_user = False
        saw_username = False
        auth_attempts = 0
        for _ in range(6):
            last_line = chunk.rsplit("\n", 1)[-1]
            if SAVE_PROMPT_RE.search(last_line):
                # 前回セッションが保存確認プロンプトで残っていた場合は保存せず N で抜ける。
                self._write("N")
                chunk = self._read_until(15, allow_password=True, allow_save_prompt=True)
                continue
            if PROMPT_RE.search(last_line):
                self._in_admin = bool(re.search(r"#[ \t]*$", last_line))
                return
            if re.search(r"Username\s*:[ \t]*$", last_line, re.I):
                if not user:
                    raise RuntimeError("名前ありユーザーが必要です。inventoryのusernameを設定してください")
                saw_username = True
                self._write(user)
            elif re.search(r"Password\s*:[ \t]*$", last_line, re.I):
                if user and not saw_username and not sent_empty_for_user:
                    # 匿名ログイン有効な機器では空EnterでUsername入力へ切り替わる。
                    sent_empty_for_user = True
                    self._write("")
                else:
                    auth_attempts += 1
                    if auth_attempts > 2:
                        raise RuntimeError("シリアル認証に失敗しました。ユーザー名とパスワードを確認してください")
                    self._write(password)
            else:
                raise RuntimeError(f"不明なログイン応答です。手動でシリアル設定を確認してください: {last_line!r}")
            chunk = self._read_until(20, allow_password=True, allow_save_prompt=True)
        raise RuntimeError("シリアルログインに失敗しました")

    def _enable(self) -> None:
        if self._in_admin:
            return
        self._write("")
        current = self._read_until(10)
        if re.search(r"#[ \t]*$", current.rsplit("\n", 1)[-1]):
            self._in_admin = True
            return
        self._write("administrator")
        data = self._read_until(20, allow_password=True)
        if ERROR_RE.search(data):
            raise RuntimeError(f"管理者モードへの移行が拒否されました: {data.strip()}")
        if re.search(r"Password\s*:[ \t]*$", data.rsplit("\n", 1)[-1], re.I):
            self._write(self.admin_pw)
            data = self._read_until(20, allow_password=True)
        last_line = data.rsplit("\n", 1)[-1]
        if ERROR_RE.search(data) or re.search(r"Password\s*:[ \t]*$", last_line, re.I) or not re.search(r"#[ \t]*$", last_line):
            raise RuntimeError(f"管理者モードへの移行に失敗しました: {data.strip()}")
        self._in_admin = True

    def run(self, command: str, admin: bool = False) -> str:
        if admin:
            self._enable()
        self._write(command)
        raw = self._read_until(120)
        return strip_echo_and_prompt(raw, command)

    def close(self) -> None:
        try:
            if self._in_admin:
                self._write("exit")
                out = self._read_until(5, allow_save_prompt=True)
                if SAVE_PROMPT_RE.search(out.rsplit("\n", 1)[-1]):
                    # 自動保存は厳禁。必ず N を応答する。
                    self._write("N")
                    self._read_until(5)
                self._in_admin = False
            self._write("exit")
            time.sleep(0.2)
        except Exception:
            pass
        finally:
            self.ser.close()


def connect(item: dict[str, Any]):
    login_pw = os.environ.get("RTX_LOGIN_PASSWORD", "")
    admin_pw = os.environ.get("RTX_ADMIN_PASSWORD", "")
    # ヤマハではログイン/管理パスワード未設定もあり得るため、空文字自体は拒否しない。
    # ただし本番運用ではパスワードと管理アクセスを適切に保護すること。
    if item["transport"] in {"ssh", "telnet"}:
        return NetworkSession(item, login_pw, admin_pw)
    if item["transport"] == "serial":
        return SerialSession(item, login_pw, admin_pw)
    raise ValueError("transport は ssh、telnet または serial を指定してください")


def device_data(inventory: str, device: str) -> dict[str, Any]:
    data = json.loads(Path(inventory).read_text(encoding="utf-8"))
    try:
        result = data["devices"][device]
    except KeyError as e:
        raise ValueError("指定されたデバイスがinventoryに見つかりません") from e
    if result.get("transport") not in {"ssh", "telnet", "serial"}:
        raise ValueError("transport は ssh、telnet または serial です")
    device_encoding(result)
    return result


def identity(session) -> tuple[dict[str, str], str]:
    env_out = session.run("show environment")
    return parse_identity(env_out), env_out


def config(session) -> str:
    return session.run("show config", admin=True)


def validate_read_command(command: str) -> str:
    cmd = command.strip()
    if not cmd or any(ord(c) < 32 for c in cmd):
        raise ValueError("readコマンドが空または制御文字を含みます")
    if not re.match(r"^show(?:\s|$)", cmd, re.I):
        raise ValueError("readはshow系コマンドに限定しています")
    if re.match(r"^show\s+config(?:\s+\d+|\s*$)", cmd, re.I):
        raise ValueError("show config は backup サブコマンドを利用してください")
    if re.search(r"\bconfig\b", cmd, re.I) and not re.match(r"^show\s+config\s+list(?:\s|$)", cmd, re.I):
        raise ValueError("show config は backup サブコマンドを利用してください")
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RTX/FWX SSH/Telnet/シリアル自動操作CLI（標準文字コードShift_JIS）"
    )
    parser.add_argument("--inventory", default="inventory.json")
    parser.add_argument("--device", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("probe")
    sub.add_parser("backup").add_argument("--output", required=True)
    reader = sub.add_parser("read")
    reader.add_argument("command", help="show コマンドのみ")
    reader.add_argument("--admin", action="store_true", help="管理者権限が必要なshowコマンドを実行")
    planner = sub.add_parser("plan")
    planner.add_argument("--commands", required=True)
    planner.add_argument("--output", required=True)
    applying = sub.add_parser("apply")
    applying.add_argument("--plan", required=True)
    applying.add_argument("--approve", required=True, help="ユーザー承認後に計画IDを指定")
    applying.add_argument("--serial-recovery-confirmed", action="store_true")
    saving = sub.add_parser("save")
    saving.add_argument("--receipt", required=True)
    saving.add_argument("--approve", required=True)
    args = parser.parse_args()

    item = device_data(args.inventory, args.device)
    session = connect(item)
    try:
        found, env_out = identity(session)
        mojibake = check_mojibake(env_out, strict=args.action in {"apply", "save"})
        if args.action == "probe":
            print(
                json.dumps(
                    {
                        **found,
                        "transport": item["transport"],
                        "encoding": device_encoding(item),
                        "mojibake_detected": mojibake,
                    },
                    ensure_ascii=False,
                )
            )
        elif args.action == "read":
            cmd = validate_read_command(args.command)
            out = session.run(cmd, admin=args.admin)
            check_mojibake(out, strict=False)
            print(out)
        elif args.action == "backup":
            snapshot = config(session)
            check_mojibake(snapshot, strict=False)
            path = Path(args.output).resolve()
            write_private(path, snapshot + ("\n" if not snapshot.endswith("\n") else ""))
            print(
                json.dumps(
                    {
                        "backup": str(path),
                        "sha256": sha(normalize_config_for_hash(snapshot)),
                        **found,
                    },
                    ensure_ascii=False,
                )
            )
        elif args.action == "plan":
            commands, risks = parse_commands(Path(args.commands).read_text(encoding="utf-8"))
            baseline = config(session)
            check_mojibake(baseline, strict=True)
            plan_path = Path(args.output).resolve()
            backup_path = plan_path.with_suffix(".before.cfg")
            write_private(backup_path, baseline + ("\n" if not baseline.endswith("\n") else ""))
            plan = {
                "schema": 2,
                "device": args.device,
                "transport": item["transport"],
                "model": found["model"],
                "revision": found["revision"],
                "baseline_sha256": sha(normalize_config_for_hash(baseline)),
                "backup": str(backup_path),
                "commands": commands,
                "high_risk": bool(risks),
                "risk_reasons": risks,
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }
            plan["plan_id"] = sha(json.dumps(plan, sort_keys=True, ensure_ascii=False))[:16]
            json_private(plan_path, plan)
            print(
                json.dumps(
                    {
                        "plan": str(plan_path),
                        "plan_id": plan["plan_id"],
                        "model": found["model"],
                        "revision": found["revision"],
                        "command_count": len(commands),
                        "masked_commands": [mask_command(c) for c in commands],
                        "high_risk": bool(risks),
                        "risk_reasons": risks,
                        "backup": str(backup_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.action == "apply":
            plan_path = Path(args.plan).resolve()
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if plan["device"] != args.device or plan["transport"] != item["transport"]:
                raise RuntimeError("計画の対象機器/接続方式が一致しません")
            if plan["plan_id"] != args.approve:
                raise RuntimeError("承認IDが一致しません")
            if plan["model"] != found["model"] or plan["revision"] != found["revision"]:
                raise RuntimeError("機種またはファームウェアが計画時と異なります")
            actual_id = sha(
                json.dumps({k: v for k, v in plan.items() if k != "plan_id"}, sort_keys=True, ensure_ascii=False)
            )[:16]
            if plan["plan_id"] != actual_id:
                raise RuntimeError("計画ファイルが改ざんされています")
            checked, risks = parse_commands("\n".join(plan["commands"]))
            if checked != plan["commands"] or bool(risks) != bool(plan["high_risk"]):
                raise RuntimeError("計画内のコマンド分類が一致しません")
            if plan["high_risk"] and not args.serial_recovery_confirmed:
                raise RuntimeError("高リスク変更にはシリアル復旧経路の実在確認が必要です")
            before = config(session)
            check_mojibake(before, strict=True)
            if sha(normalize_config_for_hash(before)) != plan["baseline_sha256"]:
                raise RuntimeError("Configが計画時から変更されています。再計画してください")
            completed = 0
            for command in plan["commands"]:
                result = session.run(command, admin=True)
                check_mojibake(result, strict=True)
                if ERROR_RE.search(result):
                    detail = result.strip().splitlines()[:3]
                    raise RuntimeError(
                        f"コマンド {completed + 1} ({mask_command(command)}) でCLIエラーを検出: "
                        f"{' | '.join(detail)}。途中まで変更された可能性があります。内容を再確認してください"
                    )
                completed += 1
            after = config(session)
            check_mojibake(after, strict=True)
            diff = config_diff_summary(before, after)
            receipt = {
                "device": args.device,
                "plan_id": plan["plan_id"],
                "model": found["model"],
                "revision": found["revision"],
                "post_sha256": sha(normalize_config_for_hash(after)),
                "saved": False,
                "applied_utc": datetime.now(timezone.utc).isoformat(),
            }
            path = plan_path.with_suffix(".receipt.json")
            json_private(path, receipt)
            print(
                json.dumps(
                    {
                        "status": "APPLIED_NOT_SAVED",
                        "completed": completed,
                        "diff": diff,
                        "receipt": str(path),
                        "save_approval_id": plan["plan_id"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.action == "save":
            receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
            if receipt["device"] != args.device or receipt["plan_id"] != args.approve:
                raise RuntimeError("承認IDまたは対象機器が一致しません")
            if receipt["model"] != found["model"] or receipt["revision"] != found["revision"]:
                raise RuntimeError("対象機器が変更されています")
            current_cfg = config(session)
            check_mojibake(current_cfg, strict=True)
            if sha(normalize_config_for_hash(current_cfg)) != receipt["post_sha256"]:
                raise RuntimeError("適用後Configから差分があります。saveを中止します")
            output = session.run("save", admin=True)
            check_mojibake(output, strict=True)
            if ERROR_RE.search(output) or not SAVE_OK_RE.search(output):
                raise RuntimeError(
                    f"保存完了を確認できません。機器の状態を確認してください: {output.strip()}"
                )
            receipt["saved"] = True
            receipt["saved_utc"] = datetime.now(timezone.utc).isoformat()
            json_private(Path(args.receipt), receipt)
            print(
                json.dumps(
                    {"status": "SAVED", "device": args.device, "receipt": str(Path(args.receipt))},
                    ensure_ascii=False,
                )
            )
    finally:
        session.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, TimeoutError, OSError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
