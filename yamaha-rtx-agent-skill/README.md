# Yamaha RTX / FWX120 Agent Skill

YAMAHA RTX810〜RTX3510、RTX1200/3500/5000、およびFWX120向けの、SSH・Telnet・シリアル共通操作SkillとCLIツールです。Claude Code / OpenCode / Codex / Antigravity 等の Agent Skills 対応環境で `SKILL.md` を参照させます。

## インストール

1. Python 3.10+ を用意。
2. 仮想環境を作成し依存関係をインストール:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. `cp inventory.example.json inventory.json` し、ホスト名/シリアルデバイスを編集。
4. SSHホスト鍵は**別経路で指紋を確認**してから `known_hosts` に登録。
5. ログイン/管理パスワードが設定されている機器では `RTX_LOGIN_PASSWORD` と `RTX_ADMIN_PASSWORD` を安全な手段で環境変数に設定（未設定機器は空可）。
6. 初回は検証機で `probe` → `read` → `backup` の順に確認:

```bash
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab probe
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-telnet probe
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device fwx120-console read 'show ip route'
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device fwx120-console read --admin 'show config list'
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab backup --output private/backup.cfg
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab plan --commands examples/change.txt --output private/change.plan.json
# 計画内容の明示承認を得た後のみ:
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab apply --plan private/change.plan.json --approve <plan_id>
# 適用後の検証と別途保存承認を得た後のみ:
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab save --receipt private/change.plan.receipt.json --approve <plan_id>
```

## 接続方式と文字コード

`inventory.json` の `transport` に `"ssh"`（標準ポート22）、`"telnet"`（標準ポート23）、`"serial"` を指定します。**Telnetの利用制限は設けていません。** Telnetでも `probe` / `read` / `backup` / `plan` / `apply` / `save` の全操作が利用可能ですが、通信は平文のため経路管理に注意してください。

**YAMAHA RTX/FWXのコンソール出力は標準でShift_JIS**です。SSH/Telnet/シリアルの全てで `encoding` の既定値は `shift_jis` です（`ja.utf8` 変更機は `"utf-8"` 等に変更）。`en.ascii` は `shift_jis` でも互換デコード可能です。

## Cisco系との重要なCLI差分

- 一般ユーザー `>` → `administrator` → 管理者 `#`。Cisco IOSの `enable` / `configure terminal` / `write memory` は使用しない。
- 設定コマンドは `#` で直接入力し、即座に稼働状態へ反映される。`save` は不揮発性メモリへの永続化であり `commit` ではない。
- `pp select N` / `tunnel select N` で後続コマンドの対象を切替。必ず `pp select none` / `tunnel select none` で解除する。
- `no ...` は設定解除・初期値復帰。`no pp select` / `no tunnel select` は選択解除であり設定削除ではない。
- `show config` は秘密情報を含むため `backup` で `0600` ファイルに保存する。

詳細は `references/yamaha-cli.md` を参照してください。

## テスト

```bash
python3 -m unittest discover -s tests -v
```

## 注意

- `plan` にパスワードや共有鍵を含めると、`0600` 権限の計画ファイルに平文で含まれます。保存先を適切に保護し、チャット等に生内容を出力しないでください。
- `apply` は非トランザクションであり途中失敗後に自動復旧しません。通信断リスクがある変更は必ずシリアル復旧手段を準備してください。
- シリアルは TTY 権限とボーレート（初期値 9600/8N1/XON-XOFF）に注意してください。
- RTX810 / FWX120 はSSH公開鍵認証非対応、RTX830等はファームウェア依存です。
