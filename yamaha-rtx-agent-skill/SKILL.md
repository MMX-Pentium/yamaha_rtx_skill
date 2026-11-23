---
name: yamaha-rtx-fwx-operator
description: Operate Yamaha RTX routers (RTX810 onward including legacy and current models) and FWX120 via SSH, Telnet or serial console; default Shift_JIS decoding, Yamaha-specific CLI semantics, inspection, backup, and approved config changes.
---

# Yamaha RTX / FWX120 実機操作 Skill

## 対象と前提

対象機種: RTX810、RTX830、RTX840、RTX1200、RTX1210、RTX1220、RTX1300、RTX3500、RTX3510、RTX5000、FWX120。**接続可能であることと、その機種・リビジョンが特定コマンドをサポートすることは別**。新規機種はモデル判定と公式リファレンスを更新し、試験するまで書き込みを禁止する。

接続方式は **SSH / Telnet（Netmiko Yamaha ドライバ）/ USB・RS-232C シリアル（pyserial）** を使用する。**Telnetの接続・状態取得・設定変更・保存にSkill固有の禁止・接続先制限を設けない**。`transport: "telnet"` で通常操作を行える。Telnetは認証情報・設定内容・出力が暗号化されないため、接続経路は管理者が把握し適切に扱う。

## 依存環境と初期セットアップ

1. Python 3.10+（仮想環境 `.venv/bin/python` がある場合はそちらを優先、なければ `python3`）で `python3 -m pip install -r requirements.txt`。
2. `inventory.example.json` を `inventory.json` にコピーし、対象を登録する（`inventory.json` や `private/` は Git にコミットしない）。
3. 秘密情報は `RTX_LOGIN_PASSWORD` と `RTX_ADMIN_PASSWORD` の環境変数で渡す（未設定・空値の機器もあるため必須ではない）。inventory、Skill、チャット、Gitにパスワードを書かない。
4. SSHは事前に**手動で指紋確認済み**のホスト鍵を `known_hosts` に登録し、ホスト鍵検証を無効化しない。Telnetにはホスト鍵検証の概念はない。

### 文字コード（重要）

**対象RTX/FWXの標準コンソール文字コードは Shift_JIS（`shift_jis`）であり、UTF-8ではない。** SSH・Telnet・シリアルの全てで既定値を `shift_jis` とする。

- `ja.sjis`（Rev.14.01系以降）および `sjis`（旧Rev）は `shift_jis`。
- `en.ascii` / `ascii` はASCIIであり `shift_jis` でも互換デコード可能（明示的に `"ascii"` を指定してもよい）。
- `ja.utf8` / `utf8` に変更した機器は inventory の `encoding` を `"utf-8"` に変更する。
- コマンドキーワードの入力はASCII。出力に文字化け（`\ufffd`）が検出された場合、CLIエラー見落としを防ぐため `apply` / `save` は自動停止する。

### シリアル・ネットワーク接続の注意点

- シリアルは基本 `9600bps / 8N1 / Xon-Xoff`。ボーレート変更済みなら inventory の `baudrate` を修正。
- `show environment` の `Security Class`（例: `Security Class: 1, FORGET: ON, TELNET: OFF, SSH: OFF`）と `user attribute` 設定を確認する。セキュリティクラスやユーザー属性によっては Telnet/SSH からの `administrator` 移行や一部コマンドが拒否される場合があり、その場合はシリアルコンソールを使用する。
- 名前ありユーザー（`login user`）利用時は、機器側の `user attribute` 設定により `Username:` が先に出る場合と `Password:` が先に出て空Enterで `Username:` に切り替わる場合がある（`rtx_agent.py` は両方に対応）。

## Yamaha CLI の基本概念（必読）

**Cisco IOSのCLI操作をそのまま適用してはいけない。** コマンド作成前に `references/yamaha-cli.md` を必ず読むこと。

1. `enable` / `configure terminal` / `interface GigabitEthernet...` / `end` / `write memory` は使わない。ヤマハは一般ユーザー `>` から `administrator` で管理者 `#` に移り、設定コマンドを直接入力する。
2. **設定コマンドは入力直後に稼働状態へ反映される。** `save` は不揮発性メモリへの永続化であり `commit` ではない。`save` 前でも通信断を起こし得る。
3. `pp select N` / `tunnel select N` は後続コマンドの対象コンテキスト切替。**必ず末尾に `pp select none` / `tunnel select none` を入れて解除する**（未解除の計画は `rtx_agent.py` が拒否する）。`no pp select` / `no tunnel select` は選択解除であり設定削除ではない。
4. `no <設定コマンド>` は設定削除・初期値復帰。Ciscoと同一視せず個別書式を確認する。
5. `show config` は初期値を全展開するものではなく、秘密情報を含み得る。必ず `backup` サブコマンドで `0600` ファイルに保存し、生の内容をチャットへ貼らない。
6. 未保存の変更がある状態で `#` を抜けると `Save new configuration ? (Y/N)`（日本語: `新しい設定をセーブしますか? (Y/N)`）が表示される。`rtx_agent.py` は終了時に自動で `N` を返し、無断保存しない。

