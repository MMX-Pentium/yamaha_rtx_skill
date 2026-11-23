# YAMAHA RTX / FWX120 CLI リファレンス — AIエージェント必読

本書はAIエージェントが YAMAHA RTX810以降 / FWX120 を誤操作なく扱うための実践リファレンス。細かな引数や対応可否は必ず**機種とRevごと**に公式資料を確認すること。

## 1. 接続方式・ログイン挙動・文字コード

- **SSH** (`transport: "ssh"`, port 22, Netmiko `yamaha`): 通信暗号化。`known_hosts` へ事前に指紋登録する。RTX810/FWX120は公開鍵認証非対応。
- **Telnet** (`transport: "telnet"`, port 23, Netmiko `yamaha_telnet`, CRLF改行): Skill側の禁止なし。**平文通信**（認証情報・設定が暗号化されない）ため管理ネットワーク内で慎重に扱う。
- **Serial** (`transport: "serial"`, 9600bps/8N1/XON-XOFF): ネットワーク切断時の復旧経路。

### 文字コード
- 標準は **Shift_JIS (`shift_jis`)**。`ja.sjis`（Rev.14.01以降）と `sjis`（旧Rev）はどちらも Shift_JIS。
- `en.ascii` / `ascii` は英語ASCII表示（`shift_jis` でも互換デコード可能）。
- `ja.utf8` / `utf8` に変更した機器では `inventory.json` の `encoding` を `"utf-8"` に設定する。
- コマンドキーワードは常にASCIIで送信する。

### ログインと権限の注意点
- `user attribute` で匿名ログイン無効（`connection=off`）かつ `login user <name>` 設定済みの機器は、接続直後に `Username:` を要求する。
- 匿名ログイン有効な機器は `Password:` を先に出し、空Enter送信で `Username:` 入力に切り替わる。
- `show environment` に表示される `Security Class`（例: `Security Class: 1, FORGET: ON, TELNET: OFF, SSH: OFF`）と `user attribute` により、Telnet/SSHからの `administrator` 移行可否やコマンド権限が制御される。

## 2. Cisco IOS との違い（絶対に混同しないこと）

| 観点 | Cisco IOS | YAMAHA RTX / FWX |
| --- | --- | --- |
| 一般モード | `>` | `>` |
| 管理者昇格 | `enable` | `administrator`（管理パスワード入力） |
| 管理者モード | `#` | `#` |
| 設定モード | `configure terminal` | **不要**。`#` から直接設定コマンドを入力 |
| IF指定 | `interface Gi0/0` | `ip lan1 address ...` 等のフラット書式。PP/Tunnel は `pp select N` / `tunnel select N` |
| 設定反映 | 即時反映 | 即時反映（`save` 前でも通信断を起こし得る） |
| 設定保存 | `write memory` | `save`（不揮発性メモリ `CONFIG0` 等へ保存） |
| 設定確認 | `show running-config` | `show config`（秘密を含むため `backup` を使う） |
| 設定削除 | `no <cmd>` | `no <cmd>`（YAMAHA固有書式を必ず確認） |
| 管理者終了 | `disable` / `exit` | `exit`（未保存変更があると `(Y/N)` 保存確認が出る） |

## 3. よく使う read-only（`show`）コマンド一覧

`rtx_agent.py ... read '<command>'`（権限エラー時は `--admin` を付与）で使用できる代表的な確認コマンド:

```text
show environment                  # 機種・Rev・CPU/メモリ・稼働時間・Security Class
show status lan1                  # LAN1リンク状態・IP・送受信パケット統計
show status lan2                  # LAN2状態（搭載機種）
show ip route                     # ルーティングテーブル
show arp                          # ARPテーブル
show status dhcp                  # DHCPリースの払い出し状況
show nat descriptor address       # NAT変換テーブル・状態
show ip connection                # セッション一覧
show log                          # システムログ（長いためページングが発生し得る）
show status pp 1                  # PP 1 の状態
show status tunnel 1              # Tunnel 1 の状態
show config list                  # 保存済みコンフィグ一覧（--admin 推奨）
```

