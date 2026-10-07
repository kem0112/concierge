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

1. [x] 各アドレス → 受け皿 Gmail への転送
2. [ ] 受け皿 Gmail のアプリパスワードを pk-206 の `~/.config/concierge/env` へ
3. [ ] `tools/mail_test.py`（pk-206）で読めるか確認
4. [ ] Discord アプリ「Concierge」と #concierge
5. [ ] モーニングブリーフを Recall から移し、メールの要約を足す

## pk-206 側（リポジトリ外）

- `~/.config/concierge/env`（600）: `GMAIL_USER`, `GMAIL_APP_PASSWORD`（以降 Discord のトークンなども）
- デプロイ: Mac から `git push pk main` → `~/repos/concierge.git` のフックが `~/concierge` に展開
