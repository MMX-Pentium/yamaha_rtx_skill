# Yamaha RTX / FWX120 Agent Skill

YAMAHA RTX810〜RTX3510、RTX1200/3500/5000、および FWX120 向けの SSH・Telnet・シリアル共通操作 Skill と CLI ツールです。リポジトリ全体の概要・エージェントへの導入手順はルートディレクトリの [README.md](../README.md) を参照してください。

## クイックセットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp inventory.example.json inventory.json
# inventory.json に接続先を記入（パスワードは記載しない）
```

認証情報は環境変数で渡します（未設定機器は空可）。

```bash
export RTX_LOGIN_PASSWORD='<login_password>'
export RTX_ADMIN_PASSWORD='<admin_password>'
```

## 基本コマンド

```bash
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab probe
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab read 'show ip route'
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab read --admin 'show config list'
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab backup --output private/backup.cfg
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab plan --commands examples/change.txt --output private/change.plan.json
# 計画内容の明示承認後のみ:
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab apply --plan private/change.plan.json --approve <plan_id>
# 適用後の検証と別途保存承認後のみ:
.venv/bin/python scripts/rtx_agent.py --inventory inventory.json --device rtx810-lab save --receipt private/change.plan.receipt.json --approve <plan_id>
```

## 接続方式と文字コード

- `inventory.json` の `transport` に `"ssh"`（標準 22）、`"telnet"`（標準 23）、`"serial"` を指定します。
- YAMAHA RTX/FWX のコンソール出力は標準で **Shift_JIS**（`shift_jis`）です。`en.ascii` は `shift_jis` で互換デコード可能、`ja.utf8` 変更機は `"utf-8"` を指定してください。
- SSH は事前に別経路で指紋確認済みのホスト鍵を `known_hosts` に登録してください。Telnet は平文通信のため管理経路の保護に注意してください。

## ドキュメント・テスト

- Skill 本体: [SKILL.md](SKILL.md)
- YAMAHA CLI リファレンス（Cisco IOS 差分・コマンド早見表）: [references/yamaha-cli.md](references/yamaha-cli.md)
- ユニットテスト:
  ```bash
  python3 -m unittest discover -s tests -v
  ```