> **注意**: `show config` は `read` では禁止されている。必ず `backup --output private/<file>.cfg` を使用すること。
> **注意**: 既定 `console columns 80` のため、長い行は80桁で折り返される。

## 4. 代表的な設定コマンド書式（RTX共通系）

適用前に必ず対象機種・Revの公式リファレンスと照合すること。

### 基本・管理
```text
description 1 Lab test router                   # 説明文（番号1〜5等、または description lan1 <text>）
no description 1                                # 説明文削除
console prompt RTX830-LAB                       # プロンプト変更（注意: high_risk）
console character ja.sjis                       # 文字コード変更（ja.sjis / ja.utf8 / en.ascii）
console lines infinity                          # ページング抑止（既定は24行）
```

### IP・静的経路・DNS・DHCP
```text
ip lan1 address 192.168.100.1/24                # LAN1 IPアドレス設定
ip route default gateway 192.168.100.254        # デフォルトルート
ip route 10.10.0.0/16 gateway 192.168.100.254   # 静的ルート
no ip route 10.10.0.0/16 gateway 192.168.100.254
dns server 8.8.8.8 1.1.1.1                      # DNSサーバ指定
dhcp service server                             # DHCPサーバ有効化
dhcp scope 1 192.168.100.2-192.168.100.191/24   # DHCPスコープ定義
dhcp scope lease type 1 bind-only               # リース方式変更
```

### パケットフィルタ（順序と適用に注意）
```text
# 書式: ip filter <番号> <pass|reject|restrict|...> <送信元> <宛先> <プロトコル> <送信元ポート> <宛先ポート>
ip filter 1000 pass * * tcp * 443
ip filter 1099 reject * * * * *
# インタフェースへ適用（未定義番号や順序誤りで即通信断になるため要注意）
ip lan1 secure filter in 1000 1099
ip lan1 secure filter out 1000 1099
```

### PP / Tunnel コンテキスト（必ず `none` で解除）
`pp select N` / `tunnel select N` は後続コマンドの対象を切り替える状態操作。プロンプトも `pp1#` や `tunnel1#` に変化する。

```text
pp select 1
pp auth accept pap chap
pp select none

tunnel select 1
tunnel encapsulation ipsec
tunnel select none
```

- `no pp select` は `pp select none` と同義（選択解除であり PP 1 の設定削除ではない）。
- `rtx_agent.py` は `pp select` / `tunnel select` が未解除の plan をエラーとして拒否する。

## 5. 自動化で遭遇しやすいCLI応答と対処

- **ページング**: `---more---`（英語）または `--- つづく ---`（日本語）。`rtx_agent.py` はシリアル側で自動応答・除去し、Netmiko側では `console lines infinity` をセッション開始時に実行する。
- **未保存終了プロンプト**: 設定変更後に `exit` すると `Save new configuration ? (Y/N)` が出る。`rtx_agent.py` は切断時に必ず `N` を返す。
- **セッション単位のコンソール設定**: `console character` / `console lines` / `console columns` 等のコンソール系設定は、未保存のままセッションを終了（`N` 応答）すると元の設定に戻る場合がある。別セッションで `save` を行う `apply` → `save` 分離フローでは差分エラーになり得るため注意する。
- **タイムスタンプ行**: `show config` の `# Reporting Date: ...` は毎秒変化する。`rtx_agent.py` はハッシュ比較時にこの行を除外する。
- **エラーメッセージ**: 英語モードでは `Error: Illegal command name` / `Error: Too few arguments` 等、日本語モードでは `エラー: コマンド名が不正です` 等。エラー検出時は `apply` が即中断するため、エラー内容を読んで構文を修正し、実機状態を再確認してから再計画する。

## 6. 参照資料（YAMAHA公式）

- https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/
- https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/howtouse/console_howtouse.html
- https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/setup/console_character.html
- https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/cmdref/cmdref_nocmd.html
- https://www.rtpro.yamaha.co.jp/RT/manual/rt-common/howtouse/keyoperation.html
- https://www.rtpro.yamaha.co.jp/RT/manual/fwx120/
- https://ktbyers.github.io/netmiko/docs/netmiko/yamaha/index.html