## 動作原則

- **必ず read-only 操作（`probe` → `read` → `backup`）から始める。**
- 変更手順は **バックアップ → 構文・適用モデル確認 → 変更計画（`plan`） → 利用者の明示承認 → 適用（`apply`） → 実機検証（`read`） → 別途保存承認 → 保存（`save`）** の順を厳守する。
- `read` で管理者権限が必要な `show` コマンド（`show config list` や `security class 2` 以上の `show log` 等）を実行する場合は `--admin` を付ける。
- `plan` は `# Reporting Date:` 等のタイムスタンプを除外した正規化ハッシュで変更前後の差分・ドリフトを検証する。`plan` 作成後に実機設定が変わった場合は再計画する。
- 破壊的変更（管理用IP、経路、フィルタ、NAT、VPN、認証、コンソール設定等）は `high_risk: true` と判定される。事前にシリアル復旧経路を確認し承認を得た場合のみ `--serial-recovery-confirmed` を付与する。
- 一括コマンド適用は**非トランザクション**。途中でCLIエラーが出たら即停止し、一部適用済みの前提で実機を再調査する。安易に再実行しない。
- 本ツールは自動ロールバックを実装していない。`save` しなくても running-config には変更が残る。

## 実行ワークフロー（コマンド例）

実行前に `.venv/bin/python` が存在するか確認し、あれば `.venv/bin/python`、なければ `python3` を使用する。以下は `yamaha-rtx-agent-skill/` ディレクトリ直下で実行する例。

```bash
# 環境変数でパスワードを渡す（未設定機器は空可）
# export RTX_LOGIN_PASSWORD='...'
# export RTX_ADMIN_PASSWORD='...'

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

# 1. 機種・Rev・Security Class・文字化け有無を確認
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial probe

# 2. 状態確認（show系のみ。管理者権限が必要なら --admin）
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial read 'show ip route'
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial read --admin 'show config list'

# 3. 変更前バックアップ（0600保存。中身をチャットに貼らない）
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial backup --output private/before.cfg

# 4. 変更計画を作成（masked_commands と plan_id が出力される）
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial plan --commands examples/change.txt --output private/change.plan.json
```

`apply` の前に、AIエージェントは `plan` の出力にある `masked_commands`、`high_risk`、`risk_reasons`、復旧手順を利用者に提示し、**利用者が `plan_id` を明示承認した場合のみ**次へ進む。

```bash
# 5. 設定適用（running-config のみ更新、未保存）
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial apply --plan private/change.plan.json --approve <plan_id>
# high_risk=true の場合のみ、シリアル復旧を確認した上で末尾に --serial-recovery-confirmed を追加
```

`apply` は `APPLIED_NOT_SAVED`、マスク済み差分 `diff`、および `.receipt.json` を返す。`read` コマンド等で疎通・設定反映を検証し、**利用者が保存を別途承認した場合のみ** `save` を実行する。

```bash
# 6. 不揮発性メモリへ保存
$PY scripts/rtx_agent.py --inventory inventory.json --device rtx830-serial save --receipt private/change.plan.receipt.json --approve <plan_id>
```

## 操作制限と公式資料

- AIエージェントが利用者の承認を自ら代行してはならない（ただしテスト環境等で利用者から明示的な一括検証指示がある場合はその範囲で実施し、結果を報告する）。
- `save`、`restart`、`cold start`、`load`、`erase`、`clear`、`delete`、`administrator`、`login`、`user attribute`、`sshd` は `plan` への直接投入を禁止している。
- 秘密情報の外部送信・チャットへの生コンフィグ貼り付けは禁止。
- 公式資料:
  - 全機種検索: https://www.rtpro.yamaha.co.jp/
  - 現行RTX共通: https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/
  - FWX120: https://www.rtpro.yamaha.co.jp/RT/manual/fwx120/
  - SSH認証対応表: https://www.rtpro.yamaha.co.jp/RT/docs/sshd/
  - コンソール仕様: https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/howtouse/console_howtouse.html
  - `no` コマンド仕様: https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/cmdref/cmdref_nocmd.html
  - ロールバックタイマー: https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/operation/rollback_timer.html
