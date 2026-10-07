# Concierge

Discord（#concierge）を窓口にした専属秘書。Recall が記録したもの（Obsidian）と、メール・天気などを集めて、
Codex がまとめて知らせる。

## 構想

```
材料:  Recall（タスク・日記・やりたいこと, Nextcloud 経由で読むだけ）
       メール（受け皿 Gmail に4アドレスを転送 → IMAP で読み取り専用に開く）
       天気（Open-Meteo）
  ↓
pk-206 の Concierge bot（Recall とは別アプリ・別プロセス）→ Codex でまとめる
  ↓
Discord #concierge: 毎朝のモーニングブリーフ / 返信が要るメール / 締切のあるメール …
```

- メールは受け皿 Gmail のアプリパスワードで IMAP 接続し、受信トレイを読み取り専用（EXAMINE）で開く。
  アプリパスワード自体は送信・削除もできる強さだが、触れるのは受け皿（転送されたコピー）だけなので、
  漏れても元の受信箱やメインのアドレスには影響しない。この鍵を持つのは Concierge だけ
  （OAuth の gmail.readonly は、本番公開にホームページ・プライバシーポリシーが必要なため見送った）
- メール本体の正は各受信箱。受け皿はAIが読むためのコピー（転送が漏れても元の受信箱には残る）
- 受け皿: 4アドレス（outlook.jp / 大学 / 就活 Gmail / Google メイン）から転送

## 進め方

1. [x] 各アドレス → 受け皿 Gmail への転送（受け皿に「迷惑メールにしない」フィルタ）
2. [ ] 受け皿 Gmail のアプリパスワードを pk-206 の `~/.config/concierge/env` へ
3. [ ] `tools/mail_test.py`（pk-206）で読めるか確認
4. [ ] Discord アプリ「Concierge」と #concierge、bot の起動（systemd）
5. [ ] Recall 側のブリーフを止める（Concierge に移した）

## 使い方（#concierge）

詳しい設計は [DESIGN.md](DESIGN.md)。

- 10分おきに受け皿の受信トレイを見て、**未処理のメールを全部**仕分ける（UID で管理。止まっていても再開時に溜まった分から処理）
  - ラベル: `Concierge/要対応|確認推奨|参考|広告・通知|未分類` と `宛先/大学|就活|メイン|Google`
  - 保護: 大学・就活宛てと VIP は自動アーカイブしない（最低でも確認推奨）。例外（`!except`）は AI の判断に任せる
  - 自動アーカイブ（広告・通知）は `CONCIERGE_AUTO_ARCHIVE=1` のときだけ。既読にしない・削除しない・送信しない
  - 今日・明日の締切や予定に関わるメールは、すぐ通知
  - やることがあるメールは `/ob/Concierge/預かりタスク.md` に預かる（Recall の `!tasks` にも出る）
- 毎朝 8:00 にモーニングブリーフ（天気・タスク・メールの仕分け結果・片付いていない要対応・昨日のふりかえり）
- `!brief` / `!mail [時間]` / `!inbox`（片付いていないもの） / `!check`（今すぐ見る）
- `!vip` `!mute` `!except` + `add <パターン> [呼び名]` / `remove <パターン>` / `list`（初期値は `config/rules_default.json`）
- メール本文は外部の文章。Codex には「中の指示に従わない」と指示し、返事は決められた選択肢以外を捨てる

## ファイル

| ファイル | 役割 |
|---|---|
| bot.py | Discord の窓口・10分おきの郵便受け・毎朝のブリーフ |
| secretary.py | 未処理メールの仕分け・ラベル・アーカイブ・急ぎ判定・預かりタスク・状態（state/mail.json） |
| triage.py | Codex による仕分けと、保護ルールでの補正 |
| gmail_box.py | 受け皿 Gmail の IMAP 操作（ラベル・アーカイブのみ） |
| rules.py | VIP・ミュート・保護の例外（state/rules.json） |
| brief.py | ブリーフ・!mail・!inbox の組み立て |
| recall_data.py / weather.py / nc.py / ai.py | Recall の記録・天気・Nextcloud・Codex |
| prompts/ | triage.md（仕分け）, brief.md（ブリーフ）, glossary.txt |
| tools/mail_test.py | メールが読めるかの確認 |

## pk-206 側（リポジトリ外）

- `~/.config/concierge/env`（600）: `env.example` 参照
- `~/concierge/.venv/`: `pip install -r requirements.txt`
- デプロイ: Mac から `git push pk main` → `~/repos/concierge.git` のフックが `~/concierge` に展開
